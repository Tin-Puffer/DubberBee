"""Tests for dubber.core.utils.ffmpeg_locator."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import dubber.core.utils.ffmpeg_locator as loc
from dubber.core.utils.ffmpeg_locator import (
    _platform_exe,
    find_ffmpeg,
    find_ffprobe,
)


@pytest.fixture(autouse=True)
def _no_homebrew_full(monkeypatch):
    """Keep tests independent of whether this machine has ffmpeg-full installed."""
    monkeypatch.setattr(loc, "_HOMEBREW_FULL_DIRS", [])


class TestHomebrewFull:
    def test_preferred_over_path_on_mac(self, tmp_path, monkeypatch):
        full = tmp_path / "ffmpeg"
        full.touch()
        monkeypatch.setattr(loc, "_HOMEBREW_FULL_DIRS", [tmp_path])
        monkeypatch.setattr(loc, "_search_bundled", lambda _: None)
        monkeypatch.setattr(loc.shutil, "which", lambda _: "/usr/bin/ffmpeg")
        monkeypatch.setattr(sys, "platform", "darwin")
        assert find_ffmpeg() == full

    def test_ignored_off_mac(self, tmp_path, monkeypatch):
        (tmp_path / "ffmpeg").touch()
        monkeypatch.setattr(loc, "_HOMEBREW_FULL_DIRS", [tmp_path])
        monkeypatch.setattr(sys, "platform", "linux")
        assert loc._search_homebrew_full("ffmpeg") is None


class TestPlatformExe:
    def test_current_platform(self):
        result = _platform_exe("ffmpeg")
        if sys.platform == "win32":
            assert result == "ffmpeg.exe"
        else:
            assert result == "ffmpeg"

    def test_no_exe_on_non_windows(self):
        with patch.object(sys, "platform", "darwin"):
            assert _platform_exe("ffprobe") == "ffprobe"


class TestFindFfmpeg:
    def test_found_in_resources(self, tmp_path, monkeypatch):
        resources = tmp_path / "resources"
        resources.mkdir()
        fake = resources / _platform_exe("ffmpeg")
        fake.touch()

        monkeypatch.setattr(loc, "_project_root", lambda: tmp_path)
        result = find_ffmpeg()
        assert result == fake

    def test_found_in_path(self, tmp_path, monkeypatch):
        fake = tmp_path / _platform_exe("ffmpeg")
        fake.touch()
        monkeypatch.setattr(loc, "_search_bundled", lambda _: None)
        monkeypatch.setattr(loc.shutil, "which", lambda _: str(fake))

        result = find_ffmpeg()
        assert result == fake

    def test_bundled_takes_priority_over_path(self, tmp_path, monkeypatch):
        bundled = tmp_path / "resources" / _platform_exe("ffmpeg")
        bundled.parent.mkdir(parents=True)
        bundled.touch()

        path_fake = tmp_path / "bin" / _platform_exe("ffmpeg")
        path_fake.parent.mkdir()
        path_fake.touch()

        monkeypatch.setattr(loc, "_project_root", lambda: tmp_path)
        monkeypatch.setattr(loc.shutil, "which", lambda _: str(path_fake))

        result = find_ffmpeg()
        assert result == bundled

    def test_raises_when_not_found(self, monkeypatch):
        monkeypatch.setattr(loc, "_search_bundled", lambda _: None)
        monkeypatch.setattr(loc.shutil, "which", lambda _: None)

        with pytest.raises(FileNotFoundError, match="FFmpeg not found"):
            find_ffmpeg()


class TestFindFfprobe:
    def test_found_in_resources(self, tmp_path, monkeypatch):
        resources = tmp_path / "resources"
        resources.mkdir()
        fake = resources / _platform_exe("ffprobe")
        fake.touch()

        monkeypatch.setattr(loc, "_project_root", lambda: tmp_path)
        result = find_ffprobe()
        assert result == fake

    def test_raises_when_not_found(self, monkeypatch):
        monkeypatch.setattr(loc, "_search_bundled", lambda _: None)
        monkeypatch.setattr(loc.shutil, "which", lambda _: None)

        with pytest.raises(FileNotFoundError, match="FFprobe not found"):
            find_ffprobe()
