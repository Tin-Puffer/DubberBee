"""Subtitle fonts bundled with the app (dubber/assets/fonts, see dubber/assets/FONTS.md)."""
from __future__ import annotations

import os
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
    ("Fz Poppins", "FZ Poppins"),
]
DEFAULT_FONT = "Arial"
DEFAULT_FONT_SIZE = 48   # px on a 1080p frame — scaled to the video height
MIN_FONT_SIZE, MAX_FONT_SIZE = 20, 120

# Bundled file per family (Arial comes from the OS)
_FONT_FILES = {
    "Be Vietnam Pro": "BeVietnamPro-Bold.ttf",
    "Roboto": "Roboto-Bold.ttf",
    "Montserrat": "Montserrat-Bold.ttf",
    "Nunito": "Nunito-Bold.ttf",
    "Lexend": "Lexend-Bold.ttf",
    "Noto Sans": "NotoSans-Bold.ttf",
    "Fz Poppins": "FZPoppins-Bold.ttf",
}
_SYSTEM_ARIAL_BOLD = [
    Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "arialbd.ttf",
    Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    Path("/Library/Fonts/Arial Bold.ttf"),
    Path("/usr/share/fonts/truetype/msttcorefonts/Arial_Bold.ttf"),
]


def fonts_dir() -> Path:
    """Folder of bundled .ttf files; works from source and inside the PyInstaller bundle
    (the spec ships it at dubber/assets/fonts next to the package)."""
    return Path(__file__).resolve().parents[2] / "assets" / "fonts"


# Font of the app's own interface (Poppins with Vietnamese glyphs). Its Bold sits in
# fonts_dir() with the subtitle fonts; the lighter weights live in ui_fonts_dir() so
# libass (which loads every file of fonts_dir) only ever sees the Bold.
UI_FONT = "Fz Poppins"


def ui_fonts_dir() -> Path:
    """Folder of the interface-only weights (Regular / Medium / SemiBold)."""
    return Path(__file__).resolve().parents[2] / "assets" / "ui-fonts"


def font_file(family: str) -> "Path | None":
    """The .ttf libass will use for this family (bold), for measuring text; None if unknown."""
    if family in _FONT_FILES:
        path = fonts_dir() / _FONT_FILES[family]
        return path if path.is_file() else None
    if family == "Arial":
        return next((p for p in _SYSTEM_ARIAL_BOLD if p.is_file()), None)
    return None


def scaled_font_px(font_size: int, video_h: int) -> int:
    """Font size in ASS units for a video of this height (PlayResY = video height)."""
    return max(16, round(font_size * video_h / 1080))
