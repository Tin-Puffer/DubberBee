"""Channel logo burned into the video (an image overlay).

The user's image is copied (as PNG, transparency kept) into the app's data folder, so the
logo keeps working when the original file is moved. Size is a share of the video width,
position the logo's center in normalized frame coordinates — the same convention as the
subtitle position, so the frame editor can place both.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

# Corner presets: center of the logo; MARGIN keeps it off the frame edge
MARGIN = 0.03
POSITIONS = ("tr", "tl", "br", "bl", "custom")
DEFAULT_POSITION = "tr"
DEFAULT_SIZE = 12        # % of the video width
DEFAULT_OPACITY = 90     # %
MIN_SIZE, MAX_SIZE = 3, 40


def logos_dir() -> Path:
    from platformdirs import user_data_dir
    return Path(user_data_dir("dubber", "dubber")) / "logos"


def import_logo(src: Path) -> Path:
    """Copy *src* into logos_dir() as PNG (alpha kept); same image → same file."""
    from PIL import Image
    with Image.open(src) as im:
        im.load()
        img = im.convert("RGBA")
    digest = hashlib.sha1(img.tobytes() + repr(img.size).encode()).hexdigest()[:16]
    dst = logos_dir() / f"{digest}.png"
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        part = dst.with_suffix(".part.png")
        img.save(part, "PNG")
        part.replace(dst)
    return dst


def image_size(path: Path) -> tuple[int, int]:
    from PIL import Image
    with Image.open(path) as im:
        return im.size


def logo_center(position: str, custom: "tuple | None", box_w: float, box_h: float) -> tuple:
    """Normalized center for a preset corner (box_w / box_h: logo size as a share of the
    frame), or the custom point; always keeps the whole logo inside the frame."""
    if position == "custom" and custom:
        cx, cy = custom
    else:
        left = position in ("tl", "bl")
        top = position in ("tl", "tr")
        cx = MARGIN + box_w / 2 if left else 1 - MARGIN - box_w / 2
        cy = MARGIN + box_h / 2 if top else 1 - MARGIN - box_h / 2
    cx = min(max(cx, box_w / 2), 1 - box_w / 2)
    cy = min(max(cy, box_h / 2), 1 - box_h / 2)
    return cx, cy


def logo_rect(video_w: int, video_h: int, image_w: int, image_h: int, size_pct: float,
              position: str, custom: "tuple | None" = None) -> tuple[int, int, int, int]:
    """(x, y, w, h) in video pixels; even values (YUV 4:2:0)."""
    w = max(2, round(video_w * size_pct / 100)) & ~1
    h = max(2, round(w * image_h / max(1, image_w))) & ~1
    w, h = min(w, video_w & ~1), min(h, video_h & ~1)
    cx, cy = logo_center(position, custom, w / video_w, h / video_h)
    x = min(max(0, round(cx * video_w - w / 2)), video_w - w) & ~1
    y = min(max(0, round(cy * video_h - h / 2)), video_h - h) & ~1
    return x, y, w, h
