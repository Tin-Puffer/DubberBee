"""Track levels in dB: loudness measure (EBU R128 via FFmpeg) + gain to REF_LUFS + dB."""
from __future__ import annotations

import wave

import numpy as np
import pytest

from dubber.core.audio import levels

SR = 48000


def _sine(peak_db: float, seconds: float = 3.0) -> np.ndarray:
    t = np.arange(int(SR * seconds)) / SR
    x = (10 ** (peak_db / 20) * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
    return np.stack([x, x], axis=1)


def test_gain_brings_tracks_to_the_reference_then_applies_db():
    assert levels.gain_for(levels.REF_LUFS, 0) == pytest.approx(1.0)
    assert 20 * np.log10(levels.gain_for(-26.0, -14)) == pytest.approx(-4.0)
    assert 20 * np.log10(levels.gain_for(-50.0, 0)) == pytest.approx(levels.MAX_BOOST_DB)
    assert 20 * np.log10(levels.gain_for(None, -20)) == pytest.approx(-20)   # silent
    assert 20 * np.log10(levels.gain_for(-70.0, -20)) == pytest.approx(-20)


def test_measures_loudness_of_samples_and_files(tmp_path, monkeypatch):
    monkeypatch.setattr(levels, "_cache_path", lambda: tmp_path / "loudness.json")
    # a stereo 1 kHz sine: LUFS ≈ its peak level in dBFS
    assert levels.measure_array(_sine(-20), SR) == pytest.approx(-20, abs=1)
    assert levels.measure_array(np.zeros((SR, 2), np.float32), SR) is None
    path = tmp_path / "s.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2), w.setsampwidth(2), w.setframerate(SR)
        w.writeframes((_sine(-30) * 32767).astype("<i2").tobytes())
    assert levels.measure_file(path) == pytest.approx(-30, abs=1)
    assert (tmp_path / "loudness.json").exists()
    monkeypatch.setattr(levels, "_run", lambda *a: pytest.fail("not cached"))
    assert levels.measure_file(path) == pytest.approx(-30, abs=1)
