"""Tests for TimingFitter — no real TTS or ffmpeg calls."""
from __future__ import annotations

import asyncio
import wave
from pathlib import Path

import numpy as np
import pytest

from dubber.core.audio.fit import FittingResult, TimingFitter, _wav_duration, MAX_ATEMPO
from dubber.core.tts.base import TTSEngine, TTSResult


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_wav(path: Path, duration: float, sample_rate: int = 24000) -> Path:
    n = int(duration * sample_rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(np.zeros(n, dtype=np.int16).tobytes())
    return path


class FakeTTSEngine(TTSEngine):
    """TTS engine that creates WAV files without any network or ffmpeg calls."""

    def __init__(self, tts_dir: Path, duration_map: dict[str, float] = None):
        self._tts_dir = tts_dir
        self._duration_map = duration_map or {}

    async def synthesize_one(
        self, text: str, segment_id: int, rate: str | None = None
    ) -> TTSResult:
        key = f"{text}_{rate or '+0%'}"
        duration = self._duration_map.get(key, len(text) / 15.0)
        fname = f"seg{segment_id}_{abs(hash(key)) % 10000}.wav"
        p = self._tts_dir / fname
        make_wav(p, duration)
        return TTSResult(segment_id=segment_id, wav_path=p, duration=duration)


class FakeFfmpegAtempo:
    """Makes atempo calls write a WAV at reduced duration without running ffmpeg."""

    def __init__(self, tts_dir: Path):
        self._tts_dir = tts_dir
        self.calls: list[tuple[Path, Path, float]] = []

    def apply(self, ffmpeg, src: Path, dst: Path, factor: float) -> None:
        self.calls.append((src, dst, factor))
        src_dur = _wav_duration(src)
        make_wav(dst, src_dur / factor)


def make_fitter(
    tmp_path: Path,
    engine: TTSEngine | None = None,
    atempo_hook: FakeFfmpegAtempo | None = None,
) -> TimingFitter:
    speed_dir = tmp_path / "speed"
    fitter = TimingFitter(
        tts_engine=engine or FakeTTSEngine(tmp_path),
        ffmpeg=Path("ffmpeg"),
        speed_dir=speed_dir,
    )
    if atempo_hook:
        import dubber.core.audio.fit as fit_mod
        fit_mod._apply_atempo = atempo_hook.apply  # type: ignore[attr-defined]
    return fitter


def seg(id_: int, start: float, end: float, vi_text: str = "text") -> dict:
    return {"id": id_, "start": start, "end": end, "vi_text": vi_text}


def tts(id_: int, wav_path: Path, duration: float) -> TTSResult:
    return TTSResult(segment_id=id_, wav_path=wav_path, duration=duration)


# ── Step 1: fits perfectly ────────────────────────────────────────────────────

def test_fits_exactly(tmp_path):
    engine = FakeTTSEngine(tmp_path)
    fitter = make_fitter(tmp_path, engine)
    wav = make_wav(tmp_path / "a.wav", duration=1.5)
    results = fitter.fit(
        tts_results=[tts(0, wav, 1.5)],
        translate_data=[seg(0, 0.0, 2.0)],
        video_duration=10.0,
    )
    assert results[0].status == "fits"
    assert results[0].speed == pytest.approx(1.0)
    assert results[0].final_start == pytest.approx(0.0)


def test_fits_zero_duration_tts(tmp_path):
    engine = FakeTTSEngine(tmp_path)
    fitter = make_fitter(tmp_path, engine)
    wav = make_wav(tmp_path / "a.wav", duration=0.5)
    results = fitter.fit(
        tts_results=[tts(0, wav, 0.5)],
        translate_data=[seg(0, 0.0, 1.0)],
        video_duration=10.0,
    )
    assert results[0].status == "fits"


# ── Step 2: overflow ≤8% ──────────────────────────────────────────────────────

def test_overflow_small(tmp_path):
    engine = FakeTTSEngine(tmp_path)
    fitter = make_fitter(tmp_path, engine)
    # slot=2.0s, tts=2.1s (5% overflow), silence gap to next=2.0s
    wav = make_wav(tmp_path / "a.wav", duration=2.1)
    results = fitter.fit(
        tts_results=[tts(0, wav, 2.1)],
        translate_data=[seg(0, 0.0, 2.0), seg(1, 5.0, 6.0)],  # big gap
        video_duration=10.0,
    )
    assert results[0].status == "overflow"


def test_overflow_exceeds_8pct_triggers_speedup(tmp_path):
    """10% overflow should trigger step 3 (not overflow)."""
    engine = FakeTTSEngine(tmp_path, duration_map={
        "text_+5%": 1.5,
        "text_+0%": 2.2,
    })
    atempo = FakeFfmpegAtempo(tmp_path)
    fitter = make_fitter(tmp_path, engine, atempo)
    wav = make_wav(tmp_path / "a.wav", duration=2.2)
    results = fitter.fit(
        tts_results=[tts(0, wav, 2.2)],
        translate_data=[seg(0, 0.0, 2.0), seg(1, 4.0, 5.0)],
        video_duration=10.0,
    )
    assert results[0].status in ("sped_up", "pushed")


# ── Step 3: speed up ──────────────────────────────────────────────────────────

def test_speedup_via_atempo(tmp_path):
    """When rate re-synthesis doesn't fit, atempo should produce sped_up."""
    # slot=2.0s, tts=2.5s → needs 1.25x speedup
    # rate re-synthesis still returns 2.5s, atempo returns 2.0s → fits
    engine = FakeTTSEngine(tmp_path, duration_map={
        "text_+13%": 2.5,  # rate re-synthesis doesn't help
        "text_+0%": 2.5,
    })
    atempo = FakeFfmpegAtempo(tmp_path)
    fitter = make_fitter(tmp_path, engine, atempo)
    wav = make_wav(tmp_path / "a.wav", duration=2.5)
    results = fitter.fit(
        tts_results=[tts(0, wav, 2.5)],
        translate_data=[seg(0, 0.0, 2.0), seg(1, 5.0, 6.0)],
        video_duration=10.0,
    )
    assert results[0].status in ("sped_up", "pushed")
    if results[0].status == "sped_up":
        assert results[0].speed > 1.0


# ── Step 5: pushed ────────────────────────────────────────────────────────────

def test_pushed_shifts_next_segment(tmp_path):
    """Segment that overruns even after atempo → next segment start is delayed."""
    engine = FakeTTSEngine(tmp_path)
    atempo = FakeFfmpegAtempo(tmp_path)
    fitter = make_fitter(tmp_path, engine, atempo)
    # slot=1.0s, tts=4.0s — way too long to speed up within 1.3×
    wav0 = make_wav(tmp_path / "a.wav", duration=4.0)
    wav1 = make_wav(tmp_path / "b.wav", duration=0.5)
    results = fitter.fit(
        tts_results=[tts(0, wav0, 4.0), tts(1, wav1, 0.5)],
        translate_data=[
            seg(0, 0.0, 1.0, "long text"),
            seg(1, 2.0, 3.0, "short"),
        ],
        video_duration=10.0,
    )
    seg0 = next(r for r in results if r.id == 0)
    assert seg0.status in ("pushed", "sped_up")


# ── Multiple segments ─────────────────────────────────────────────────────────

def test_multiple_segments_all_fit(tmp_path):
    engine = FakeTTSEngine(tmp_path)
    fitter = make_fitter(tmp_path, engine)
    translate_data = [seg(i, i * 3.0, i * 3.0 + 2.0) for i in range(4)]
    tts_results = [
        tts(i, make_wav(tmp_path / f"{i}.wav", 1.5), 1.5)
        for i in range(4)
    ]
    results = fitter.fit(tts_results, translate_data, video_duration=20.0)
    assert len(results) == 4
    assert all(r.status == "fits" for r in results)


def test_missing_tts_skipped(tmp_path):
    engine = FakeTTSEngine(tmp_path)
    fitter = make_fitter(tmp_path, engine)
    wav = make_wav(tmp_path / "a.wav", 1.0)
    # Only TTS for segment 0; segment 1 has no TTS
    results = fitter.fit(
        tts_results=[tts(0, wav, 1.0)],
        translate_data=[seg(0, 0.0, 2.0), seg(1, 3.0, 4.0)],
        video_duration=10.0,
    )
    assert len(results) == 1
    assert results[0].id == 0


# ── Shortener callback (step 4) ───────────────────────────────────────────────

def test_shortener_called_when_too_long(tmp_path):
    """If TTS overruns and shortener is given, it should be called."""
    shortener_calls = []

    def shortener(text: str, max_chars: int) -> str:
        shortener_calls.append((text, max_chars))
        return text[:max_chars]

    engine = FakeTTSEngine(tmp_path, duration_map={
        "long_text_+0%": 1.0,
        "long_+0%": 0.8,
    })
    atempo = FakeFfmpegAtempo(tmp_path)
    speed_dir = tmp_path / "speed"
    fitter = TimingFitter(
        tts_engine=engine,
        ffmpeg=Path("ffmpeg"),
        speed_dir=speed_dir,
        shortener=shortener,
    )
    import dubber.core.audio.fit as fit_mod
    fit_mod._apply_atempo = atempo.apply  # type: ignore[attr-defined]

    # slot=0.5s, tts=3.0s — very long
    wav = make_wav(tmp_path / "a.wav", 3.0)
    fitter.fit(
        tts_results=[tts(0, wav, 3.0)],
        translate_data=[seg(0, 0.0, 0.5, "long_text")],
        video_duration=5.0,
    )
    assert len(shortener_calls) > 0


# ── _wav_duration ─────────────────────────────────────────────────────────────

def test_wav_duration_accuracy(tmp_path):
    p = tmp_path / "t.wav"
    make_wav(p, duration=3.75)
    assert _wav_duration(p) == pytest.approx(3.75, rel=1e-3)


# ── Engines without a speed control (VieNeu v3 Turbo) ─────────────────────────

class NoRateEngine(FakeTTSEngine):
    supports_rate = False

    def __init__(self, tts_dir: Path):
        super().__init__(tts_dir)
        self.rates: list = []

    async def synthesize_one(self, text, segment_id, rate=None):
        self.rates.append(rate)
        return await super().synthesize_one(text, segment_id, rate)


def test_no_rate_engine_skips_resynthesis_and_uses_atempo(tmp_path, monkeypatch):
    import dubber.core.audio.fit as fit_mod
    engine = NoRateEngine(tmp_path)
    atempo = FakeFfmpegAtempo(tmp_path)
    monkeypatch.setattr(fit_mod, "_apply_atempo", atempo.apply)
    fitter = TimingFitter(tts_engine=engine, ffmpeg=Path("ffmpeg"), speed_dir=tmp_path / "speed")
    wav = make_wav(tmp_path / "a.wav", duration=3.0)   # slot + overflow = 2.5 s → 1.2×
    results = fitter.fit(
        tts_results=[tts(0, wav, 3.0)],
        translate_data=[seg(0, 0.0, 2.0), seg(1, 5.0, 6.0)],
        video_duration=10.0,
    )
    assert engine.rates == []            # never asked to re-synthesize faster
    assert len(atempo.calls) == 1
    assert results[0].status == "sped_up"
