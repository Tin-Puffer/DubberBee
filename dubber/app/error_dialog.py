"""Pop-up shown when a video fails: the message, the full report (selectable), copy / open."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase, QGuiApplication
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout,
)

_RED = "#f44336"
_DIM = "#888"


class ErrorDialog(QDialog):
    """Non-modal: the queue keeps going while it is open."""

    def __init__(self, video: str, message: str, report_text: str = "",
                 report_path: "Path | None" = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Lỗi — {video}")
        self.resize(760, 520)
        self._text = report_text or message
        self._path = Path(report_path) if report_path else None

        lay = QVBoxLayout(self)
        head = QLabel(f"<b>{video}</b> không xử lý được:", self)
        lay.addWidget(head)
        msg = QLabel(message, self)
        msg.setWordWrap(True)
        msg.setStyleSheet(f"color: {_RED}; font-size: 13px;")
        msg.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(msg)

        self.details = QPlainTextEdit(self)
        self.details.setReadOnly(True)
        self.details.setPlainText(self._text)
        self.details.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))  # Menlo / Consolas
        lay.addWidget(self.details, 1)

        if self._path:
            where = QLabel(f"Đã lưu báo cáo lỗi: {self._path}", self)
            where.setWordWrap(True)
            where.setStyleSheet(f"color: {_DIM}; font-size: 11px;")
            where.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lay.addWidget(where)

        row = QHBoxLayout()
        self.btn_copy = QPushButton("Sao chép lỗi", self)
        self.btn_copy.clicked.connect(self.copy)
        row.addWidget(self.btn_copy)
        if self._path:
            btn_open = QPushButton("Mở file lỗi", self)
            btn_open.clicked.connect(self._open_file)
            row.addWidget(btn_open)
        row.addStretch(1)
        btn_close = QPushButton("Đóng", self)
        btn_close.setDefault(True)
        btn_close.clicked.connect(self.close)
        row.addWidget(btn_close)
        lay.addLayout(row)

    def copy(self):
        QGuiApplication.clipboard().setText(self._text)
        self.btn_copy.setText("✓ Đã sao chép")

    def _open_file(self):
        from dubber.app.manual_dialog import _open_text
        _open_text(self._path)
