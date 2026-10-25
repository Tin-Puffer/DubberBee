"""Mix preview (🎧 Nghe thử): render's mixing rules, job-cache windows, live volumes."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import numpy as np
import pytest

import dubber.core.audio.preview_mix as pm

FR = 1000   # frames of the synthetic tracks


def _ff():
    from dubber.core.utils.ffmpeg_locator import find_ffmpeg
    return str(find_ffmpeg())


def _tracks(**names) -> pm.PreviewTracks:
    """Constant-level tracks: name=value → (FR, 2) filled with value."""
    tr = pm.PreviewTracks(start=0.0, frames=FR)
    for name, value in names.items():
        tr.arrays[name] = np.full((FR, 2), value, np.float32)
    return tr


ALL = dict(orig=0.1, music=0.1, cn=0.1, dub=0.1, bgm=0.1)
BASE = dict(vi_on=True, vi=1.0, music_on=True, music=0.4, cn_on=True, cn=0.2,
            bgm_on=False, bgm=0.3)


def _on(g):
    return {k: round(v, 3) for k, v in g.items() if v}


# ── mixing rules (same as run_m5 / render_final) ──────────────────────────────

@pytest.mark.parametrize("change, expected", [
    ({}, {"music": 0.4, "cn": 0.2, "dub": 1.0}),                    # different → stems
    ({"cn": 0.4}, {"orig": 0.4, "dub": 1.0}),                       # same → original track
    ({"cn_on": False}, {"music": 0.4, "dub": 1.0}),                 # only music
    ({"music_on": False}, {"cn": 0.2, "dub": 1.0}),                 # only Chinese voice
    ({"cn_on": False, "music": 0.0}, {"music": 0.2, "dub": 1.0}),   # on at 0 % → 20 %
    ({"music_on": False, "cn_on": False}, {"dub": 1.0}),
    ({"vi_on": False}, {"music": 0.4, "cn": 0.2}),
    ({"vi": 1.5, "bgm_on": True}, {"music": 0.4, "cn": 0.2, "dub": 1.5, "bgm": 0.3}),
])
def test_track_gains_follow_the_render_rules(change, expected):
    assert _on(pm.track_gains(_tracks(**ALL), {**BASE, **change})) == expected


def test_without_stems_the_original_track_stands_in():
    tr = _tracks(orig=0.1, dub=0.1)                    # separation failed
    assert _on(pm.track_gains(tr, BASE)) == {"orig": 0.4, "dub": 1.0}
    assert _on(pm.track_gains(tr, {**BASE, "music_on": False})) == {"orig": 0.2, "dub": 1.0}


def test_missing_voice_or_music_track_gets_no_gain():
    g = pm.track_gains(_tracks(orig=0.1, music=0.1, cn=0.1), {**BASE, "bgm_on": True})
    assert not g["dub"] and not g["bgm"]


def test_mix_sums_the_tracks_at_their_gains():
    tr = _tracks(music=0.5, cn=0.25, dub=0.1)
    out = pm.mix(tr, {"music": 0.4, "cn": 0.2, "dub": 1.0}, 10, 20)
    assert out.shape == (10, 2) and np.allclose(out, 0.5 * 0.4 + 0.25 * 0.2 + 0.1)


def test_loudness_gain_brings_the_rest_up_when_a_track_goes_down():
    tr = _tracks(music=0.2, cn=0.2, dub=0.05)
    loud = pm.loudness_gain(tr, {"music": 1.0, "cn": 0.5, "dub": 1.0})
    quiet = pm.loudness_gain(tr, {"music": 0.1, "cn": 0.5, "dub": 1.0})
    assert quiet > loud                                # like the export's loudnorm
    assert pm.loudness_gain(_tracks(dub=0.0), {"dub": 1.0}) == 1.0   # silence: unchanged


def test_window_bounds_follow_length_and_trim():
    assert pm.window_bounds(120.0) == (0.0, 100.0)
    assert pm.window_bounds(120.0, (30.0, 90.0)) == (30.0, 70.0)
    assert pm.window_bounds(12.0) == (0.0, 0.0)        # shorter than a window


# ── windows cut from the job cache ────────────────────────────────────────────

def _tone_after(path: Path, at: float, dur: float = 40.0) -> Path:
    """A wav that is silent until *at* s, then a tone."""
    subprocess.run([_ff(), "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"aevalsrc='if(gte(t,{at}),0.5*sin(2*PI*440*t),0)':d={dur}:s=48000",
                    str(path)], check=True)
    return path


@pytest.fixture
def video(tmp_path, monkeypatch):
    import dubber.core.utils.paths as paths
    monkeypatch.setattr(paths, "_app_data_dir", lambda: tmp_path / "appdata")
    monkeypatch.setattr(pm, "preview_dir", lambda: tmp_path / "preview")
    v = tmp_path / "clip.mkv"
    subprocess.run([_ff(), "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=black:s=64x64:d=40", "-f", "lavfi", "-i",
                    "sine=f=220:d=40:sample_rate=48000", "-shortest", "-c:v", "mpeg4",
                    "-c:a", "pcm_s16le", str(v)], check=True)
    return v


def _active_from(a: np.ndarray) -> float:
    """Seconds into the window where sound starts."""
    idx = np.flatnonzero(np.abs(a[:, 0]) > 0.05)
    return idx[0] / pm.SR if len(idx) else -1.0


def _fill_job(job: Path, at: float) -> None:
    sep = job / "separated"
    sep.mkdir()
    for name in ("vocals.wav", "instrumental.wav", "dub.wav"):
        _tone_after(job / name if name == "dub.wav" else sep / name, at)


def test_processed_video_plays_its_cached_stems_and_dub(video, monkeypatch):
    from dubber.core.utils.paths import prepare_job_dir
    job = prepare_job_dir(video)
    _fill_job(job, at=10.0)
    monkeypatch.setattr(pm, "_separate_window", lambda *a: pytest.fail("separated again"))
    monkeypatch.setattr(pm, "_read_lines", lambda *a, **k: pytest.fail("read a sample"))
    tr = pm.prepare(video, 5.0)
    assert tr.stems_source == "cache" and tr.dub_source == "dub"
    assert set(tr.arrays) == {"orig", "music", "cn", "dub"}
    assert all(a.shape == (round(pm.WINDOW_S * pm.SR), 2) for a in tr.arrays.values())
    for name in ("dub", "cn", "music"):
        assert abs(_active_from(tr.arrays[name]) - 5.0) < 0.05     # 10 s − window at 5 s


def test_trimmed_video_maps_the_window_onto_the_trimmed_cache(video, monkeypatch):
    from dubber.core.utils.paths import prepare_job_dir
    job = prepare_job_dir(video, trim=(8.0, None))      # cache timeline starts at 8 s
    _fill_job(job, at=10.0)                             # i.e. 18 s on the video
    monkeypatch.setattr(pm, "_separate_window", lambda *a: pytest.fail("separated again"))
    tr = pm.prepare(video, 12.0, trim=(8.0, None))
    assert tr.dub_source == "dub"
    assert abs(_active_from(tr.arrays["dub"]) - 6.0) < 0.05
    # another trim than the cache's → not this cache
    calls = []
    monkeypatch.setattr(pm, "_separate_window", lambda *a: calls.append(1) or (
        np.zeros((tr.frames, 2), np.float32),) * 2 + ("mdxnet",))
    monkeypatch.setattr(pm, "_read_lines", lambda *a, **k: np.zeros((tr.frames, 2), np.float32))
    other = pm.prepare(video, 12.0, trim=(5.0, None))
    assert calls == [1] and other.dub_source == "sample"


def test_unprocessed_video_reads_the_translated_lines_or_a_sample(video, monkeypatch):
    from dubber.core.utils.paths import prepare_job_dir
    seen = []
    monkeypatch.setattr(pm, "_separate_window", lambda *a: (
        np.zeros((960000, 2), np.float32),) * 2 + ("mdxnet",))
    monkeypatch.setattr(pm, "_read_lines", lambda lines, engine, voice, frames, repeat:
                        seen.append((lines, engine, voice, repeat)) or
                        np.zeros((frames, 2), np.float32))
    tr = pm.prepare(video, 0.0, engine="vieneu", voice="preset:x")
    assert tr.dub_source == "sample" and seen[-1] == ([(0.5, pm.SAMPLE_TEXT)], "vieneu",
                                                       "preset:x", True)
    job = prepare_job_dir(video)                       # translated, no voice yet
    (job / "translate.json").write_text(
        '[{"start": 3.0, "vi_text": "Câu một."}, {"start": 30.0, "vi_text": "Ngoài đoạn."},'
        ' {"start": 15.0, "vi_text": "Câu hai."}]', encoding="utf-8")
    tr = pm.prepare(video, 2.0)
    assert tr.dub_source == "lines" and seen[-1][0] == [(1.0, "Câu một."), (13.0, "Câu hai.")]
    assert seen[-1][3] is False


def test_read_lines_places_clips_without_overlap(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import dubber.core.tts.factory as factory
    clip = _tone_after(tmp_path / "clip.wav", 0.0, dur=2.0)

    class Engine:
        async def synthesize_all(self, items):
            return [SimpleNamespace(segment_id=i, wav_path=clip, duration=2.0)
                    for i, _ in items]
    monkeypatch.setattr(factory, "make_tts_engine", lambda *a: Engine())
    monkeypatch.setattr(pm, "preview_dir", lambda: tmp_path)
    frames = 10 * pm.SR

    def loud(a, t):                                   # 20 ms around t seconds
        i = int(t * pm.SR)
        return float(np.sqrt(np.mean(a[i:i + 960, 0] ** 2))) > 0.05
    out = pm._read_lines([(1.0, "a"), (2.0, "b")], "piper", None, frames, repeat=False)
    assert not loud(out, 0.5) and loud(out, 1.1)
    assert loud(out, 4.9) and not loud(out, 5.2)      # b waits for a: 3 → 5 s
    rep = pm._read_lines([(0.5, "s")], "piper", None, frames, repeat=True)
    assert loud(rep, 1.0) and not loud(rep, 3.0) and loud(rep, 4.0)   # again after 1.2 s


# ── live playback stream ──────────────────────────────────────────────────────

@pytest.fixture
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _rms(b: bytes) -> float:
    a = np.frombuffer(b, "<i2").astype(np.float32) / 32767
    return float(np.sqrt(np.mean(a ** 2)))


def _noise(n, level, seed):
    return (np.random.default_rng(seed).standard_normal((n, 2)) * level).astype(np.float32)


def test_volume_changes_are_heard_at_once(qapp):
    from dubber.app.mix_preview import MixDevice
    n = pm.SR * 2
    tr = pm.PreviewTracks(start=0.0, frames=n, arrays={
        "music": _noise(n, 0.05, 1), "cn": _noise(n, 0.05, 2), "dub": _noise(n, 0.05, 3),
        "orig": _noise(n, 0.07, 4)})
    dev = MixDevice(tr, {**BASE, "music": 0.3})
    for _ in range(6):
        dev.render(4800)                                # settle the loudness gain
    before = dev.levels["Nhạc nền"]
    dev.set_volumes({**BASE, "music": 1.0})
    for _ in range(3):
        dev.render(4800)                                # ≤ 0.3 s later
    assert dev.levels["Nhạc nền"] > before + 0.05
    dev.set_volumes({**BASE, "vi_on": False, "music_on": False, "cn_on": False})
    for _ in range(3):
        out = dev.render(4800)
    assert _rms(out) == 0.0                             # everything off → silence


def test_stream_loops_the_window_and_never_clips(qapp):
    from dubber.app.mix_preview import MixDevice
    n = 3000
    tr = pm.PreviewTracks(start=0.0, frames=n, arrays={"dub": np.full((n, 2), 0.9, np.float32)})
    dev = MixDevice(tr, {**BASE, "vi": 2.0, "music_on": False, "cn_on": False})
    out = np.frombuffer(dev.render(n + 500), "<i2")
    assert len(out) == (n + 500) * 2 and dev.pos == 500
    assert np.abs(out).max() <= int(0.89 * 32767) + 1


def test_warns_when_the_background_covers_the_voice(qapp):
    from dubber.app.mix_preview import MixDevice
    n = pm.SR
    tr = pm.PreviewTracks(start=0.0, frames=n, arrays={
        "music": _noise(n, 0.2, 1), "cn": _noise(n, 0.01, 2), "dub": _noise(n, 0.05, 3)})
    dev = MixDevice(tr, {**BASE, "music": 1.0, "cn": 0.1})
    for _ in range(5):
        dev.render(4800)
    assert dev.covered
    dev.set_volumes({**BASE, "music": 0.05, "cn": 0.1})
    for _ in range(10):
        dev.render(4800)
    assert not dev.covered


def test_qt_reads_the_stream_through_readdata(qapp):
    from PySide6.QtCore import QIODevice
    from dubber.app.mix_preview import MixDevice
    tr = _tracks(dub=0.1)
    dev = MixDevice(tr, BASE)
    dev.open(QIODevice.ReadOnly)
    assert len(bytes(dev.read(400))) == 400


# ── the Audio tab ─────────────────────────────────────────────────────────────

def test_audio_tab_feeds_the_preview(qapp, tmp_path):
    import dubber.app.gui as gui
    panel = gui.OptionsPanel()
    mp = panel.mix_preview
    assert not mp.btn.isEnabled()                       # no video selected
    panel._sld_music.setValue(55)
    panel._chk_cn_voice.setChecked(False)
    panel._sld_vi.setValue(150)
    v = panel._preview_volumes()
    assert v["music"] == 0.55 and not v["cn_on"] and v["vi"] == 1.5
    assert v["bgm_on"] is False                         # no music file chosen
    panel.set_preview_source(tmp_path / "v.mp4", 125.0)
    assert mp.btn.isEnabled() and mp.pos.maximum() == 105
    ctx = panel._preview_context()
    assert ctx["path"] == tmp_path / "v.mp4" and ctx["engine"] == panel.tts_engine()
    assert ctx["trim"] == (0.0, None)
    panel.set_preview_source(None)
    assert not mp.btn.isEnabled()
