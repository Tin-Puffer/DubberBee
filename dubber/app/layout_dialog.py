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
    QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QRadioButton, QSizePolicy,
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
                   blur_pad: int = 12) -> QImage:
    """One frame at t with the sample subtitle + blur exactly as the export renders them."""
    from dubber.core.pipeline import compute_auto_blur, make_sub_style, _norm_rect_to_px
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
        vf = f"ass='{esc(ass)}':fontsdir='{esc(fonts_dir())}'"
        if area:
            bx, by, bw, bh = area
            r = max(3, min(15, bw // 4, bh // 4))  # same radius rule as render_final
            vf = (f"split[a][b];[b]crop={bw}:{bh}:{bx}:{by},boxblur={r}:3[bl];"
                  f"[a][bl]overlay={bx}:{by}," + vf)
        png = Path(d) / "p.png"
        subprocess.run(
            [str(find_ffmpeg()), "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(path),
             "-frames:v", "1", "-filter_complex", vf, "-update", "1", str(png)],
            capture_output=True, **no_window(),
        )
        return QImage(str(png))


class FrameCanvas(QWidget):
    """Shows the rendered preview; drag = blur rectangle, click/drag = subtitle center."""

    changed = Signal()

    def __init__(self, video_w: int, video_h: int, parent=None):
        super().__init__(parent)
        self.video_w, self.video_h = video_w, video_h
        self.image = QImage()
        self.mode = "blur"                      # "blur" | "sub"
        self.blur_rect: tuple | None = None     # normalized (x, y, w, h)
        self.sub_pos: tuple | None = None       # normalized (cx, cy)
        self.dragging = False
        self._drag_start: QPointF | None = None
        self.setMinimumSize(640, 360)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
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

    # ── mouse ─────────────────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        self.dragging = True
        if self.mode == "blur":
            self._drag_start = e.position()
        else:
            self.sub_pos = self._to_norm(e.position())
            self.update()

    def mouseMoveEvent(self, e):
        if self.mode == "blur" and self._drag_start is not None:
            (x0, y0), (x1, y1) = self._to_norm(self._drag_start), self._to_norm(e.position())
            self.blur_rect = (min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
            self.update()
        elif self.mode == "sub" and e.buttons() & Qt.LeftButton:
            self.sub_pos = self._to_norm(e.position())
            self.update()

    def mouseReleaseEvent(self, e):
        if not self.dragging:
            return
        self.dragging = False
        if self.mode == "blur":
            self._drag_start = None
            if self.blur_rect and (self.blur_rect[2] < 0.01 or self.blur_rect[3] < 0.01):
                self.blur_rect = None  # a click, not a drag
        self.changed.emit()
        self.update()

    # ── painting ──────────────────────────────────────────────────────────────
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor("#0b0b0b"))
        target = self._target()
        if not self.image.isNull():
            p.drawImage(target, self.image)

        if self.blur_rect:
            wr = self._norm_rect_to_widget(self.blur_rect)
            p.setPen(QPen(_BLUR_COLOR, 2, Qt.DashLine))
            p.drawRect(wr)
            p.setPen(_BLUR_COLOR)
            p.setFont(QFont("Arial", 11))
            p.drawText(wr.topLeft() + QPointF(4, -6), "Vùng làm mờ")

        if self.sub_pos:
            c = QPointF(target.x() + self.sub_pos[0] * target.width(),
                        target.y() + self.sub_pos[1] * target.height())
            p.setPen(QPen(_SUB_COLOR, 2))
            p.drawLine(c + QPointF(-10, 0), c + QPointF(10, 0))
            p.drawLine(c + QPointF(0, -10), c + QPointF(0, 10))

        if self.dragging:
            p.setPen(QColor("#ffffffcc"))
            p.setFont(QFont("Arial", 11))
            p.drawText(target.adjusted(8, 8, -8, -8), Qt.AlignTop | Qt.AlignRight,
                       "Thả chuột để xem chính xác")
        p.end()


class LayoutDialog(QDialog):
    """Modal editor; after exec() == Accepted read .blur_rect / .sub_pos / .font_family / .font_size."""

    def __init__(self, video_path: Path, blur_rect=None, sub_pos=None, start_mode="blur", parent=None,
                 font_family: str = DEFAULT_FONT, font_size: int = DEFAULT_FONT_SIZE,
                 blur_mode: str = "auto", style: "dict | None" = None,
                 video_info: "tuple[int, int, float] | None" = None):
        super().__init__(parent)
        self.setWindowTitle(f"Xem trước phụ đề & vùng mờ — {video_path.name}")
        self._path = video_path
        self._blur_mode = blur_mode       # off / auto / custom — dragging a box picks custom
        self._style = dict(style or {})   # color, outline_color, outline, box, blur_pad
        self._vw, self._vh, self._duration = video_info or probe_video(video_path)
        self._runner = TaskRunner(self)
        self._gen = 0          # newest render request; older results are dropped
        self._rendering = False

        lay = QVBoxLayout(self)
        hint = QLabel(
            "Ảnh xem trước được vẽ giống hệt video xuất ra. Kéo chuột để chọn vùng làm mờ "
            "(che phụ đề Trung) · Bấm để đặt tâm phụ đề tiếng Việt · Kéo thanh thời gian để tìm "
            "khung hình có phụ đề gốc.", self)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888; font-size: 12px;")
        lay.addWidget(hint)

        self.canvas = FrameCanvas(self._vw, self._vh, self)
        self.canvas.blur_rect = tuple(blur_rect) if blur_rect else None
        self.canvas.sub_pos = tuple(sub_pos) if sub_pos else None
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
        lay.addLayout(row_f)

        # Mode + reset
        row_m = QHBoxLayout()
        self._rb_blur = QRadioButton("Kéo chọn vùng làm mờ", self)
        self._rb_sub = QRadioButton("Bấm chọn tâm phụ đề", self)
        (self._rb_sub if start_mode in ("sub", "preview") else self._rb_blur).setChecked(True)
        self._rb_blur.toggled.connect(self._on_mode)
        self._on_mode()
        btn_clear_blur = QPushButton("Xoá vùng mờ", self)
        btn_clear_blur.clicked.connect(lambda: self._reset("blur"))
        btn_reset_sub = QPushButton("Phụ đề về mặc định", self)
        btn_reset_sub.clicked.connect(lambda: self._reset("sub"))
        for w in (self._rb_blur, self._rb_sub):
            row_m.addWidget(w)
        row_m.addStretch()
        row_m.addWidget(btn_clear_blur)
        row_m.addWidget(btn_reset_sub)
        lay.addLayout(row_m)

        row_b = QHBoxLayout()
        row_b.addStretch()
        btn_cancel = QPushButton("Huỷ", self)
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

    def _on_mode(self, *_):
        self.canvas.mode = "blur" if self._rb_blur.isChecked() else "sub"

    def _on_slider_moved(self, v: int):
        t = v / 10
        self._lbl_t.setText(f"{int(t // 60)}:{t % 60:04.1f} / {int(self._duration // 60)}:{self._duration % 60:04.1f}")
        if not self._slider.isSliderDown():  # keyboard / click-to-jump
            self._schedule_render()

    def _schedule_render(self, *_):
        self._timer.start()

    def _on_canvas_changed(self):
        if self.canvas.mode == "blur" and self.canvas.blur_rect and self._blur_mode != "off":
            self._blur_mode = "custom"
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
                self._blur_mode = "auto"
        else:
            self.canvas.sub_pos = None
        self._schedule_render()

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
