"""Tests for EdgeTTSEngine — no network calls."""
from __future__ import annotations

import asyncio
import os
import wave
from pathlib import Path

import numpy as np
import pytest

from dubber.core.tts.base import tts_cache_key, TTSResult
from dubber.core.tts.edge import EdgeTTSEngine, _wav_duration


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_wav(path: Path, duration: float = 1.0, sample_rate: int = 24000) -> Path:
    """Write a silent WAV file of given duration."""
    n = int(duration * sample_rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(np.zeros(n, dtype=np.int16).tobytes())
    return path


def make_engine(tts_dir: Path, ffmpeg: Path = Path("ffmpeg")) -> EdgeTTSEngine:
    engine = EdgeTTSEngine(
        voice="vi-VN-HoaiMyNeural",
        rate="+0%",
        tts_dir=tts_dir,
        ffmpeg=ffmpeg,
        max_concurrent=2,
    )

    async def _mock_do_synthesize(text: str, rate: str, wav_path: Path) -> None:
        make_wav(wav_path, duration=len(text) / 15.0)

    engine._do_synthesize = _mock_do_synthesize
    return engine


# ── tts_cache_key ─────────────────────────────────────────────────────────────

def test_cache_key_deterministic():
    k1 = tts_cache_key("hello", "vi-VN-HoaiMyNeural", "+0%")
    k2 = tts_cache_key("hello", "vi-VN-HoaiMyNeural", "+0%")
    assert k1 == k2


def test_cache_key_differs_by_voice():
    k1 = tts_cache_key("hello", "vi-VN-HoaiMyNeural", "+0%")
    k2 = tts_cache_key("hello", "vi-VN-NamMinhNeural", "+0%")
    assert k1 != k2


def test_cache_key_differs_by_rate():
    k1 = tts_cache_key("hello", "vi-VN-HoaiMyNeural", "+0%")
    k2 = tts_cache_key("hello", "vi-VN-HoaiMyNeural", "+25%")
    assert k1 != k2


def test_cache_key_differs_by_engine():
    k1 = tts_cache_key("hello", "Hải Đăng", "+0%", engine="piper")
    k2 = tts_cache_key("hello", "Hải Đăng", "+0%", engine="vieneu")
    assert k1 != k2


def test_cache_key_length():
    k = tts_cache_key("text", "voice", "+10%")
    assert len(k) == 16


# ── synthesize_one (cache miss) ───────────────────────────────────────────────

def test_synthesize_creates_wav(tmp_path):
    engine = make_engine(tmp_path)
    result = asyncio.run(engine.synthesize_one("Xin chào", segment_id=0))
    assert isinstance(result, TTSResult)
    assert result.segment_id == 0
    assert result.wav_path.exists()
    assert result.wav_path.suffix == ".wav"
    assert result.duration > 0


def test_synthesize_duration_proportional_to_length(tmp_path):
    engine = make_engine(tmp_path)
    short = asyncio.run(engine.synthesize_one("A", segment_id=0))
    long_ = asyncio.run(engine.synthesize_one("A" * 30, segment_id=1))
    assert long_.duration > short.duration


# ── synthesize_one (cache hit) ────────────────────────────────────────────────

def test_synthesize_cache_hit_skips_call(tmp_path):
    engine = make_engine(tmp_path)
    call_count = [0]
    original = engine._do_synthesize

    async def counting(*a, **kw):
        call_count[0] += 1
        return await original(*a, **kw)

    engine._do_synthesize = counting

    asyncio.run(engine.synthesize_one("Xin chào", segment_id=0))
    asyncio.run(engine.synthesize_one("Xin chào", segment_id=0))
    assert call_count[0] == 1  # second call hits cache


# ── synthesize_one (rate override) ────────────────────────────────────────────

def test_synthesize_different_rate_different_file(tmp_path):
    engine = make_engine(tmp_path)
    r0 = asyncio.run(engine.synthesize_one("Xin chào", 0, rate="+0%"))
    r1 = asyncio.run(engine.synthesize_one("Xin chào", 0, rate="+25%"))
    assert r0.wav_path != r1.wav_path


# ── synthesize_one (retry on failure) ────────────────────────────────────────

def test_synthesize_retries_on_failure(tmp_path):
    engine = make_engine(tmp_path)
    attempts = [0]
    real_do = engine._do_synthesize

    async def flaky(text: str, rate: str, wav_path: Path) -> None:
        attempts[0] += 1
        if attempts[0] < 2:
            raise RuntimeError("network error")
        await real_do(text, rate, wav_path)

    engine._do_synthesize = flaky
    result = asyncio.run(engine.synthesize_one("Xin chào", 0))
    assert attempts[0] == 2
    assert result.wav_path.exists()


def test_synthesize_raises_after_max_retries(tmp_path):
    engine = make_engine(tmp_path)

    async def always_fail(text, rate, wav_path):
        raise RuntimeError("always fails")

    engine._do_synthesize = always_fail
    with pytest.raises(RuntimeError):
        asyncio.run(engine.synthesize_one("text", 0))


# ── synthesize_all ────────────────────────────────────────────────────────────

def test_synthesize_all_returns_all_results(tmp_path):
    engine = make_engine(tmp_path)
    items = [(i, f"đoạn {i}") for i in range(5)]
    results = asyncio.run(engine.synthesize_all(items))
    assert len(results) == 5
    ids = sorted(r.segment_id for r in results)
    assert ids == list(range(5))


def test_synthesize_all_no_duplicate_files(tmp_path):
    engine = make_engine(tmp_path)
    items = [(0, "text"), (1, "text")]  # same text → same cache file
    results = asyncio.run(engine.synthesize_all(items))
    # Both results valid even if they share the same wav_path
    assert all(r.wav_path.exists() for r in results)


# ── _wav_duration helper ──────────────────────────────────────────────────────

def test_wav_duration(tmp_path):
    p = tmp_path / "t.wav"
    make_wav(p, duration=2.5)
    assert _wav_duration(p) == pytest.approx(2.5, rel=1e-3)


# ── factory ───────────────────────────────────────────────────────────────────

def test_factory_picks_engine(tmp_path, monkeypatch):
    import dubber.core.tts.piper_tts as piper_mod
    import dubber.core.tts.vieneu_tts as vieneu_mod
    from dubber.core.tts.factory import make_tts_engine

    made = []
    monkeypatch.setattr(piper_mod, "PiperTTSEngine",
                        lambda model_path, tts_dir: made.append(("piper", model_path.name)) or "P")
    monkeypatch.setattr("dubber.core.asr.model_manager.find_piper_model",
                        lambda v: Path(f"{v or 'default'}.onnx"))
    monkeypatch.setattr(vieneu_mod, "VieNeuTTSEngine",
                        lambda voice, tts_dir: made.append(("vieneu", voice)) or "V")
    assert make_tts_engine("piper", "vi_VN-x", tmp_path) == "P"
    assert make_tts_engine("vieneu", "preset:Hải Đăng", tmp_path) == "V"
    assert made == [("piper", "vi_VN-x.onnx"), ("vieneu", "preset:Hải Đăng")]
    with pytest.raises(ValueError):
        make_tts_engine("edge", None, tmp_path)


def test_engine_flags():
    from dubber.core.tts.piper_tts import PiperTTSEngine
    from dubber.core.tts.vieneu_tts import VieNeuTTSEngine
    assert PiperTTSEngine.supports_rate and PiperTTSEngine.engine_id == "piper"
    assert not VieNeuTTSEngine.supports_rate and VieNeuTTSEngine.engine_id == "vieneu"


# ── VieNeu (SDK mocked) ───────────────────────────────────────────────────────

class _FakeVieneu:
    sample_rate = 48_000

    def __init__(self):
        self.calls = []

    def resolve_voice_name(self, name):
        return name if name in ("Hải Đăng", "Mai Anh") else None

    def infer(self, text, voice=None):
        self.calls.append((text, voice))
        return np.full(int(0.5 * self.sample_rate), 0.1, dtype=np.float32)


def test_vieneu_engine_writes_48k_wav_and_caches(tmp_path):
    import wave
    from dubber.core.tts.vieneu_tts import VieNeuTTSEngine
    sdk = _FakeVieneu()
    eng = VieNeuTTSEngine("preset:Mai Anh", tmp_path, tts=sdk)
    progress = []
    eng.on_progress = lambda d, t: progress.append((d, t))
    res = asyncio.run(eng.synthesize_all([(0, "Xin chào 你好"), (1, "Xin chào 你好")]))
    assert sdk.calls == [("Xin chào", "Mai Anh")]      # Chinese stripped, 2nd is a cache hit
    assert res[0].wav_path == res[1].wav_path
    assert res[0].duration == pytest.approx(0.5)
    with wave.open(str(res[0].wav_path)) as wf:
        assert wf.getframerate() == 48_000 and wf.getsampwidth() == 2
    assert progress == [(1, 2), (2, 2)]


def test_vieneu_unknown_voice(tmp_path):
    from dubber.core.tts.vieneu_tts import VieNeuTTSEngine
    with pytest.raises(ValueError):
        VieNeuTTSEngine("preset:Không Có", tmp_path, tts=_FakeVieneu())


def test_vieneu_cache_key_differs_from_piper_same_text(tmp_path):
    from dubber.core.tts.vieneu_tts import VieNeuTTSEngine
    eng = VieNeuTTSEngine("preset:Hải Đăng", tmp_path, tts=_FakeVieneu())
    r = asyncio.run(eng.synthesize_one("chào", 0))
    assert r.wav_path.stem == tts_cache_key("chào", "preset:Hải Đăng", "+0%", engine="vieneu")
    assert r.wav_path.stem != tts_cache_key("chào", "preset:Hải Đăng", "+0%", engine="piper")


@pytest.mark.slow
@pytest.mark.skipif(not os.environ.get("DUBBER_SLOW_TESTS"), reason="set DUBBER_SLOW_TESTS=1")
def test_vieneu_real(tmp_path):
    pytest.importorskip("vieneu")
    from dubber.core.asr.model_manager import find_vieneu_model_dir
    from dubber.core.tts.vieneu_tts import VieNeuTTSEngine
    if find_vieneu_model_dir() is None:
        pytest.skip("VieNeu models not downloaded")
    r = asyncio.run(VieNeuTTSEngine(None, tmp_path).synthesize_one("Xin chào các bạn.", 0))
    assert r.duration > 0.5
