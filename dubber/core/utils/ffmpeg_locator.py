"""Locate FFmpeg and FFprobe binaries: resources/ first, then Homebrew ffmpeg-full (macOS), then PATH."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def no_window() -> dict:
    """On Windows, hide the console window spawned by subprocess calls."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def _project_root() -> Path:
    """Walk up from this file to find the project root (has pyproject.toml).
    When running as a PyInstaller bundle, return the directory containing the .exe.
    """
    if getattr(sys, 'frozen', False):
        # sys._MEIPASS is the _internal/ folder where PyInstaller extracts datas
        return Path(sys._MEIPASS)
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path(__file__).resolve().parents[3]


def _platform_exe(name: str) -> str:
    return f"{name}.exe" if sys.platform == "win32" else name


# Homebrew's plain `ffmpeg` formula is built without libass (no `ass` filter → can't burn
# subtitles). `ffmpeg-full` has it but is keg-only (not on PATH), so look for it explicitly.
_HOMEBREW_FULL_DIRS = [
    Path("/opt/homebrew/opt/ffmpeg-full/bin"),  # Apple Silicon
    Path("/usr/local/opt/ffmpeg-full/bin"),     # Intel
]


def _search_homebrew_full(name: str) -> Path | None:
    if sys.platform != "darwin":
        return None
    for d in _HOMEBREW_FULL_DIRS:
        c = d / name
        if c.is_file():
            return c
    return None


def _search_bundled(name: str) -> Path | None:
    """Search resources/ directory for a bundled binary."""
    root = _project_root()
    plat = "win" if sys.platform == "win32" else "mac"
    candidates = [
        root / "resources" / _platform_exe(name),
        root / "resources" / plat / _platform_exe(name),
        root / "resources" / "ffmpeg-mac" / "bin" / name,  # self-contained (build_exe_mac.sh)
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def find_ffmpeg() -> Path:
    """Return path to ffmpeg: resources/ → Homebrew ffmpeg-full → PATH."""
    bundled = _search_bundled("ffmpeg") or _search_homebrew_full("ffmpeg")
    if bundled:
        return bundled
    found = shutil.which("ffmpeg")
    if found:
        return Path(found)
    raise FileNotFoundError(
        "FFmpeg not found. Install FFmpeg (https://ffmpeg.org/download.html) "
        "and add to PATH, or place ffmpeg[.exe] in resources/."
    )


def find_ffprobe() -> Path:
    """Return path to ffprobe: resources/ → Homebrew ffmpeg-full → PATH."""
    bundled = _search_bundled("ffprobe") or _search_homebrew_full("ffprobe")
    if bundled:
        return bundled
    found = shutil.which("ffprobe")
    if found:
        return Path(found)
    raise FileNotFoundError(
        "FFprobe not found. Install FFmpeg (includes ffprobe) "
        "and add to PATH, or place ffprobe[.exe] in resources/."
    )
