"""Trim range editor: a two-handle slider over the video's length + two second inputs.

Dragging a handle updates the numbers and typing a number moves the handle. Values are
kept valid automatically: inside [0, video length], start < end with at least MIN_GAP
seconds between them. Any number can be typed; on Enter / leaving the field the value
just typed is corrected (start after end → just before end, end past the video → end
of video, …).
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QValidator
from PySide6.QtWidgets import (
    QAbstractSpinBox, QDoubleSpinBox, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

MIN_GAP = 0.5          # seconds — shortest piece that can be processed
UNKNOWN_MAX = 9999.0   # video length not known yet (no video selected)
_TYPE_MAX = 999_999.0  # inputs accept any number; it is corrected after typing

_ACCENT = QColor("#4db8ff")
_GROOVE = QColor("#2a2a2a")
_DISABLED = QColor("#3a3a3a")


def fmt_time(t: float) -> str:
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m):02d}:{s:04.1f}"


class SecondsSpinBox(QDoubleSpinBox):
    """Seconds input that reads both "12.5" and "12,5" whatever the system locale
    (a Vietnamese locale would otherwise read "12.5" as 125) and shows "12.5 s"."""

    def textFromValue(self, value: float) -> str:
        return f"{value:.{self.decimals()}f}"

    def valueFromText(self, text: str) -> float:
        t = text.replace(self.suffix(), "").strip().replace(",", ".")
        try:
            return float(t) if t else 0.0
        except ValueError:
            return self.value()

    def validate(self, text: str, pos: int):
        t = text.replace(self.suffix(), "").strip().replace(",", ".")
        if t in ("", "."):
            return QValidator.Intermediate, text, pos
        try:
            float(t)
        except ValueError:
            return QValidator.Invalid, text, pos
        return QValidator.Acceptable, text, pos


class RangeSlider(QWidget):
    """Horizontal slider with a low and a high handle (float values)."""

    rangeChanged = Signal(float, float)   # while dragging

    _R = 7   # handle radius

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(24)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self._max = 1.0
        self._lo, self._hi = 0.0, 1.0
        self._drag = None   # "lo" | "hi"

    # ── values ────────────────────────────────────────────────────────────────

    def set_maximum(self, maximum: float) -> None:
        self._max = max(maximum, MIN_GAP)
        self.update()

    def set_values(self, lo: float, hi: float) -> None:
        self._lo, self._hi = lo, hi
        self.update()

    def values(self) -> tuple[float, float]:
        return self._lo, self._hi

    # ── geometry ──────────────────────────────────────────────────────────────

    def _x(self, v: float) -> float:
        span = self.width() - 2 * self._R
        return self._R + span * (v / self._max if self._max else 0)

    def _value_at(self, x: float) -> float:
        span = max(1, self.width() - 2 * self._R)
        return min(self._max, max(0.0, (x - self._R) / span * self._max))

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cy = self.height() / 2
        p.setPen(Qt.NoPen)
        p.setBrush(_GROOVE)
        p.drawRoundedRect(QRectF(self._R, cy - 2, self.width() - 2 * self._R, 4), 2, 2)
        color = _ACCENT if self.isEnabled() else _DISABLED
        x_lo, x_hi = self._x(self._lo), self._x(self._hi)
        p.setBrush(color)
        p.drawRect(QRectF(x_lo, cy - 2, x_hi - x_lo, 4))
        p.setPen(QPen(QColor("#111"), 1.5))
        for x in (x_lo, x_hi):
            p.drawEllipse(QPointF(x, cy), self._R, self._R)

    # ── mouse ─────────────────────────────────────────────────────────────────

    def mousePressEvent(self, e):
        if not self.isEnabled() or e.button() != Qt.LeftButton:
            return
        x = e.position().x()
        d_lo, d_hi = abs(x - self._x(self._lo)), abs(x - self._x(self._hi))
        if d_lo == d_hi:   # handles on top of each other: pick by direction
            self._drag = "lo" if x < self._x(self._lo) else "hi"
        else:
            self._drag = "lo" if d_lo < d_hi else "hi"
        self._move_to(x)

    def mouseMoveEvent(self, e):
        if self._drag:
            self._move_to(e.position().x())

    def mouseReleaseEvent(self, _e):
        self._drag = None

    def _move_to(self, x: float) -> None:
        v = self._value_at(x)
        if self._drag == "lo":
            self._lo = min(v, self._hi - MIN_GAP)
            self._lo = max(0.0, self._lo)
        else:
            self._hi = max(v, self._lo + MIN_GAP)
            self._hi = min(self._max, self._hi)
        self.update()
        self.rangeChanged.emit(self._lo, self._hi)


class TrimEditor(QWidget):
    """Slider + start / end inputs. ``trim()`` → (start, end | None = until the end)."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duration = 0.0   # 0 = unknown

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        self.slider = RangeSlider(self)
        self.slider.rangeChanged.connect(self._on_slider)
        lay.addWidget(self.slider)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.spin_start = SecondsSpinBox(self)
        self.spin_end = SecondsSpinBox(self)
        for sp in (self.spin_start, self.spin_end):
            sp.setDecimals(1)
            sp.setSingleStep(0.5)
            sp.setSuffix(" s")
            sp.setFixedWidth(100)
            sp.setKeyboardTracking(False)   # apply on Enter / focus out, not per keystroke
            sp.setCorrectionMode(QAbstractSpinBox.CorrectToNearestValue)
            sp.setRange(0.0, _TYPE_MAX)     # wide: don't block keystrokes, fix afterwards
        self.spin_start.valueChanged.connect(self._on_start_typed)
        self.spin_end.valueChanged.connect(self._on_end_typed)
        lbl_to = QLabel("đến", self)
        lbl_to.setStyleSheet("color: #888;")
        self.lbl_info = QLabel("", self)
        self.lbl_info.setStyleSheet("color: #555; font-size: 11px;")
        row.addWidget(self.spin_start)
        row.addWidget(lbl_to)
        row.addWidget(self.spin_end)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(self.lbl_info)

        self.set_duration(0.0)

    # ── public ────────────────────────────────────────────────────────────────

    def set_duration(self, duration: float) -> None:
        """Length of the selected video (0 = unknown); keeps the current range valid."""
        start, end = self.trim()
        self._duration = max(0.0, duration)
        self.set_trim(start, end)

    def duration(self) -> float:
        return self._duration

    def trim(self) -> tuple[float, "float | None"]:
        start, end = self.spin_start.value(), self.spin_end.value()
        if self._duration:
            return start, (None if end >= self._duration - 0.05 else end)
        return start, (end or None)   # unknown length: 0 = "hết video"

    def set_trim(self, start: float, end: "float | None") -> None:
        """Set the range; invalid values are corrected (clamped / reordered)."""
        start, end = self._valid(start or 0.0, end)
        self._apply(start, end)

    # ── internal ──────────────────────────────────────────────────────────────

    def _valid(self, start: float, end: "float | None") -> tuple[float, float]:
        d = self._duration
        if not d:   # length unknown: only keep start < end when an end is given
            start = min(max(0.0, start), UNKNOWN_MAX - MIN_GAP)
            if end:
                end = min(max(end, start + MIN_GAP), UNKNOWN_MAX)
            return start, (end or 0.0)
        end = d if (end is None or end <= 0 or end > d) else end
        start = min(max(0.0, start), d - MIN_GAP)
        if end - start < MIN_GAP:     # start after end, or too short
            if start + MIN_GAP <= d:
                end = start + MIN_GAP
            else:
                start = end - MIN_GAP
        return start, end

    def _apply(self, start: float, end: float) -> None:
        d = self._duration
        for sp in (self.spin_start, self.spin_end):
            sp.blockSignals(True)
        if d:
            self.spin_end.setSpecialValueText("")
            self.slider.set_maximum(d)
            self.slider.set_values(start, end)
        else:
            self.spin_end.setSpecialValueText("hết video")
            self.slider.set_maximum(1.0)
            self.slider.set_values(0.0, 1.0)
        self.spin_start.setValue(start)
        self.spin_end.setValue(end)
        for sp in (self.spin_start, self.spin_end):
            sp.blockSignals(False)
        self.slider.setEnabled(self.isEnabled() and bool(d))
        self._update_info()
        self.changed.emit()

    def _on_slider(self, lo: float, hi: float) -> None:
        self._apply(round(lo, 1), round(hi, 1))

    def _on_start_typed(self, start: float) -> None:
        """Start was typed: correct the start (the end stays put)."""
        end = self.spin_end.value()
        if self._duration:
            start = min(start, self._duration - MIN_GAP, end - MIN_GAP)
            self._apply(max(0.0, start), end)
        else:
            self.set_trim(start, end or None)

    def _on_end_typed(self, end: float) -> None:
        """End was typed: correct the end (the start stays put)."""
        start = self.spin_start.value()
        if self._duration:
            end = min(end, self._duration) if end > 0 else self._duration
            self._apply(start, max(end, start + MIN_GAP))
        elif end and end <= start:
            self._apply(start, start + MIN_GAP)
        else:
            self._apply(start, end)

    def _update_info(self) -> None:
        start, end = self.spin_start.value(), self.spin_end.value()
        if not self._duration:
            self.lbl_info.setText("Chọn video để đặt đoạn cắt")
            return
        tail = " (hết video)" if end >= self._duration - 0.05 else ""
        self.lbl_info.setText(f"{fmt_time(start)} → {fmt_time(end)}{tail} · dài {end - start:.1f}s")

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == e.Type.EnabledChange:
            self.slider.setEnabled(self.isEnabled() and bool(self._duration))
