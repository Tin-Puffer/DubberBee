"""Tests for source video info parsing and display."""
from __future__ import annotations

import os
import subprocess

import pytest

from dubber.core.video.info import VideoInfo, parse_ffprobe, probe_info


def _probe(video=None, audio=None, fmt=None):
    streams = []
    if video:
        streams.append({"codec_type": "video", **video})
    if audio:
        streams.append({"codec_type": "audio", **audio})
    return {"format": fmt or {"duration": "125.5", "size": "52428800", "bit_rate": "3342000"},
            "streams": streams}


def test_parse_basic_landscape():
    i = parse_ffprobe(_probe(
        {"codec_name": "h264", "width": 1920, "height": 1080, "avg_frame_rate": "30000/1001",
         "bit_rate": "3000000", "pix_fmt": "yuv420p"},
        {"codec_name": "aac", "sample_rate": "44100", "channels": 2, "bit_rate": "128000"}))
    assert (i.width, i.height, i.aspect) == (1920, 1080, "16:9")
    assert round(i.fps, 2) == 29.97
    assert (i.video_codec, i.video_kbps, i.bit_depth) == ("h264", 3000, 8)
    assert (i.audio_codec, i.sample_rate, i.channels, i.audio_kbps) == ("aac", 44100, 2, 128)
    assert (i.duration, i.size_bytes, i.bitrate_kbps) == (125.5, 52428800, 3342)


def test_rotated_phone_video_reports_display_size():
    i = parse_ffprobe(_probe({"codec_name": "hevc", "width": 1920, "height": 1080,
                              "avg_frame_rate": "30/1", "pix_fmt": "yuv420p10le",
                              "side_data_list": [{"rotation": -90}]}))
    assert (i.width, i.height, i.rotation, i.aspect, i.bit_depth) == (1080, 1920, -90, "9:16 (dọc)", 10)


def test_no_audio_and_odd_aspect():
    i = parse_ffprobe(_probe({"codec_name": "h264", "width": 1152, "height": 720, "avg_frame_rate": "0/0",
                              "r_frame_rate": "25/1"}))
    assert i.audio_codec == ""
    assert i.fps == 25.0                 # avg_frame_rate 0/0 → falls back to r_frame_rate
    assert i.aspect == "8:5"


def test_cover_art_stream_is_ignored():
    data = _probe({"codec_name": "mjpeg", "width": 300, "height": 300, "disposition": {"attached_pic": 1}})
    data["streams"].append({"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720,
                            "avg_frame_rate": "24/1"})
    assert parse_ffprobe(data).width == 1280


def test_probe_real_file(tmp_path):
    from dubber.core.utils.ffmpeg_locator import find_ffmpeg
    clip = tmp_path / "c.mp4"
    subprocess.run([str(find_ffmpeg()), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=25:d=2",
                    "-f", "lavfi", "-i", "sine=d=2:sample_rate=48000", "-shortest",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(clip)], check=True)
    i = probe_info(clip)
    assert (i.width, i.height, i.fps, i.audio_codec, i.sample_rate) == (640, 360, 25.0, "aac", 48000)
    assert 1.9 < i.duration < 2.2


# ── GUI ───────────────────────────────────────────────────────────────────────

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")


def test_info_panel_and_export_original_labels():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    import dubber.app.gui as g
    info = VideoInfo(width=1152, height=720, fps=29.97, duration=3723, size_bytes=52428800,
                     video_codec="hevc", pix_fmt="yuv420p", audio_codec="aac", sample_rate=44100, channels=2)
    p = g.VideoInfoPanel()
    p.show_info(info)
    assert p._val["res"].text() == "1152 × 720  (8:5)"
    assert p._val["fps"].text() == "29.97"
    assert p._val["dur"].text() == "1:02:03"
    assert p._val["audio"].text().startswith("AAC · 44.1 kHz · stereo")
    p.show_info(VideoInfo(width=640, height=360, fps=25))
    assert "Không có âm thanh" in p._val["audio"].text()

    e = g.ExportPanel()
    e.set_source(info)
    assert e._combo_res.itemText(0) == "Gốc (1152×720)"
    assert e._combo_fps.itemText(0) == "Gốc (29.97)"
    assert e.scale() is None and e.fps() is None   # labels only — values still mean "keep original"
