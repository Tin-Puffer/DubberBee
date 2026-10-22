"""Separation cache must not keep poor FFmpeg-fallback stems once MDX-Net is available."""
from __future__ import annotations

from pathlib import Path

import dubber.core.audio.separate as sep


def _fake_stems(vocals: Path, instr: Path) -> None:
    vocals.write_bytes(b"v"); instr.write_bytes(b"i")


def test_fallback_cache_redone_when_mdxnet_available(tmp_path, monkeypatch):
    out = tmp_path / "separated"
    out.mkdir()
    _fake_stems(out / "vocals.wav", out / "instrumental.wav")
    (out / ".ffmpeg_fallback").touch()
    calls = []
    monkeypatch.setattr(sep, "_mdxnet_available", lambda: True)
    monkeypatch.setattr(sep, "_separate_mdxnet", lambda a, d, v, i, cb: (calls.append(1), _fake_stems(v, i)))
    sep.separate_tracks(tmp_path / "a.wav", out)
    assert calls == [1]
    assert not (out / ".ffmpeg_fallback").exists()


def test_real_cache_is_reused(tmp_path, monkeypatch):
    out = tmp_path / "separated"
    out.mkdir()
    _fake_stems(out / "vocals.wav", out / "instrumental.wav")
    monkeypatch.setattr(sep, "_mdxnet_available", lambda: True)
    monkeypatch.setattr(sep, "_separate_mdxnet", lambda *a: (_ for _ in ()).throw(AssertionError("re-ran")))
    sep.separate_tracks(tmp_path / "a.wav", out)


def test_fallback_marks_cache(tmp_path, monkeypatch):
    out = tmp_path / "separated"

    def no_mdx(*a):
        raise ImportError("no audio_separator")

    monkeypatch.setattr(sep, "_separate_mdxnet", no_mdx)
    monkeypatch.setattr(sep, "_separate_ffmpeg_fallback", lambda a, v, i: _fake_stems(v, i))
    sep.separate_tracks(tmp_path / "a.wav", out)
    assert (out / ".ffmpeg_fallback").exists()
