"""Subtitle fonts bundled with the app (dubber/assets/fonts, see dubber/assets/FONTS.md)."""
from __future__ import annotations

from pathlib import Path

# (ASS Fontname = family name inside the .ttf, label shown in the GUI)
SUBTITLE_FONTS: list[tuple[str, str]] = [
    ("Arial", "Arial (hệ thống)"),
    ("Be Vietnam Pro", "Be Vietnam Pro"),
    ("Roboto", "Roboto"),
    ("Montserrat", "Montserrat"),
    ("Nunito", "Nunito"),
    ("Lexend", "Lexend"),
    ("Noto Sans", "Noto Sans"),
]
DEFAULT_FONT = "Arial"
DEFAULT_FONT_SIZE = 48   # px on a 1080p frame — scaled to the video height
MIN_FONT_SIZE, MAX_FONT_SIZE = 20, 120


def fonts_dir() -> Path:
    """Folder of bundled .ttf files; works from source and inside the PyInstaller bundle
    (the spec ships it at dubber/assets/fonts next to the package)."""
    return Path(__file__).resolve().parents[2] / "assets" / "fonts"


def scaled_font_px(font_size: int, video_h: int) -> int:
    """Font size in ASS units for a video of this height (PlayResY = video height)."""
    return max(16, round(font_size * video_h / 1080))
