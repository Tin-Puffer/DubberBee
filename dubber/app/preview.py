"""Quick subtitle preview on a frame of the video (approximate: Qt draws it, not libass —
"Xem trước trên video…" in the Phụ đề tab renders the exact look)."""
from __future__ import annotations

import hashlib
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap

SAMPLE_TEXT = "Phụ đề tiếng Việt sẽ hiện như thế này"


def frame_cache_path(video: Path) -> Path:
    from platformdirs import user_cache_dir
    st = video.stat()
    key = hashlib.sha1(f"{video.resolve()}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()
    return Path(user_cache_dir("dubber", "dubber")) / "frames" / f"{key[:16]}.jpg"


def extract_frame(video: Path, duration: float) -> Path:
    """One frame at ~30 % of the video (past intros, likely with speech / old subtitles)."""
    import subprocess

    from dubber.core.utils.ffmpeg_locator import find_ffmpeg, no_window
    out = frame_cache_path(video)
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    t = max(0.0, min(duration * 0.3, duration - 0.5)) if duration else 0.0
    part = out.with_suffix(".part.jpg")
    cmd = [str(find_ffmpeg()), "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(video),
           "-frames:v", "1", "-vf", "scale=640:-2", str(part)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       **no_window())
    if r.returncode != 0 or not part.exists():
        raise RuntimeError((r.stderr or "FFmpeg không lấy được khung hình")[-300:])
    part.replace(out)
    return out


def _blur(pix: QPixmap, rect: QRectF) -> None:
    """Pixelate-then-smooth *rect* of *pix* in place (looks like the export's boxblur)."""
    r = rect.toAlignedRect().intersected(pix.rect())
    if r.width() < 4 or r.height() < 4:
        return
    piece = pix.copy(r)
    small = piece.scaled(max(1, r.width() // 12), max(1, r.height() // 12),
                         Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    p = QPainter(pix)
    p.drawPixmap(r, small.scaled(r.size(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
    p.end()


def _paint_logo(pix: QPixmap, video_w: int, video_h: int, logo: "dict | None") -> None:
    """The channel logo where the export puts it (same logo_rect math)."""
    if not logo or not logo.get("path"):
        return
    from dubber.core.video.logo import DEFAULT_OPACITY, DEFAULT_POSITION, DEFAULT_SIZE, logo_rect
    img = QPixmap(str(logo["path"]))
    if img.isNull():
        return
    x, y, w, h = logo_rect(video_w, video_h, img.width(), img.height(),
                           logo.get("size", DEFAULT_SIZE), logo.get("position", DEFAULT_POSITION),
                           logo.get("pos"))
    s = pix.height() / video_h
    p = QPainter(pix)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.setOpacity(logo.get("opacity", DEFAULT_OPACITY) / 100)
    p.drawPixmap(QRectF(x * s, y * s, w * s, h * s), img, QRectF(img.rect()))
    p.end()


def _brighten(pix: QPixmap, pct: int) -> None:
    """Approximate FFmpeg eq brightness (% / 200 added to every pixel) in place."""
    if not pct:
        return
    p = QPainter(pix)
    a = min(255, round(abs(pct) / 200 * 255))
    p.fillRect(pix.rect(), QColor(255, 255, 255, a) if pct > 0 else QColor(0, 0, 0, a))
    p.end()


def portrait_canvas(pix: QPixmap, bg_brightness: "int | None" = None) -> QPixmap:
    """*pix* fitted in a 9:16 frame over a blurred copy of itself (as exported)."""
    from dubber.core.video.render import DEFAULT_BG_BRIGHTNESS
    if bg_brightness is None:
        bg_brightness = DEFAULT_BG_BRIGHTNESS
    W = pix.width() if pix.width() * 16 >= pix.height() * 9 else round(pix.height() * 9 / 16)
    H = round(W * 16 / 9)
    cover = pix.scaled(W, H, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    cover = cover.copy((cover.width() - W) // 2, (cover.height() - H) // 2, W, H)
    small = cover.scaled(max(1, W // 24), max(1, H // 24), Qt.IgnoreAspectRatio,
                         Qt.SmoothTransformation)
    out = small.scaled(W, H, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    fit = pix.scaled(W, H, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    _brighten(out, bg_brightness)
    p = QPainter(out)
    p.drawPixmap((W - fit.width()) // 2, (H - fit.height()) // 2, fit)
    p.end()
    return out


def paint_preview(frame: Path, video_w: int, video_h: int, layout: dict, show_subs: bool,
                  width: int = 320) -> QPixmap:
    """*layout*: the run_m5 blur / subtitle arguments (gui._layout_kwargs)."""
    pix = _paint_frame(frame, video_w, video_h, layout, show_subs, width)
    if layout.get("portrait") and not pix.isNull():
        pix = portrait_canvas(pix, layout.get("bg_brightness"))
    return pix


def _paint_frame(frame: Path, video_w: int, video_h: int, layout: dict, show_subs: bool,
                 width: int) -> QPixmap:
    pix = QPixmap(str(frame))
    if pix.isNull():
        return pix
    pix = pix.scaledToWidth(width, Qt.SmoothTransformation)
    if layout.get("flip"):            # mirrored before blur / logo / subtitles, as exported
        from PySide6.QtGui import QTransform
        pix = pix.transformed(QTransform().scale(-1, 1))
    _brighten(pix, layout.get("brightness", 0))     # the picture only, before subtitles
    W, H = pix.width(), pix.height()
    vw, vh = (video_w or W), (video_h or H)
    scale = H / vh

    font = QFont(layout.get("sub_font") or "Arial")
    font.setPixelSize(max(6, round(layout.get("sub_size", 48) * scale * 0.85)))
    path = QPainterPath()
    path.addText(0, 0, font, SAMPLE_TEXT)
    box = path.boundingRect()
    if box.width() > W * 0.94:   # long line: shrink like the exporter's one-line fit
        f = W * 0.94 / box.width()
        font.setPixelSize(max(6, round(font.pixelSize() * f)))
        path = QPainterPath(); path.addText(0, 0, font, SAMPLE_TEXT)
        box = path.boundingRect()

    pos = layout.get("sub_pos")
    if pos:
        cx, bottom = pos[0] * W, pos[1] * H
    else:
        cx, bottom = W / 2, H - max(10, vh * 0.02) * scale
    dx, dy = cx - box.center().x(), bottom - box.bottom()
    path.translate(dx, dy)
    text_rect = path.boundingRect()

    mode = layout.get("blur_mode", "auto")
    if mode == "custom" and layout.get("blur_rect"):
        x, y, w, h = layout["blur_rect"]
        _blur(pix, QRectF(x * W, y * H, w * W, h * H))
    elif mode == "auto" and show_subs:
        pad = layout.get("blur_pad", 12) * scale
        _blur(pix, text_rect.adjusted(-pad, -pad, pad, pad))

    _paint_logo(pix, vw, vh, layout.get("logo"))
    if not show_subs:
        return pix
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    if layout.get("sub_box"):
        p.fillRect(text_rect.adjusted(-6, -3, 6, 3), QColor(0, 0, 0, 170))
    outline = float(layout.get("sub_outline", 2.0)) * scale * 2
    if outline > 0:
        p.setPen(QPen(QColor(layout.get("sub_outline_color", "#000000")), outline,
                      Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(layout.get("sub_color", "#FFFFFF")))
    p.drawPath(path)
    p.end()
    return pix
