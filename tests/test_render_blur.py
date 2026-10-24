"""Tests for the blur band in VideoRenderer.render_final's filter graph."""
from __future__ import annotations

from pathlib import Path

import dubber.core.video.render as render
from dubber.core.video.render import VideoRenderer, _enable_groups


def _filter_for(blur_band, tmp_path: Path, monkeypatch) -> str:
    captured = {}

    def fake_run(cmd, on_progress):
        captured["cmd"] = cmd
        Path(cmd[-1]).write_bytes(b"x")
        return ""

    monkeypatch.setattr(render, "_detect_hw_encoder", lambda _ff: "libx264")
    monkeypatch.setattr(render, "_run_ffmpeg_with_progress", fake_run)
    VideoRenderer(Path("ffmpeg")).render_final(
        tmp_path / "in.mp4", tmp_path / "dub.wav", tmp_path / "subs.ass",
        tmp_path / "out.mp4", blur_band=blur_band,
    )
    cmd = captured["cmd"]
    return cmd[cmd.index("-filter_complex") + 1]


def test_many_blur_boxes_use_single_overlay(tmp_path, monkeypatch):
    boxes = [(i * 3.0, i * 3.0 + 2.5, 420, 919, 1079, 148) for i in range(34)]
    fc = _filter_for(boxes, tmp_path, monkeypatch)
    assert fc.count("split") == 1
    assert fc.count("overlay") == 1
    assert fc.count("between(t,") == 34


def test_blur_covers_union_of_boxes(tmp_path, monkeypatch):
    boxes = [(0.0, 1.0, 524, 986, 871, 81), (1.0, 2.0, 420, 919, 1079, 148)]
    fc = _filter_for(boxes, tmp_path, monkeypatch)
    # x: 420 → max(524+871, 420+1079)=1499 → w=1079; y: 919 → max(1067, 1067) → h=148
    assert "crop=1079:148:420:919" in fc
    # touching intervals are merged into one term
    assert "overlay=420:919:enable='between(t,0.000,2.000)'" in fc


def test_no_blur_band_has_no_overlay(tmp_path, monkeypatch):
    fc = _filter_for(None, tmp_path, monkeypatch)
    assert "overlay" not in fc


def test_enable_groups_merge_touching_and_near_intervals():
    groups = _enable_groups([(0.0, 1.0), (1.2, 2.0), (5.0, 6.0), (5.5, 7.0)])
    assert groups == ["between(t,0.000,2.000)+between(t,5.000,7.000)"]


def test_enable_groups_cap_terms_per_expression():
    # 1000 separated subtitles (1h video) — FFmpeg rejects >100 terms in one expression
    groups = _enable_groups([(i * 3.0, i * 3.0 + 2.0) for i in range(1000)])
    assert len(groups) == 20
    assert all(g.count("between(") <= 50 for g in groups)
    assert sum(g.count("between(") for g in groups) == 1000


def test_many_subtitles_chain_overlays_of_one_blur(tmp_path, monkeypatch):
    boxes = [(i * 3.0, i * 3.0 + 2.0, 420, 919, 1079, 148) for i in range(127)]
    fc = _filter_for(boxes, tmp_path, monkeypatch)
    assert fc.count("boxblur") == 1          # blurred once…
    assert "split=3" in fc                   # …and reused by 3 overlays
    assert fc.count("overlay=") == 3
    for expr in fc.split("enable='")[1:]:
        assert expr.split("'")[0].count("between(") <= 50


def test_audio_mix_does_not_halve_volumes(tmp_path, monkeypatch):
    # amix defaults to normalize=1 (each input × 1/N) — sliders would show 2× the real level
    fc = _filter_for(None, tmp_path, monkeypatch)
    assert "amix=" in fc and "normalize=0" in fc


# ── audio tracks: with / without the Vietnamese voice ─────────────────────────

def _cmd_for(tmp_path, monkeypatch, **kw):
    captured = {}

    def fake_run(cmd, on_progress):
        captured["cmd"] = cmd
        Path(cmd[-1]).write_bytes(b"x")
        return ""
    monkeypatch.setattr(render, "_detect_hw_encoder", lambda _ff: "libx264")
    monkeypatch.setattr(render, "_run_ffmpeg_with_progress", fake_run)
    VideoRenderer(Path("ffmpeg")).render_final(
        tmp_path / "in.mp4", kw.pop("dub_wav", tmp_path / "dub.wav"), tmp_path / "subs.ass",
        tmp_path / "out.mp4", **kw)
    return captured["cmd"]


def _inputs(cmd):
    return [Path(cmd[i + 1]).name for i, a in enumerate(cmd) if a == "-i"]


def test_subtitles_only_mixes_just_the_background(tmp_path, monkeypatch):
    cmd = _cmd_for(tmp_path, monkeypatch, dub_wav=None, background_wav=tmp_path / "bg.wav",
                   orig_volume=0.8, logo=(tmp_path / "l.png", 0, 0, 10, 10, 1.0))
    assert _inputs(cmd) == ["in.mp4", "bg.wav", "l.png"]
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "[1:a]volume=0.8,loudnorm" in fc and "amix" not in fc
    assert "[2:v]scale=10:10" in fc                          # the logo is input 2 now
    assert "[aout]" in cmd and "-an" not in cmd


def test_subtitles_only_with_original_audio(tmp_path, monkeypatch):
    cmd = _cmd_for(tmp_path, monkeypatch, dub_wav=None, orig_volume=1.0)
    assert _inputs(cmd) == ["in.mp4"]
    assert "[0:a]volume=1.0,loudnorm" in cmd[cmd.index("-filter_complex") + 1]


def test_no_audio_at_all_gives_a_silent_video(tmp_path, monkeypatch):
    cmd = _cmd_for(tmp_path, monkeypatch, dub_wav=None, orig_volume=0.0)
    assert "-an" in cmd and "[aout]" not in cmd
    assert "loudnorm" not in cmd[cmd.index("-filter_complex") + 1]


def test_voice_and_background_are_mixed(tmp_path, monkeypatch):
    cmd = _cmd_for(tmp_path, monkeypatch, background_wav=tmp_path / "bg.wav", orig_volume=0.4)
    assert _inputs(cmd) == ["in.mp4", "dub.wav", "bg.wav"]
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "[2:a]volume=0.4[bg_a];[1:a]volume=1.0[dub_a]" in fc and "amix=inputs=2" in fc
