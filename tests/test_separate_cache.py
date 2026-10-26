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
    monkeypatch.setattr(sep, "_separate_mdxnet", lambda a, d, v, i, cb, ov: (calls.append(1), _fake_stems(v, i)))
    sep.separate_tracks(tmp_path / "a.wav", out)
    assert calls == [1]
    assert not (out / ".ffmpeg_fallback").exists()


def test_real_cache_is_reused(tmp_path, monkeypatch):
    """Stems from before the overlap was recorded were made at the default 0.25."""
    out = tmp_path / "separated"
    out.mkdir()
    _fake_stems(out / "vocals.wav", out / "instrumental.wav")
    monkeypatch.setattr(sep, "_mdxnet_available", lambda: True)
    monkeypatch.setattr(sep, "_separate_mdxnet", lambda *a: (_ for _ in ()).throw(AssertionError("re-ran")))
    sep.separate_tracks(tmp_path / "a.wav", out, overlap=sep.LEGACY_OVERLAP)


def test_stems_are_redone_only_for_another_overlap(tmp_path, monkeypatch):
    out = tmp_path / "separated"
    used = []

    def fake_mdx(a, d, v, i, cb, overlap):
        used.append(overlap)
        _fake_stems(v, i)
    monkeypatch.setattr(sep, "_mdxnet_available", lambda: True)
    monkeypatch.setattr(sep, "_separate_mdxnet", fake_mdx)
    for overlap in (0.5, 0.5, 0.75, 0.75, 0.5):
        sep.separate_tracks(tmp_path / "a.wav", out, overlap=overlap)
    assert used == [0.5, 0.75, 0.5]


def test_overlap_reaches_audio_separator(tmp_path, monkeypatch):
    import sys
    import types
    seen = {}

    class FakeSeparator:
        def __init__(self, **kw):
            seen.update(kw.get("mdx_params", {}), chunk_duration=kw.get("chunk_duration"))
        def load_model(self, name): pass
        def separate(self, path):
            _fake_stems(tmp_path / "x_(Vocals)_m.wav", tmp_path / "x_(Instrumental)_m.wav")
            return ["x_(Vocals)_m.wav", "x_(Instrumental)_m.wav"]
    mod = types.ModuleType("audio_separator.separator")
    mod.Separator = FakeSeparator
    monkeypatch.setitem(sys.modules, "audio_separator", types.ModuleType("audio_separator"))
    monkeypatch.setitem(sys.modules, "audio_separator.separator", mod)
    import os
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))   # restored after the test
    monkeypatch.setattr(sep, "_find_ffmpeg", lambda: Path("/usr/bin/ffmpeg"))
    monkeypatch.setattr(sep, "_uvr_model_dir", lambda: tmp_path / "models")
    monkeypatch.setattr(sep, "_mdxnet_available", lambda: True)
    monkeypatch.setattr(sep, "SEPARATE_IN_CHILD", False)   # the fake lives in this process
    sep.separate_tracks(tmp_path / "a.wav", tmp_path, overlap=0.75)
    assert seen["overlap"] == 0.75 and seen["segment_size"] == 256
    assert seen["chunk_duration"] == sep.CHUNK_S   # long videos would overflow RAM


def test_fallback_marks_cache(tmp_path, monkeypatch):
    out = tmp_path / "separated"

    def no_mdx(*a):
        raise ImportError("no audio_separator")

    monkeypatch.setattr(sep, "_separate_mdxnet", no_mdx)
    monkeypatch.setattr(sep, "_separate_ffmpeg_fallback", lambda a, v, i: _fake_stems(v, i))
    sep.separate_tracks(tmp_path / "a.wav", out)
    assert (out / ".ffmpeg_fallback").exists()


def test_prefetch_and_run_never_separate_the_same_job_at_once(tmp_path, monkeypatch):
    """A queue prefetch holds the job dir's lock; the video's own run waits, then reuses."""
    import threading
    import time
    import dubber.core.audio.separate as sep
    import dubber.core.pipeline as pl
    import dubber.core.utils.paths as paths

    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    job = tmp_path / "job"
    monkeypatch.setattr(paths, "job_dir_for", lambda p: job)
    monkeypatch.setattr(sep, "mdxnet_available", lambda: True)
    monkeypatch.setattr(pl.AudioExtractor, "extract_audio_stereo",
                        lambda self, v, out: out.write_bytes(b"wav") or out)
    active, overlaps, calls = [0], [], []

    def fake_mdx(audio, out_dir, vocals, inst, cb=None, overlap=None):
        active[0] += 1
        overlaps.append(active[0])
        calls.append(out_dir)
        time.sleep(0.2)
        vocals.write_bytes(b"v"); inst.write_bytes(b"i")
        active[0] -= 1
    monkeypatch.setattr(sep, "_separate_mdxnet", fake_mdx)

    t = threading.Thread(target=pl.prefetch_stems, args=(video, "ffmpeg", "ffprobe"))
    t.start()
    time.sleep(0.05)                                 # prefetch now holds the lock
    with sep.separation_lock(job):                   # what the queued run does first
        vocals, inst = pl.Pipeline("ffmpeg", "ffprobe", job).stems(video)
    t.join()
    assert len(calls) == 1 and max(overlaps) == 1    # separated once, never concurrently
    assert vocals.read_bytes() == b"v" and inst.read_bytes() == b"i"


def test_separation_lock_is_per_job_dir(tmp_path):
    from dubber.core.audio.separate import separation_lock
    assert separation_lock(tmp_path / "a") is separation_lock(tmp_path / "a")
    assert separation_lock(tmp_path / "a") is not separation_lock(tmp_path / "b")


def test_cached_stems_matches_what_separate_tracks_reuses(tmp_path, monkeypatch):
    out = tmp_path / "separated"
    assert sep.cached_stems(out) is None
    monkeypatch.setattr(sep, "_mdxnet_available", lambda: True)
    monkeypatch.setattr(sep, "_separate_mdxnet", lambda a, d, v, i, cb, ov: _fake_stems(v, i))
    stems = sep.separate_tracks(tmp_path / "a.wav", out, overlap=0.5)
    assert sep.cached_stems(out, 0.5) == stems
    assert sep.cached_stems(out, 0.75) is None


def test_stems_drop_the_stereo_audio_and_skip_it_when_cached(tmp_path, monkeypatch):
    """audio_stereo.wav is only the separator's input: not kept, not re-extracted."""
    import dubber.core.pipeline as pl
    extracted = []

    def extract(self, v, out):
        extracted.append(out)
        out.write_bytes(b"wav")
        return out
    monkeypatch.setattr(pl.AudioExtractor, "extract_audio_stereo", extract)
    monkeypatch.setattr(sep, "_mdxnet_available", lambda: True)
    monkeypatch.setattr(sep, "_separate_mdxnet", lambda a, d, v, i, cb, ov: _fake_stems(v, i))
    job = tmp_path / "job"
    job.mkdir()
    p = pl.Pipeline("ffmpeg", "ffprobe", job)
    p.stems(tmp_path / "v.mp4")
    assert len(extracted) == 1 and not (job / "audio_stereo.wav").exists()
    p.stems(tmp_path / "v.mp4")
    assert len(extracted) == 1


def _child_ready() -> bool:
    return sep._mdxnet_available()


def test_child_process_is_stopped_by_a_cancel(tmp_path):
    """The separation runs in a child process; a raising progress callback ends it."""
    import pytest
    if not _child_ready():
        pytest.skip("audio-separator not installed")

    class Cancelled(Exception):
        pass

    def cb(pct):
        raise Cancelled
    import multiprocessing
    with pytest.raises(Cancelled):
        sep._separate_mdxnet(tmp_path / "missing.wav", tmp_path, tmp_path / "v.wav",
                             tmp_path / "i.wav", cb)
    assert not [c for c in multiprocessing.active_children() if c.name == "dubber-separate"]


def test_child_process_error_comes_back(tmp_path):
    import pytest
    if not _child_ready() or not (sep._uvr_model_dir() / sep.MODEL_NAME).is_file():
        pytest.skip("audio-separator / MDX-Net model not installed")   # never downloads
    (tmp_path / "bad.wav").write_bytes(b"not a wav")
    with pytest.raises(RuntimeError):
        sep._separate_mdxnet(tmp_path / "bad.wav", tmp_path, tmp_path / "v.wav",
                             tmp_path / "i.wav", None)
