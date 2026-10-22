"""Tests for persistent settings and output path resolution."""
from __future__ import annotations

from pathlib import Path

from dubber.core.utils.settings import load_settings, resolve_output_path, save_settings


def test_load_defaults_when_missing(tmp_path):
    assert load_settings(tmp_path / "nope.json") == {"output_dir": None, "tts_engine": "piper", "voice": None,
                                       "workspace_dir": None, "logs_dir": None}


def test_save_then_load_roundtrip(tmp_path):
    f = tmp_path / "cfg" / "settings.json"
    save_settings({"output_dir": "/Users/x/Phim đã dub"}, f)
    assert load_settings(f)["output_dir"] == "/Users/x/Phim đã dub"


def test_corrupt_file_falls_back_to_defaults(tmp_path):
    f = tmp_path / "settings.json"
    f.write_text("{not json")
    assert load_settings(f) == {"output_dir": None, "tts_engine": "piper", "voice": None,
                                       "workspace_dir": None, "logs_dir": None}


def test_output_next_to_input_by_default(tmp_path):
    src = tmp_path / "clip 1.mp4"
    assert resolve_output_path(src, None) == tmp_path / "clip 1_vi.mp4"


def test_output_in_chosen_dir(tmp_path):
    out = tmp_path / "out"
    assert resolve_output_path(Path("/videos/a.mkv"), out) == out / "a_vi.mp4"


def test_output_never_overwrites(tmp_path):
    (tmp_path / "a_vi.mp4").touch()
    (tmp_path / "a_vi (2).mp4").touch()
    assert resolve_output_path(tmp_path / "a.mp4", tmp_path) == tmp_path / "a_vi (3).mp4"
