"""run_m1 feeds ASR the MDX-Net vocal stem only when that is safe."""
from __future__ import annotations

import wave
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import dubber.core.audio.separate as sep
from dubber.core.pipeline import Pipeline


def _wav(path: Path, seconds: float, sr: int = 16000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sr)
        wf.writeframes(b"\0\0" * int(seconds * sr))
    return path


@pytest.fixture
def pipe(tmp_path, monkeypatch):
    job = tmp_path / "job"
    job.mkdir()
    _wav(job / "audio.wav", 10.0)
    p = Pipeline(ffmpeg=Path("ffmpeg"), ffprobe=Path("ffprobe"), job_dir=job)
    p._extractor = MagicMock()
    monkeypatch.setattr(sep, "mdxnet_available", lambda: True)
    return p


def _fake_separation(monkeypatch, job: Path, vocals_s: float, method: str = "mdxnet",
                     calls: list | None = None):
    def separate_tracks(stereo, out_dir, progress_cb=None, overlap=None):
        if calls is not None:
            calls.append(progress_cb)
        out_dir.mkdir(parents=True, exist_ok=True)
        if method == "ffmpeg":
            (out_dir / ".ffmpeg_fallback").touch()
        return out_dir / "vocals.wav", out_dir / "instrumental.wav"
    monkeypatch.setattr(sep, "separate_tracks", separate_tracks)

    def fake_ffmpeg(cmd, **kw):   # the 16 kHz conversion of the vocal stem
        _wav(Path(cmd[-1]), vocals_s)
        return MagicMock(returncode=0)
    monkeypatch.setattr("subprocess.run", fake_ffmpeg)


def test_uses_vocal_stem(pipe, monkeypatch):
    calls = []
    _fake_separation(monkeypatch, pipe.job_dir, 10.02, calls=calls)
    cb = object()
    wav = pipe._vocals_for_asr(Path("in.mp4"), pipe.job_dir / "audio.wav", cb)
    assert wav.name == "audio_vocals_16k.wav"
    assert calls == [cb]   # GUI separate-progress callback reaches the separator


def test_ffmpeg_fallback_stems_are_not_used(pipe, monkeypatch):
    _fake_separation(monkeypatch, pipe.job_dir, 10.0, method="ffmpeg")
    wav = pipe._vocals_for_asr(Path("in.mp4"), pipe.job_dir / "audio.wav")
    assert wav.name == "audio.wav"


def test_length_mismatch_falls_back(pipe, monkeypatch):
    _fake_separation(monkeypatch, pipe.job_dir, 10.2)
    wav = pipe._vocals_for_asr(Path("in.mp4"), pipe.job_dir / "audio.wav")
    assert wav.name == "audio.wav"


def test_no_mdxnet_skips_separation(pipe, monkeypatch):
    monkeypatch.setattr(sep, "mdxnet_available", lambda: False)
    monkeypatch.setattr(sep, "separate_tracks",
                        lambda *a, **k: pytest.fail("should not separate"))
    wav = pipe._vocals_for_asr(Path("in.mp4"), pipe.job_dir / "audio.wav")
    assert wav.name == "audio.wav"


def test_separation_error_falls_back(pipe, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("onnx crashed")
    monkeypatch.setattr(sep, "separate_tracks", boom)
    wav = pipe._vocals_for_asr(Path("in.mp4"), pipe.job_dir / "audio.wav")
    assert wav.name == "audio.wav"


def test_separation_method_marker(tmp_path):
    assert sep.separation_method(tmp_path) == "mdxnet"
    (tmp_path / ".ffmpeg_fallback").touch()
    assert sep.separation_method(tmp_path) == "ffmpeg"
