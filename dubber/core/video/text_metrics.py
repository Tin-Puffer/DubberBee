"""Measure subtitle text the way libass will draw it (for 1-line fitting and blur boxes)."""
from __future__ import annotations

import struct
from functools import lru_cache

from dubber.core.video.fonts import font_file

_FALLBACK_CHAR_W = 0.62   # average advance / font size when the font file isn't found
_REF_SIZE = 100           # measure at a fixed size, scale linearly


@lru_cache(maxsize=16)
def _font(family: str):
    path = font_file(family)
    if path is None:
        return None
    try:
        from PIL import ImageFont
        return ImageFont.truetype(str(path), _REF_SIZE)
    except Exception:
        return None


def _win_metrics(path) -> "tuple[int, int, int] | None":
    """(units_per_em, usWinAscent, usWinDescent) read straight from the sfnt tables."""
    try:
        data = path.read_bytes()
        tables = {}
        for i in range(struct.unpack(">H", data[4:6])[0]):
            tag, _, off, length = struct.unpack(">4sIII", data[12 + 16 * i: 28 + 16 * i])
            tables[tag] = data[off: off + length]
        upem = struct.unpack(">H", tables[b"head"][18:20])[0]
        win_ascent, win_descent = struct.unpack(">HH", tables[b"OS/2"][74:78])
        return upem, win_ascent, win_descent
    except Exception:
        return None


@lru_cache(maxsize=16)
def _em_per_size(family: str) -> float:
    """libass (like VSFilter) scales a font so usWinAscent + usWinDescent = the ASS
    Fontsize, so the em it draws with is Fontsize × units_per_em / (winAscent + winDescent)."""
    path = font_file(family)
    m = _win_metrics(path) if path else None
    if m:
        upem, win_ascent, win_descent = m
        return upem / max(1, win_ascent + win_descent)
    f = _font(family)
    if f is None:
        return 1.0
    ascent, descent = f.getmetrics()
    return _REF_SIZE / max(1, ascent + descent)


def text_width(text: str, family: str, size: float) -> float:
    """Advance width in px of *text* at ASS Fontsize *size* (no outline)."""
    f = _font(family)
    if f is None:
        return len(text) * size * _FALLBACK_CHAR_W
    return f.getlength(text) * size / _REF_SIZE * _em_per_size(family)


def line_height(family: str, size: float) -> float:
    """Height of one line in px at ASS Fontsize *size* (= the size itself in libass)."""
    return float(size) if _font(family) is not None else size * 1.2
