"""Manual translation with a chatbot: Chinese lines + exact times → copy for the AI → paste
its Vietnamese lines (merged / split / timed as it chose) → the run follows them."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QPlainTextEdit,
    QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from dubber.core.translate.manual import clock, manual_text, parse_manual_reply

_AMBER = "#ffb74d"
_GREEN = "#4caf50"
_DIM = "#888"


def _table(headers: list, parent) -> QTableWidget:
    t = QTableWidget(0, len(headers), parent)
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setWordWrap(True)
    hdr = t.horizontalHeader()
    for i in range(len(headers) - 1):
        hdr.setSectionResizeMode(i, QHeaderView.ResizeToContents)
    hdr.setSectionResizeMode(len(headers) - 1, QHeaderView.Stretch)
    return t


class TimedReplyPane(QWidget):
    """Paste box for a "[start → end] câu" reply + what was understood (live).

    ``segments`` = the parsed lines; ``changed`` fires with whether they're usable.
    """

    changed = Signal(bool)

    def __init__(self, duration: float, n_source: int, text: str = "", parent=None):
        super().__init__(parent)
        self._duration, self._n_source = duration, n_source
        self.segments: list = []
        rl = QVBoxLayout(self)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(QLabel("Dán kết quả của AI vào đây:", self))
        self.edit = QPlainTextEdit(self)
        self.edit.setPlaceholderText("[00:00.02 → 00:04.10] Câu tiếng Việt thứ nhất.\n"
                                     "[00:04.30 → 00:09.80] Câu tiếng Việt thứ hai.")
        rl.addWidget(self.edit, 2)
        self.info = QLabel("", self)
        self.info.setWordWrap(True)
        rl.addWidget(self.info)
        self.table = _table(["Thời gian", "Dài", "Tiếng Việt"], self)
        rl.addWidget(self.table, 3)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self.parse)
        self.edit.textChanged.connect(self._timer.start)
        if text:
            self.edit.setPlainText(text)
            self.parse()

    def parse(self) -> None:
        text = self.edit.toPlainText()
        self.table.setRowCount(0)
        self.segments = []
        if not text.strip():
            self.info.setText("")
            self.changed.emit(False)
            return
        try:
            segs, warns = parse_manual_reply(text, self._duration)
        except ValueError as exc:
            self.info.setText(f"<span style='color:{_AMBER}'>⚠ {exc}</span>")
            self.changed.emit(False)
            return
        self.segments = segs
        self.table.setRowCount(len(segs))
        fast_n = 0
        for i, s in enumerate(segs):
            dur = s["end"] - s["start"]
            words = len(s["vi_text"].split())
            fast = dur > 0 and words / dur > 4.5            # more than ~4 words a second
            fast_n += fast
            items = [f"{clock(s['start'])} → {clock(s['end'])}", f"{dur:.1f}s", s["vi_text"]]
            for col, text_ in enumerate(items):
                it = QTableWidgetItem(text_)
                if fast:
                    it.setForeground(QColor(_AMBER))
                    it.setToolTip(f"{words} từ / {dur:.1f}s — có thể phải đọc nhanh")
                self.table.setItem(i, col, it)
        parts = [f"<span style='color:{_GREEN}'>✓ Đọc được {len(segs)} câu tiếng Việt</span>"
                 f" (từ {self._n_source} dòng tiếng Trung)"]
        if fast_n:
            parts.append(f"<span style='color:{_AMBER}'>{fast_n} câu dày chữ (tô cam) — "
                         "sẽ được đọc nhanh hơn một chút</span>")
        parts += [f"<span style='color:{_AMBER}'>⚠ {w}</span>" for w in warns]
        self.info.setText("<br>".join(parts))
        self.changed.emit(True)


class TimedReplyDialog(QDialog):
    """Review dialog's paste of a merge / split reply: preview, then apply.
    After exec() == Accepted: ``segments``."""

    def __init__(self, duration: float, n_source: int, text: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Dán kết quả AI (gộp / tách câu, có thời gian)")
        self.resize(900, 640)
        lay = QVBoxLayout(self)
        note = QLabel("Kết quả này <b>thay cách chia câu</b>: bảng duyệt sẽ dựng lại theo các "
                      "câu và thời gian của AI (Hoàn tác để quay lại).", self)
        note.setTextFormat(Qt.RichText)
        note.setWordWrap(True)
        lay.addWidget(note)
        self.pane = TimedReplyPane(duration, n_source, text, self)
        lay.addWidget(self.pane, 1)
        row = QHBoxLayout()
        row.addStretch()
        b_cancel = QPushButton("Hủy", self)
        b_cancel.clicked.connect(self.reject)
        self._ok = QPushButton("Áp dụng", self)
        self._ok.setEnabled(bool(self.pane.segments))
        self._ok.clicked.connect(self.accept)
        self.pane.changed.connect(self._ok.setEnabled)
        row.addWidget(b_cancel)
        row.addWidget(self._ok)
        lay.addLayout(row)

    @property
    def segments(self) -> list:
        return self.pane.segments


class ManualTranslateDialog(QDialog):
    """After exec() == Accepted: ``segments`` = [{start, end, vi_text}] (time order)."""

    def __init__(self, video: str, rows: list, duration: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Dịch thủ công với AI — {video}")
        self.resize(1100, 760)
        self._rows, self._duration = rows, duration

        lay = QVBoxLayout(self)
        steps = QLabel(
            "<b>1.</b> Bấm <b>📋 Sao chép cho AI</b> rồi dán vào ChatGPT (hoặc AI khác).  "
            "<b>2.</b> Dán câu trả lời của AI vào ô bên phải.  "
            "<b>3.</b> Kiểm tra bảng kết quả rồi bấm <b>Dùng kết quả này</b>.<br>"
            "<span style='color:#888'>AI được gộp / tách câu và tự đặt thời gian, khoảng nghỉ — "
            "giọng đọc và phụ đề sẽ chạy đúng theo kết quả đó.</span>", self)
        steps.setTextFormat(Qt.RichText)
        steps.setWordWrap(True)
        lay.addWidget(steps)

        split = QSplitter(Qt.Horizontal, self)
        # Left: the Chinese lines with their exact times
        left = QWidget(self)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel(f"Tiếng Trung ({len(rows)} dòng · video {clock(duration)})", self))
        self._zh = _table(["#", "Thời gian", "Tiếng Trung"], self)
        self._zh.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self._zh.setItem(i, 0, QTableWidgetItem(str(i + 1)))
            self._zh.setItem(i, 1, QTableWidgetItem(f"{clock(r['start'])} → {clock(r['end'])}"))
            self._zh.setItem(i, 2, QTableWidgetItem(" ".join((r.get("src_text") or "").split())))
        ll.addWidget(self._zh, 1)
        row_btn = QHBoxLayout()
        self._btn_copy = QPushButton("📋  Sao chép cho AI", self)
        self._btn_copy.setDefault(False)
        self._btn_copy.clicked.connect(self._copy)
        btn_prompt = QPushButton("✎ Sửa prompt…", self)
        btn_prompt.clicked.connect(self._edit_prompt)
        row_btn.addWidget(self._btn_copy, 1)
        row_btn.addWidget(btn_prompt)
        ll.addLayout(row_btn)
        self._lbl_copied = QLabel("", self)
        self._lbl_copied.setStyleSheet(f"color: {_GREEN}; font-size: 12px;")
        ll.addWidget(self._lbl_copied)
        split.addWidget(left)

        # Right: paste box + what was understood
        right = TimedReplyPane(duration, len(rows), parent=self)
        self._pane = right
        split.addWidget(right)
        split.setSizes([480, 620])
        lay.addWidget(split, 1)

        row_b = QHBoxLayout()
        row_b.addStretch()
        btn_cancel = QPushButton("Hủy (dừng video này)", self)
        btn_cancel.clicked.connect(self.reject)
        self._btn_ok = QPushButton("Dùng kết quả này", self)
        self._btn_ok.setEnabled(False)
        self._btn_ok.clicked.connect(self.accept)
        self._pane.changed.connect(self._btn_ok.setEnabled)
        row_b.addWidget(btn_cancel)
        row_b.addWidget(self._btn_ok)
        lay.addLayout(row_b)

    @property
    def segments(self) -> list:
        return self._pane.segments

    # ── actions ──────────────────────────────────────────────────────────────
    def prompt_text(self) -> str:
        return manual_text(self._rows, self._duration)

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.prompt_text())
        self._lbl_copied.setText(f"✓ Đã sao chép prompt + {len(self._rows)} dòng — "
                                 "dán vào ChatGPT")

    def _edit_prompt(self) -> None:
        from dubber.app.prompt_dialog import edit_prompt
        edit_prompt("manual", self)
