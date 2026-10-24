"""Tests for TimingFitter — no real TTS or ffmpeg calls."""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

import dubber.core.audio.fit as fit_mod
from dubber.core.audio.fit import (
    CLAUSE_GAP, MAX_GLOBAL_SPEED, MAX_LAG, MAX_SPEED, SENTENCE_GAP,
    TimingFitter, _wav_duration, trim_silence,
)
from dubber.core.tts.base import TTSEngine, TTSResult

SR = 22050


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_wav(path: Path, duration: float, sample_rate: int = SR,
             lead: float = 0.0, trail: float = 0.0) -> Path:
    """*duration* s of tone, with *lead* / *trail* s of silence around it."""
    t = np.arange(int(duration * sample_rate)) / sample_rate
    tone = 0.3 * np.sin(2 * np.pi * 220 * t)
    x = np.concatenate([np.zeros(int(lead * sample_rate)), tone,
                        np.zeros(int(trail * sample_rate))])
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes((x * 32767).astype(np.int16).tobytes())
    return path


def _rate_factor(rate: str | None) -> float:
    return 1 + float((rate or "+0%").strip("+%")) / 100


class FakeTTSEngine(TTSEngine):
    """Duration = len(text) / 15 s (or *durations[text]*); a rate makes it faster by
    *rate_effect* of what was asked (piper voices follow length_scale only partly)."""

    def __init__(self, tts_dir: Path, durations: dict | None = None, rate_effect: float = 1.0):
        self._tts_dir = tts_dir
        self._durations = durations or {}
        self._rate_effect = rate_effect
        self.calls: list[tuple[str, str | None]] = []

    async def synthesize_one(self, text, segment_id, rate=None):
        self.calls.append((text, rate))
        base = self._durations.get(text, len(text) / 15.0)
        dur = base / (1 + (_rate_factor(rate) - 1) * self._rate_effect)
        p = make_wav(self._tts_dir / f"seg{segment_id}_{abs(hash((text, rate))) % 10**8}.wav", dur)
        return TTSResult(segment_id=segment_id, wav_path=p, duration=dur)


class NoRateEngine(FakeTTSEngine):
    supports_rate = False


class FakeAtempo:
    """Stands in for ffmpeg atempo: writes the source's tone shortened by *factor*."""

    def __init__(self):
        self.calls: list[float] = []

    def __call__(self, ffmpeg, src: Path, dst: Path, factor: float) -> None:
        self.calls.append(factor)
        make_wav(dst, _wav_duration(src) / factor)


@pytest.fixture
def atempo(monkeypatch):
    fake = FakeAtempo()
    monkeypatch.setattr(fit_mod, "_apply_atempo", fake)
    return fake


def run(tmp_path, segs, durations, video=60.0, engine=None, shortener=None):
    """Fit lines given as (start, end, text) with TTS durations (seconds, no silence)."""
    engine = engine or FakeTTSEngine(tmp_path, dict(zip((s[2] for s in segs), durations)))
    # the pause after a line follows the Chinese punctuation
    data = [{"id": i, "start": s, "end": e, "vi_text": t,
             "src_text": "中文。" if t.endswith(".") else "中文，"}
            for i, (s, e, t) in enumerate(segs)]
    results = [TTSResult(i, make_wav(tmp_path / f"in{i}.wav", d, lead=0.1, trail=0.3), d + 0.4)
               for i, d in enumerate(durations)]
    fitter = TimingFitter(engine, Path("ffmpeg"), tmp_path / "speed", shortener=shortener)
    return fitter.fit(results, data, video)


def assert_no_overlap(fitted):
    for a, b in zip(fitted, fitted[1:]):
        assert b.final_start >= a.final_end - 1e-6, (a, b)


# ── Placement ─────────────────────────────────────────────────────────────────

def test_line_that_fits_starts_at_its_chinese_time(tmp_path, atempo):
    fitted = run(tmp_path, [(1.0, 4.0, "Một câu.")], [2.0])
    f = fitted[0]
    assert f.status == "fits" and f.speed == 1.0
    assert f.final_start == pytest.approx(1.0)
    # the engine's silence is trimmed: speech + EDGE_PAD on both sides
    assert f.tts_duration == pytest.approx(2.0 + 2 * fit_mod.EDGE_PAD, abs=0.03)


def test_dense_lines_never_overlap(tmp_path, atempo):
    """Every line much longer than its slot, no pauses in the Chinese."""
    segs = [(i * 2.0, i * 2.0 + 2.0, f"Câu số {i}.") for i in range(10)]
    fitted = run(tmp_path, segs, [3.0] * 10, video=40.0)
    assert len(fitted) == 10
    assert_no_overlap(fitted)
    assert all(f.speed <= MAX_SPEED + 1e-6 for f in fitted)


def test_pause_after_sentence_is_longer_than_after_clause(tmp_path, atempo):
    segs = [(0.0, 2.0, "Hết câu."), (2.0, 4.0, "chưa hết câu,"), (4.0, 6.0, "tiếp.")]
    fitted = run(tmp_path, segs, [2.0, 2.0, 2.0], video=20.0)
    gap1 = fitted[1].final_start - fitted[0].final_end
    gap2 = fitted[2].final_start - fitted[1].final_end
    assert gap1 == pytest.approx(SENTENCE_GAP, abs=0.02)
    assert gap2 == pytest.approx(CLAUSE_GAP, abs=0.02)


def test_voice_catches_up_at_the_speakers_pause(tmp_path, atempo):
    """A long line delays the next ones only until the Chinese speaker pauses."""
    segs = [(0.0, 1.0, "Câu rất dài."), (1.0, 2.0, "Câu kế."), (10.0, 11.0, "Sau quãng nghỉ.")]
    fitted = run(tmp_path, segs, [3.0, 0.8, 0.8], video=20.0)
    assert_no_overlap(fitted)
    assert fitted[1].status == "late"
    assert fitted[2].final_start == pytest.approx(10.0)
    assert fitted[2].status == "fits"


def test_lag_stays_within_limit_when_speed_up_is_enough(tmp_path, atempo):
    segs = [(i * 2.0, i * 2.0 + 2.0, f"Câu {i}.") for i in range(8)]
    fitted = run(tmp_path, segs, [2.0] * 8, video=30.0)
    assert_no_overlap(fitted)
    for f, (s, _, _) in zip(fitted, segs):
        assert f.final_start - s <= MAX_LAG + 0.05


def test_even_speed_is_the_lowest_that_fits(tmp_path, atempo):
    """Slightly too long everywhere → a gentle speed-up, not the maximum."""
    segs = [(i * 2.5, i * 2.5 + 2.5, f"Câu {i},") for i in range(6)]
    fitted = run(tmp_path, segs, [2.5] * 6, video=16.0)
    speeds = [f.speed for f in fitted]
    assert max(speeds) < MAX_GLOBAL_SPEED
    assert_no_overlap(fitted)


# ── Speed-up ──────────────────────────────────────────────────────────────────

def test_rate_engine_is_topped_up_with_atempo(tmp_path, atempo):
    """A voice that follows the rate only half-way still ends up at the planned speed."""
    segs = [(0.0, 2.0, "Câu dài một chút."), (2.0, 4.0, "Câu sau.")]
    engine = FakeTTSEngine(tmp_path, {"Câu dài một chút.": 4.0, "Câu sau.": 1.0},
                           rate_effect=0.5)
    fitted = run(tmp_path, segs, [4.0, 1.0], video=5.5, engine=engine)
    assert any(rate and rate != "+0%" for _, rate in engine.calls)
    assert atempo.calls and all(1.0 < a <= MAX_SPEED for a in atempo.calls)
    assert fitted[0].status in ("sped_up", "late")
    assert_no_overlap(fitted)


def test_no_rate_engine_uses_atempo_only(tmp_path, atempo):
    segs = [(0.0, 2.0, "Câu dài một chút."), (2.0, 4.0, "Câu sau.")]
    engine = NoRateEngine(tmp_path, {"Câu dài một chút.": 4.0, "Câu sau.": 1.0})
    fitted = run(tmp_path, segs, [4.0, 1.0], video=5.5, engine=engine)
    assert engine.calls == []          # never re-synthesized
    assert len(atempo.calls) >= 1
    assert fitted[0].speed > 1.0


# ── Shortening ────────────────────────────────────────────────────────────────

def test_shortener_gets_too_long_lines_and_its_text_is_spoken(tmp_path, atempo):
    calls = []

    def shortener(items):
        calls.append(items)
        return {it["id"]: "Ngắn." for it in items}

    segs = [(0.0, 1.0, "Một câu dịch dài dòng quá mức cần thiết."), (1.0, 2.0, "Câu kế,")]
    fitted = run(tmp_path, segs, [4.0, 0.5], video=3.0, shortener=shortener)
    item = calls[0][0]
    assert item["id"] == 0 and item["zh"] == "中文。" and item["max_chars"] < len(segs[0][2])
    assert fitted[0].vi_text == "Ngắn." and fitted[0].status == "shortened"
    assert_no_overlap(fitted)


def test_shortener_not_called_when_everything_fits(tmp_path, atempo):
    calls = []
    run(tmp_path, [(0.0, 3.0, "Vừa.")], [1.0], shortener=lambda items: calls.append(items))
    assert calls == []


def test_failing_shortener_keeps_the_job_going(tmp_path, atempo):
    def shortener(items):
        raise RuntimeError("Groq down")

    segs = [(0.0, 1.0, "Câu quá dài so với chỗ trống."), (1.0, 2.0, "Câu kế,")]
    fitted = run(tmp_path, segs, [4.0, 0.5], video=10.0, shortener=shortener)
    assert len(fitted) == 2 and fitted[0].vi_text == segs[0][2]
    assert_no_overlap(fitted)


# ── Audio helpers ─────────────────────────────────────────────────────────────

def test_trim_silence_keeps_speech_and_a_little_padding():
    t = np.arange(SR) / SR
    x = np.concatenate([np.zeros(SR // 5), 0.3 * np.sin(2 * np.pi * 220 * t),
                        np.zeros(SR // 2)]).astype(np.float32)
    out = trim_silence(x, SR)
    assert len(out) / SR == pytest.approx(1.0 + 2 * fit_mod.EDGE_PAD, abs=0.02)
    assert abs(out[0]) < 1e-3 and abs(out[-1]) < 1e-3     # faded edges, no click


def test_trim_silence_leaves_all_silent_audio_alone():
    x = np.zeros(SR, dtype=np.float32)
    assert len(trim_silence(x, SR)) == SR


def test_wav_duration_accuracy(tmp_path):
    p = make_wav(tmp_path / "t.wav", duration=3.75)
    assert _wav_duration(p) == pytest.approx(3.75, rel=1e-3)


def test_dub_wav_keeps_the_voices_sample_rate(tmp_path, atempo):
    """No resampling of piper's 22 050 Hz voice (nearest-neighbour resampling = crackle)."""
    from dubber.core.audio.mixer import build_dub_wav
    fitted = run(tmp_path, [(0.0, 2.0, "Câu.")], [1.0], video=3.0)
    out = build_dub_wav(fitted, 3.0, tmp_path / "dub.wav")
    with wave.open(str(out)) as wf:
        assert wf.getframerate() == SR
