"""Preview + pick the blur area, subtitle center, font and size on a real video frame.

The preview is rendered by FFmpeg/libass with the same ASS + blur code as the final
video, so what you see is what the export looks like (Qt's own text rendering differs
from libass by up to ~60% in width). Coordinates are normalized (0–1).
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
    QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from dubber.app.tasks import BusyBar, TaskRunner
from dubber.core.utils.ffmpeg_locator import find_ffmpeg, find_ffprobe, no_window
from dubber.core.video.fonts import (
    DEFAULT_FONT, DEFAULT_FONT_SIZE, MAX_FONT_SIZE, MIN_FONT_SIZE, SUBTITLE_FONTS, fonts_dir,
)

# One subtitle event: no . , ; and ≤ subtitles.MAX_CUE_CHARS, otherwise build_ass splits it
# into consecutive cues and the preview frame only shows the first one. About as long as
# the longest real line, so the automatic blur box looks like the export's.
SAMPLE_TEXT = "Phụ đề tiếng Việt mẫu để xem cỡ chữ và màu viền"
_BLUR_COLOR = QColor("#ff5c5c")
_SUB_COLOR = QColor("#4db8ff")
_LOGO_COLOR = QColor("#5cd65c")
_HANDLE = 10    # px: logo corner handles (drawn size, and how near counts as grabbing one)


def probe_video(path: Path) -> tuple[int, int, float]:
    """Return (width, height, duration_sec)."""
    out = subprocess.run(
        [str(find_ffprobe()), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height:format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", **no_window(),
    )
    data = json.loads(out.stdout or "{}")
    st = (data.get("streams") or [{}])[0]
    return int(st.get("width", 1920)), int(st.get("height", 1080)), float(
        data.get("format", {}).get("duration", 0) or 0
    )


def render_preview(path: Path, t: float, w: int, h: int, *, font: str, size: int,
                   sub_pos, blur_mode: str, blur_rect, color: str = "#FFFFFF",
                   outline_color: str = "#000000", outline: float = 2.0, box: bool = False,
                   blur_pad: int = 12, logo: "dict | None" = None) -> QImage:
    """One frame at t with the sample subtitle + blur + logo exactly as the export
    renders them."""
    from dubber.core.pipeline import _logo_arg, compute_auto_blur, make_sub_style, _norm_rect_to_px
    from dubber.core.video.subtitles import build_ass

    style = make_sub_style(w, h, font, size, sub_pos, color, outline_color, outline, box)
    with tempfile.TemporaryDirectory() as d:
        ass = Path(d) / "s.ass"
        seg = SimpleNamespace(vi_text=SAMPLE_TEXT, final_start=0.0, final_end=60.0)
        build_ass([seg], w, h, ass, style=style)

        area = None
        if blur_mode == "custom":
            area = _norm_rect_to_px(blur_rect, w, h)
        if blur_mode != "off" and area is None:
            area = compute_auto_blur([SAMPLE_TEXT], w, h, style, blur_pad)

        esc = lambda p: str(p).replace("\\", "/").replace(":", "\\:")  # noqa: E731
        # Same layer order as render_final: frame → blur → logo → subtitles
        parts, cur = [], "[0:v]"
        if area:
            bx, by, bw, bh = area
            r = max(3, min(15, bw // 4, bh // 4))  # same radius rule as render_final
            parts.append(f"{cur}split[a][b];[b]crop={bw}:{bh}:{bx}:{by},boxblur={r}:3[bl];"
                         f"[a][bl]overlay={bx}:{by}[blurred]")
            cur = "[blurred]"
        extra_inputs = []
        lg = _logo_arg(logo, w, h)
        if lg:
            img, lx, ly, lw, lh, opacity = lg
            extra_inputs = ["-i", str(img)]
            parts.append(f"[1:v]scale={lw}:{lh},format=rgba,colorchannelmixer=aa={opacity:.3f}[lg];"
                         f"{cur}[lg]overlay={lx}:{ly}[with_logo]")
            cur = "[with_logo]"
        parts.append(f"{cur}ass='{esc(ass)}':fontsdir='{esc(fonts_dir())}'")
        png = Path(d) / "p.png"
        subprocess.run(
            [str(find_ffmpeg()), "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(path),
             *extra_inputs, "-frames:v", "1", "-filter_complex", ";".join(parts),
             "-update", "1", str(png)],
            capture_output=True, **no_window(),
        )
        return QImage(str(png))


class FrameCanvas(QWidget):
    """The rendered preview, edited directly — whatever is under the mouse is what moves:

    logo corner = resize (aspect kept) · logo = move · blur corner = resize · blur area =
    move · subtitle line = move · anywhere else = draw a new blur area. The cursor shows
    what a drag will do. Coordinates are normalized (0–1) like the saved config.
    """

    changed = Signal()

    def __init__(self, video_w: int, video_h: int, parent=None):
        super().__init__(parent)
        self.video_w, self.video_h = video_w, video_h
        self.image = QImage()
        self.blur_rect: tuple | None = None     # normalized (x, y, w, h)
        self.blur_enabled = True                # blur mode "off" hides the area
        self.sub_pos: tuple | None = None       # normalized center of the line; None = default
        # Subtitle line size + default center (normalized), from the real font metrics
        self.sub_size: tuple = (0.0, 0.0)
        self.sub_default: tuple = (0.5, 0.95)
        # Logo: size in % of the video width, image height / width, normalized center;
        # logo_moved = placed by hand (→ custom position)
        self.logo_size: float | None = None
        self.logo_aspect = 1.0
        self.logo_center: tuple | None = None
        self.logo_moved = False
        self.logo_pixmap = None
        self.dragging = False
        self._drag: "tuple | None" = None       # (kind, a, b[, c, d]) — see press()
        self.setMinimumSize(640, 360)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)              # cursor feedback while hovering
        self.setCursor(Qt.CrossCursor)

    # ── geometry ──────────────────────────────────────────────────────────────
    def _target(self) -> QRectF:
        """Where the frame is drawn (aspect-fit inside the widget)."""
        if self.video_w <= 0 or self.video_h <= 0:
            return QRectF(self.rect())
        s = min(self.width() / self.video_w, self.height() / self.video_h)
        w, h = self.video_w * s, self.video_h * s
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def _to_norm(self, p: QPointF) -> tuple[float, float]:
        r = self._target()
        return (
            min(1.0, max(0.0, (p.x() - r.x()) / r.width())),
            min(1.0, max(0.0, (p.y() - r.y()) / r.height())),
        )

    def _norm_rect_to_widget(self, nr: tuple) -> QRectF:
        r = self._target()
        return QRectF(r.x() + nr[0] * r.width(), r.y() + nr[1] * r.height(),
                      nr[2] * r.width(), nr[3] * r.height())

    def _near(self, pos: QPointF, norm_pt: tuple) -> bool:
        r = self._target()
        px = QPointF(r.x() + norm_pt[0] * r.width(), r.y() + norm_pt[1] * r.height())
        return abs(px.x() - pos.x()) <= _HANDLE and abs(px.y() - pos.y()) <= _HANDLE

    @staticmethod
    def _corners(box: tuple) -> list:
        x, y, w, h = box
        return [(x, y), (x + w, y), (x, y + h), (x + w, y + h)]

    @staticmethod
    def _inside(pt: tuple, box: "tuple | None", grow: float = 0.0) -> bool:
        if not box:
            return False
        x, y, w, h = box
        return x - grow <= pt[0] <= x + w + grow and y - grow <= pt[1] <= y + h + grow

    def logo_box(self) -> "tuple | None":
        """Normalized (x, y, w, h) of the logo, kept inside the frame (as the export)."""
        if self.logo_size is None or self.logo_center is None:
            return None
        from dubber.core.video.logo import logo_center
        w = self.logo_size / 100
        h = w * self.logo_aspect * self.video_w / max(1, self.video_h)
        cx, cy = logo_center("custom", self.logo_center, w, h)
        return cx - w / 2, cy - h / 2, w, h

    def sub_box(self) -> "tuple | None":
        """Normalized (x, y, w, h) of the subtitle line (None until its size is known)."""
        w, h = self.sub_size
        if w <= 0 or h <= 0:
            return None
        cx, cy = self.sub_pos or self.sub_default
        return cx - w / 2, cy - h / 2, w, h

    def _blur_box(self) -> "tuple | None":
        return self.blur_rect if self.blur_enabled else None

    # ── what is under the mouse ───────────────────────────────────────────────
    def hit(self, pos: QPointF) -> tuple:
        """(what, corner index) the mouse would grab: "logo_corner", "logo",
        "blur_corner", "blur", "sub" or "empty"."""
        pt = self._to_norm(pos)
        logo, blur = self.logo_box(), self._blur_box()
        if logo:
            for i, c in enumerate(self._corners(logo)):
                if self._near(pos, c):
                    return "logo_corner", i
            if self._inside(pt, logo):
                return "logo", None
        if blur:
            for i, c in enumerate(self._corners(blur)):
                if self._near(pos, c):
                    return "blur_corner", i
        if self._inside(pt, self.sub_box(), grow=0.01):
            return "sub", None
        if self._inside(pt, blur):
            return "blur", None
        return "empty", None

    _CURSORS = {"logo_corner": None, "blur_corner": None, "logo": Qt.SizeAllCursor,
                "blur": Qt.SizeAllCursor, "sub": Qt.SizeAllCursor, "empty": Qt.CrossCursor}

    def _hover_cursor(self, pos: QPointF) -> None:
        what, i = self.hit(pos)
        if what.endswith("corner"):
            self.setCursor(Qt.SizeFDiagCursor if i in (0, 3) else Qt.SizeBDiagCursor)
        else:
            self.setCursor(self._CURSORS[what])

    # ── editing (also called by tests) ────────────────────────────────────────
    def press(self, pos: QPointF) -> None:
        pt = self._to_norm(pos)
        what, i = self.hit(pos)
        if what == "logo_corner":
            ax, ay = self._corners(self.logo_box())[3 - i]   # the opposite corner stays put
            self._drag = ("logo_resize", ax, ay)
        elif what == "logo":
            x, y, w, h = self.logo_box()
            self._drag = ("logo_move", x + w / 2 - pt[0], y + h / 2 - pt[1])
        elif what == "blur_corner":
            ax, ay = self._corners(self.blur_rect)[3 - i]
            self._drag = ("blur_resize", ax, ay)
        elif what == "blur":
            x, y, w, h = self.blur_rect
            self._drag = ("blur_move", x - pt[0], y - pt[1], w, h)
        elif what == "sub":
            cx, cy = self.sub_pos or self.sub_default
            self._drag = ("sub_move", cx - pt[0], cy - pt[1])
        else:
            self._drag = ("blur_new", pt[0], pt[1])

    def drag(self, pos: QPointF) -> None:
        if not self._drag:
            return
        px, py = self._to_norm(pos)
        kind, a, b, *rest = self._drag
        if kind == "logo_move":
            self.logo_center, self.logo_moved = (px + a, py + b), True
        elif kind == "logo_resize":
            from dubber.core.video.logo import MAX_SIZE, MIN_SIZE
            ratio = self.logo_aspect * self.video_w / max(1, self.video_h)   # h = w * ratio
            w = max(abs(px - a), abs(py - b) / ratio)    # whichever the mouse went further
            w = min(max(w, MIN_SIZE / 100), MAX_SIZE / 100)
            sx, sy = (1 if px >= a else -1), (1 if py >= b else -1)
            self.logo_size = w * 100
            self.logo_center, self.logo_moved = (a + sx * w / 2, b + sy * w * ratio / 2), True
        elif kind in ("blur_new", "blur_resize"):
            self.blur_rect = (min(a, px), min(b, py), abs(px - a), abs(py - b))
            self.blur_enabled = True
        elif kind == "blur_move":
            w, h = rest
            self.blur_rect = (min(max(0.0, px + a), 1 - w), min(max(0.0, py + b), 1 - h), w, h)
        elif kind == "sub_move":
            self.sub_pos = (min(max(0.0, px + a), 1.0), min(max(0.0, py + b), 1.0))
        self.update()

    def release(self) -> None:
        if self._drag and self._drag[0] in ("blur_new", "blur_resize") and self.blur_rect \
                and (self.blur_rect[2] < 0.01 or self.blur_rect[3] < 0.01):
            if self._drag[0] == "blur_new":
                self.blur_rect = None            # a click, not a drag
        self._drag = None

    # ── mouse ─────────────────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        self.dragging = True
        self.press(e.position())
        self.update()

    def mouseMoveEvent(self, e):
        if self.dragging and e.buttons() & Qt.LeftButton:
            self.drag(e.position())
        else:
            self._hover_cursor(e.position())

    def mouseReleaseEvent(self, e):
        if not self.dragging:
            return
        self.dragging = False
        self.release()
        self.changed.emit()
        self.update()

    # ── painting ──────────────────────────────────────────────────────────────
    def _handles(self, p: QPainter, wr: QRectF, color: QColor) -> None:
        p.setPen(QPen(QColor("#0b0b0b"), 1))
        p.setBrush(color)
        for c in (wr.topLeft(), wr.topRight(), wr.bottomLeft(), wr.bottomRight()):
            p.drawRect(QRectF(c.x() - _HANDLE / 2, c.y() - _HANDLE / 2, _HANDLE, _HANDLE))
        p.setBrush(Qt.NoBrush)

    def _tag(self, p: QPainter, wr: QRectF, text: str, color: QColor) -> None:
        p.setPen(color)
        p.setFont(QFont("Arial", 11))
        y = wr.top() - 6 if wr.top() > self._target().top() + 16 else wr.bottom() + 16
        p.drawText(QPointF(wr.left() + 2, y), text)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor("#0b0b0b"))
        target = self._target()
        if not self.image.isNull():
            p.drawImage(target, self.image)

        blur = self._blur_box()
        if blur:
            wr = self._norm_rect_to_widget(blur)
            p.setPen(QPen(_BLUR_COLOR, 2, Qt.DashLine))
            p.drawRect(wr)
            self._handles(p, wr, _BLUR_COLOR)
            self._tag(p, wr, "Vùng làm mờ", _BLUR_COLOR)

        sub = self.sub_box()
        if sub:
            wr = self._norm_rect_to_widget(sub)
            p.setPen(QPen(_SUB_COLOR, 1.5, Qt.DashLine))
            p.drawRect(wr)
            self._tag(p, wr, "Phụ đề — kéo để di chuyển", _SUB_COLOR)

        logo = self.logo_box()
        if logo:
            wr = self._norm_rect_to_widget(logo)
            if self.dragging and self.logo_pixmap is not None:   # live, before the re-render
                p.setOpacity(0.85)
                p.drawPixmap(wr, self.logo_pixmap, QRectF(self.logo_pixmap.rect()))
                p.setOpacity(1.0)
            p.setPen(QPen(_LOGO_COLOR, 2, Qt.DashLine))
            p.drawRect(wr)
            self._handles(p, wr, _LOGO_COLOR)
            self._tag(p, wr, "Logo", _LOGO_COLOR)

        if self.dragging:
            p.setPen(QColor("#ffffffcc"))
            p.setFont(QFont("Arial", 11))
            p.drawText(target.adjusted(8, 8, -8, -8), Qt.AlignTop | Qt.AlignRight,
                       "Thả chuột để xem chính xác")
        p.end()


class LayoutDialog(QDialog):
    """Modal editor; after exec() == Accepted read .blur_rect / .sub_pos / .font_family / .font_size."""

    def __init__(self, video_path: Path, blur_rect=None, sub_pos=None, parent=None,
                 font_family: str = DEFAULT_FONT, font_size: int = DEFAULT_FONT_SIZE,
                 blur_mode: str = "auto", style: "dict | None" = None,
                 video_info: "tuple[int, int, float] | None" = None,
                 logo: "dict | None" = None):
        super().__init__(parent)
        self._logo = dict(logo) if logo and logo.get("path") else None
        self.setWindowTitle(f"Chỉnh trên khung hình — {video_path.name}")
        self._path = video_path
        self._blur_mode = blur_mode       # off / auto / custom — dragging a box picks custom
        self._style = dict(style or {})   # color, outline_color, outline, box, blur_pad
        self._vw, self._vh, self._duration = video_info or probe_video(video_path)
        self._runner = TaskRunner(self)
        self._gen = 0          # newest render request; older results are dropped
        self._rendering = False

        lay = QVBoxLayout(self)
        hint = QLabel(
            "Khung hình giống hệt video xuất ra — kéo trực tiếp:  "
            "<span style='color:#4db8ff'>■ phụ đề</span> để di chuyển  ·  "
            "<span style='color:#ff5c5c'>■ vùng làm mờ</span> để di chuyển, góc để đổi cỡ, "
            "kéo chỗ trống để vẽ vùng mới  ·  "
            "<span style='color:#5cd65c'>■ logo</span> để di chuyển, góc để đổi cỡ.  "
            "Kéo thanh thời gian để tìm khung hình có phụ đề gốc.", self)
        hint.setTextFormat(Qt.RichText)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888; font-size: 12px;")
        lay.addWidget(hint)

        self.canvas = FrameCanvas(self._vw, self._vh, self)
        self.canvas.blur_rect = tuple(blur_rect) if blur_rect else None
        self.canvas.blur_enabled = blur_mode == "custom"
        self.canvas.sub_pos = tuple(sub_pos) if sub_pos else None
        if self._logo:
            self._init_logo_canvas()
        self.canvas.changed.connect(self._on_canvas_changed)
        lay.addWidget(self.canvas, 1)
        self._busy = BusyBar(self)
        lay.addWidget(self._busy)

        # Time slider
        row_t = QHBoxLayout()
        self._slider = QSlider(Qt.Horizontal, self)
        self._slider.setRange(0, max(1, int(self._duration * 10)))
        self._slider.setValue(int(self._duration * 10 / 3))
        self._lbl_t = QLabel("", self)
        self._lbl_t.setFixedWidth(110)
        self._slider.valueChanged.connect(self._on_slider_moved)
        self._slider.sliderReleased.connect(self._schedule_render)
        row_t.addWidget(QLabel("Khung hình:", self))
        row_t.addWidget(self._slider, 1)
        row_t.addWidget(self._lbl_t)
        lay.addLayout(row_t)

        # Font + size (live)
        row_f = QHBoxLayout()
        row_f.addWidget(QLabel("Font:", self))
        self._combo_font = QComboBox(self)
        for family, label in SUBTITLE_FONTS:
            self._combo_font.addItem(label, userData=family)
            f = QFont(family); f.setBold(True); f.setPixelSize(13)
            self._combo_font.setItemData(self._combo_font.count() - 1, f, Qt.FontRole)
        idx = self._combo_font.findText(next((l for fam, l in SUBTITLE_FONTS if fam == font_family), font_family))
        self._combo_font.setCurrentIndex(max(0, idx))
        self._combo_font.currentIndexChanged.connect(self._schedule_render)
        row_f.addWidget(self._combo_font, 1)
        row_f.addWidget(QLabel("Cỡ chữ:", self))
        self._spin_size = QSpinBox(self)
        self._spin_size.setRange(MIN_FONT_SIZE, MAX_FONT_SIZE)
        self._spin_size.setSingleStep(2)
        self._spin_size.setValue(font_size)
        self._spin_size.valueChanged.connect(self._schedule_render)
        row_f.addWidget(self._spin_size)
        self._lbl_px = QLabel("", self)
        self._lbl_px.setStyleSheet("color: #888; font-size: 11px;")
        row_f.addWidget(self._lbl_px)
        self._spin_logo = None
        if self._logo:
            from dubber.core.video.logo import DEFAULT_SIZE, MAX_SIZE, MIN_SIZE
            row_f.addWidget(QLabel("Logo:", self))
            self._spin_logo = QSpinBox(self)
            self._spin_logo.setRange(MIN_SIZE, MAX_SIZE)
            self._spin_logo.setSuffix(" % rộng")
            self._spin_logo.setValue(int(self._logo.get("size", DEFAULT_SIZE)))
            self._spin_logo.setToolTip("Hoặc kéo góc logo trên khung hình (giữ tỉ lệ ảnh)")
            self._spin_logo.valueChanged.connect(self._on_logo_spin)
            row_f.addWidget(self._spin_logo)
        lay.addLayout(row_f)

        # Blur mode + resets
        row_m = QHBoxLayout()
        row_m.addWidget(QLabel("Che phụ đề gốc:", self))
        self._combo_blur = QComboBox(self)
        self._combo_blur.addItem("Tắt", userData="off")
        self._combo_blur.addItem("Tự động (theo phụ đề Việt)", userData="auto")
        self._combo_blur.addItem("Vùng tự vẽ", userData="custom")
        idx = self._combo_blur.findData(blur_mode)
        self._combo_blur.setCurrentIndex(max(0, idx))
        self._combo_blur.currentIndexChanged.connect(self._on_blur_combo)
        row_m.addWidget(self._combo_blur)
        row_m.addStretch()
        btn_clear_blur = QPushButton("Xóa vùng làm mờ", self)
        btn_clear_blur.clicked.connect(lambda: self._reset("blur"))
        btn_reset_sub = QPushButton("Phụ đề về mặc định", self)
        btn_reset_sub.clicked.connect(lambda: self._reset("sub"))
        row_m.addWidget(btn_clear_blur)
        row_m.addWidget(btn_reset_sub)
        if self._logo:
            btn_reset_logo = QPushButton("Logo về góc", self)
            btn_reset_logo.setToolTip("Đưa logo về góc đã chọn trong tab Phụ đề, cỡ giữ nguyên")
            btn_reset_logo.clicked.connect(lambda: self._reset("logo"))
            row_m.addWidget(btn_reset_logo)
        lay.addLayout(row_m)

        row_b = QHBoxLayout()
        row_b.addStretch()
        btn_cancel = QPushButton("Hủy", self)
        btn_ok = QPushButton("Xong", self)
        btn_ok.setDefault(True)
        btn_cancel.clicked.connect(self.reject)
        btn_ok.clicked.connect(self.accept)
        row_b.addWidget(btn_cancel)
        row_b.addWidget(btn_ok)
        lay.addLayout(row_b)

        # Debounce: re-render 150 ms after the last change
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._render)

        self.resize(1000, 760)
        self._on_slider_moved(self._slider.value())
        self._render()

    def _init_logo_canvas(self, keep_size: bool = False) -> None:
        """Logo box on the canvas: image aspect + size + where the export puts it
        (keep_size: back to its corner preset at the size shown now)."""
        from PySide6.QtGui import QPixmap
        from dubber.core.video.logo import DEFAULT_POSITION, DEFAULT_SIZE, logo_center
        pix = QPixmap(str(self._logo["path"]))
        c = self.canvas
        c.logo_pixmap = None if pix.isNull() else pix
        c.logo_aspect = pix.height() / pix.width() if not pix.isNull() and pix.width() else 1.0
        if not (keep_size and c.logo_size):
            c.logo_size = float(self._logo.get("size", DEFAULT_SIZE))
        w = c.logo_size / 100
        h = w * c.logo_aspect * self._vw / max(1, self._vh)
        position = self._logo.get("position", DEFAULT_POSITION)
        if keep_size and position == "custom":
            position = DEFAULT_POSITION
        pos = None if keep_size else self._logo.get("pos")
        c.logo_center = logo_center(position, pos, w, h)
        if keep_size:
            self._logo["position"], self._logo["pos"] = position, None
        c.logo_moved = False

    def _on_logo_spin(self, value: int) -> None:
        if self.canvas.logo_size is not None and round(self.canvas.logo_size) != value:
            self.canvas.logo_size = float(value)
            self.canvas.update()
        self._schedule_render()

    def _on_blur_combo(self, *_):
        self._blur_mode = self._combo_blur.currentData()
        self.canvas.blur_enabled = self._blur_mode == "custom"
        self.canvas.update()
        self._schedule_render()

    def _sub_geometry(self) -> None:
        """Size + default center of the subtitle line on the canvas (real font metrics, as
        libass draws it) — so the line can be grabbed where it shows."""
        from dubber.core.pipeline import make_sub_style
        from dubber.core.video.subtitles import cue_size_px
        s = self._style
        style = make_sub_style(self._vw, self._vh, self.font_family, self.font_size, None,
                               s.get("color", "#FFFFFF"), s.get("outline_color", "#000000"),
                               s.get("outline", 2.0), s.get("box", False))
        tw, th = cue_size_px(SAMPLE_TEXT, style, self._vh)
        tw = min(tw, self._vw * 0.94)                     # long lines are fitted to one line
        self.canvas.sub_size = (tw / self._vw, th / self._vh)
        self.canvas.sub_default = (0.5, (self._vh - style.margin_v - th / 2) / self._vh)
        self.canvas.update()

    def _on_slider_moved(self, v: int):
        t = v / 10
        self._lbl_t.setText(f"{int(t // 60)}:{t % 60:04.1f} / {int(self._duration // 60)}:{self._duration % 60:04.1f}")
        if not self._slider.isSliderDown():  # keyboard / click-to-jump
            self._schedule_render()

    def _schedule_render(self, *_):
        self._timer.start()

    def _on_canvas_changed(self):
        if self._spin_logo is not None and self.canvas.logo_size is not None:
            self.canvas.logo_size = float(round(self.canvas.logo_size))   # whole % like the box
            self._spin_logo.blockSignals(True)
            self._spin_logo.setValue(int(self.canvas.logo_size))
            self._spin_logo.blockSignals(False)
        if self.canvas.blur_enabled and self.canvas.blur_rect and self._blur_mode != "custom":
            self._blur_mode = "custom"            # drew / moved an area → "Vùng tự vẽ"
            self._combo_blur.blockSignals(True)
            self._combo_blur.setCurrentIndex(self._combo_blur.findData("custom"))
            self._combo_blur.blockSignals(False)
        self._schedule_render()

    def _effective_blur_mode(self) -> str:
        if self._blur_mode == "custom" and not self.canvas.blur_rect:
            return "auto"
        return self._blur_mode

    def _render(self):
        """Render the frame on a worker thread (FFmpeg takes ~0.3–1 s); keep showing the
        previous frame meanwhile. Only the newest request is drawn."""
        from dubber.core.video.fonts import scaled_font_px
        self._lbl_px.setText(f"= {scaled_font_px(self.font_size, self._vh)} px trên video {self._vw}×{self._vh}")
        self._sub_geometry()
        if self._rendering:          # one FFmpeg at a time; redo when it finishes
            self._gen += 1
            return
        self._gen += 1
        gen = self._gen
        s = self._style
        args = dict(
            font=self.font_family, size=self.font_size, sub_pos=self.canvas.sub_pos,
            blur_mode=self._effective_blur_mode(), blur_rect=self.canvas.blur_rect,
            color=s.get("color", "#FFFFFF"), outline_color=s.get("outline_color", "#000000"),
            outline=s.get("outline", 2.0), box=s.get("box", False), blur_pad=s.get("blur_pad", 12),
            logo=self.logo,
        )
        t = self._slider.value() / 10
        self._rendering = True
        self._busy.start("Đang dựng khung xem trước…")
        self._runner.run(lambda: render_preview(self._path, t, self._vw, self._vh, **args),
                         on_done=lambda img: self._rendered(gen, img),
                         on_fail=lambda msg: self._rendered(gen, None, msg))

    def _rendered(self, gen: int, image, error: str = ""):
        self._rendering = False
        self._busy.stop()
        if image is not None and not image.isNull():
            self.canvas.image = image
            self.canvas.update()
        elif error:
            self._lbl_px.setText(f"Không dựng được khung hình: {error[:80]}")
        if gen != self._gen:          # settings changed while rendering
            self._render()

    def done(self, result):   # QDialog.done (accept / reject)
        self._runner.wait_all()
        super().done(result)

    def _reset(self, what: str):
        if what == "blur":
            self.canvas.blur_rect = None
            if self._blur_mode == "custom":
                self._combo_blur.setCurrentIndex(self._combo_blur.findData("auto"))
        elif what == "logo":
            self._init_logo_canvas(keep_size=True)
        else:
            self.canvas.sub_pos = None
        self.canvas.update()
        self._schedule_render()

    @property
    def logo(self) -> "dict | None":
        """The logo settings with this dialog's size / clicked position (None: no logo)."""
        if not self._logo:
            return None
        out = dict(self._logo)
        if self._spin_logo is not None:
            out["size"] = self._spin_logo.value()
        if self.canvas.logo_moved and self.canvas.logo_center:
            x, y, w, h = self.canvas.logo_box()
            out["position"], out["pos"] = "custom", (x + w / 2, y + h / 2)
        return out

    @property
    def blur_mode(self) -> str:
        return self._effective_blur_mode()

    @property
    def blur_rect(self):
        return self.canvas.blur_rect

    @property
    def sub_pos(self):
        return self.canvas.sub_pos

    @property
    def font_family(self) -> str:
        return self._combo_font.currentData()

    @property
    def font_size(self) -> int:
        return self._spin_size.value()
