"""One queued video as a two-line card: name + facts, then state + the actions that fit it."""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

_TXT_MAIN = "#ddd"
_TXT_MID = "#999"
_GREEN = "#4caf50"
_AMBER = "#ffb74d"
_RED = "#f44336"
_ACCENT = "#4db8ff"

_BTN = ("QPushButton { background: #262626; color: #ccc; border: 1px solid #333;"
        " border-radius: 4px; font-size: 11px; padding: 1px 8px; }"
        " QPushButton:hover { background: #303030; color: #fff; }")

_TRANSLATORS = {"auto": "Tự động", "groq": "Groq", "local": "MarianMT", "nllb": "NLLB"}
_WAITING = {"review": "Đang chờ bạn duyệt bản dịch",
            "rate_limit": "Groq rate limit — chờ bạn chọn cách xử lý"}

# Actions per state: (key, button text, tooltip)
ACTIONS = {
    "pending": [("remove", "✕", "Xóa khỏi hàng đợi")],
    "running": [("cancel", "⊘ Hủy", "Dừng ngay video này và xóa file tạm của nó")],
    "done": [("play", "▶ Xem", "Mở video kết quả"),
             ("tiktok", "📋 TikTok", "Copy prompt TikTok (kèm lời thoại) để dán vào ChatGPT"),
             ("reveal", "📂", "Hiện video trong "
                         + ("Explorer" if sys.platform == "win32" else "Finder")),
             ("rerun", "↻", "Chạy lại video này")],
    "error": [("show_error", "Xem lỗi", "Xem chi tiết lỗi"),
              ("rerun", "↻ Thử lại", "Đưa video về hàng đợi để chạy lại")],
    "cancelled": [("rerun", "↻", "Chạy lại video này"), ("remove", "✕", "Xóa khỏi hàng đợi")],
}
ICONS = {"pending": "⏳", "running": "▶", "done": "✓", "error": "✗", "cancelled": "⊘"}
ICON_COLORS = {"pending": _TXT_MID, "running": _ACCENT, "done": _GREEN, "error": _RED,
               "cancelled": _TXT_MID}


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def _duration(sec: float) -> str:
    s = int(round(sec))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def voice_name(engine: str, voice: "str | None") -> str:
    if not voice:
        return "giọng mặc định"
    if voice.startswith("preset:"):
        return voice.split(":", 1)[1]
    if voice.startswith("clone:"):
        return "giọng clone"
    name = voice.removeprefix("vi_VN-")
    for tail in ("-medium", "-low", "-high", "-x_low"):
        name = name.removesuffix(tail)
    return name.replace("_", " ")


def config_summary(opts: dict) -> str:
    """"Groq · VieNeu Thiện Minh · duyệt" — what this video will run with."""
    engine = opts.get("tts_engine", "piper")
    parts = [_TRANSLATORS.get(opts.get("translator"), opts.get("translator") or ""),
             f"{'VieNeu' if engine == 'vieneu' else 'Piper'} {voice_name(engine, opts.get('voice'))}"]
    if not opts.get("skip_review", True):
        parts.append("duyệt bản dịch")
    if opts.get("trim_on"):
        parts.append("chỉ một đoạn")
    return " · ".join(p for p in parts if p)


def video_facts(info) -> str:
    if info is None:
        return ""
    bits = []
    if info.duration:
        bits.append(_duration(info.duration))
    if info.height:
        bits.append(f"{min(info.width, info.height)}p")
    if info.size_bytes:
        bits.append(_size(info.size_bytes))
    return " · ".join(bits)


class QueueCard(QWidget):
    """Shows one _Job; ``action`` carries the key of the button pressed."""
    action = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, False)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 5, 8, 5)
        lay.setSpacing(2)

        top = QHBoxLayout(); top.setSpacing(8)
        self._icon = QLabel(self)
        self._icon.setFixedWidth(16)
        self._name = QLabel(self)
        self._name.setStyleSheet(f"color: {_TXT_MAIN}; font-size: 12px; font-weight: 600;")
        self._name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)   # elided to fit
        self._facts = QLabel(self)
        self._facts.setStyleSheet(f"color: {_TXT_MID}; font-size: 11px;")
        top.addWidget(self._icon)
        top.addWidget(self._name, 1)
        top.addWidget(self._facts)
        lay.addLayout(top)

        bottom = QHBoxLayout(); bottom.setSpacing(6)
        bottom.addSpacing(24)
        self._state = QLabel(self)
        self._state.setStyleSheet(f"color: {_TXT_MID}; font-size: 11px;")
        self._state.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        bottom.addWidget(self._state, 1)
        self._btns = QHBoxLayout(); self._btns.setSpacing(4)
        bottom.addLayout(self._btns)
        lay.addLayout(bottom)

        for w in (self._icon, self._name, self._facts, self._state):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)   # clicks select the row
        self._full_name = ""
        self._buttons_for = None

    # ── content ───────────────────────────────────────────────────────────────

    def show_job(self, job, state_text: str = "") -> None:
        status = job.status
        waiting = getattr(job, "waiting", "")
        icon = "✋" if waiting else ICONS[status]
        color = _AMBER if waiting else ICON_COLORS[status]
        self._icon.setText(icon)
        self._icon.setStyleSheet(f"color: {color}; font-size: 13px;")
        self._full_name = job.path.name
        self._elide_name()
        self.setToolTip(str(job.path))
        facts = video_facts(job.info)
        if not job.probed() and status == "pending":
            facts = "đang đọc…"
        self._facts.setText(facts)

        if waiting:
            text, tcolor = "✋ " + _WAITING.get(waiting, "Đang chờ bạn"), _AMBER
        elif status == "pending":
            text, tcolor = "Chờ chạy · " + config_summary(job.opts), _TXT_MID
        elif status == "running":
            text, tcolor = state_text or "Đang bắt đầu…", _ACCENT
        elif status == "done":
            text, tcolor = state_text or "Xong", _GREEN
        elif status == "error":
            text, tcolor = f"Lỗi: {job.error}", _RED
        else:
            text, tcolor = "Đã hủy", _TXT_MID
        self._state.setText(text)
        self._state.setToolTip(text)
        self._state.setStyleSheet(f"color: {tcolor}; font-size: 11px;")
        self._set_buttons("waiting" if waiting else status)

    def _set_buttons(self, state: str) -> None:
        if state == self._buttons_for:
            return
        self._buttons_for = state
        while self._btns.count():
            w = self._btns.takeAt(0).widget()
            if w is not None:
                w.hide()            # gone now, even before the deferred delete runs
                w.deleteLater()
        for key, text, tip in ACTIONS.get(state, []):
            b = QPushButton(text, self)
            b.setToolTip(tip)
            b.setFixedHeight(22)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(_BTN)
            b.setObjectName(f"card_{key}")
            b.clicked.connect(lambda _=False, k=key: self.action.emit(k))
            self._btns.addWidget(b)

    def button(self, key: str) -> "QPushButton | None":
        for i in range(self._btns.count()):
            w = self._btns.itemAt(i).widget()
            if w is not None and w.objectName() == f"card_{key}":
                return w
        return None

    def text(self) -> str:
        """Everything the card shows (name, facts, state) — for tests / accessibility."""
        return " ".join((self._full_name, self._facts.text(), self._state.text()))

    def _elide_name(self) -> None:
        width = max(60, self._name.width())
        self._name.setText(self._name.fontMetrics().elidedText(
            self._full_name, Qt.ElideMiddle, width))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide_name()
