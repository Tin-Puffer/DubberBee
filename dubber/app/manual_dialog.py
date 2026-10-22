"""Dialogs shown while a video waits on the user: manual translation, Groq rate limit."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout,
)

_RED = "#f44336"
_DIM = "#888"


def _reveal(path: Path) -> None:
    """Show the file in Finder / Explorer."""
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", str(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])


def _open_text(path: Path) -> None:
    """Open a .txt in the plain text editor (TextEdit / Notepad)."""
    if sys.platform == "win32":
        subprocess.Popen(["notepad", str(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-e", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class ManualTranslateDialog(QDialog):
    """Result: ``self.answer`` = Path of the Vietnamese file, "reuse", or None (cancel video)."""

    def __init__(self, video: str, script: Path, count: int, can_reuse: bool,
                 error: "str | None" = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Dịch thủ công — {video}")
        self.setMinimumWidth(520)
        self.answer = None
        self._script = Path(script)

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"Đã xuất file tiếng Trung (<b>{count} câu</b>, mỗi dòng một câu):",
                             self))
        path = QLabel(str(self._script), self)
        path.setWordWrap(True)
        path.setStyleSheet(f"color: {_DIM}; font-size: 11px;")
        lay.addWidget(path)
        row = QHBoxLayout()
        btn_open = QPushButton("Mở file", self)
        btn_open.clicked.connect(lambda: _open_text(self._script))
        btn_dir = QPushButton("Mở thư mục", self)
        btn_dir.clicked.connect(lambda: _reveal(self._script))
        row.addWidget(btn_open); row.addWidget(btn_dir); row.addStretch(1)
        lay.addLayout(row)

        how = QLabel(
            f"Dịch sang tiếng Việt và lưu thành file <b>.txt</b>: mỗi dòng một câu, "
            f"đúng thứ tự, đủ <b>{count} dòng</b> (dòng 1 là câu 1…).", self)
        how.setWordWrap(True)
        lay.addWidget(how)

        if error:
            err = QLabel(f"File vừa chọn chưa dùng được: {error}", self)
            err.setWordWrap(True)
            err.setStyleSheet(f"color: {_RED};")
            lay.addWidget(err)

        btns = QHBoxLayout()
        btn_cancel = QPushButton("Hủy video này", self)
        btn_cancel.clicked.connect(self.reject)
        btns.addWidget(btn_cancel)
        btns.addStretch(1)
        if can_reuse:
            btn_reuse = QPushButton("Dùng lại bản dịch trước", self)
            btn_reuse.setToolTip("Bản tiếng Việt đã tải lên lần trước cho đúng video + đoạn cắt này")
            btn_reuse.clicked.connect(self._reuse)
            btns.addWidget(btn_reuse)
        btn_pick = QPushButton("Chọn file tiếng Việt…", self)
        btn_pick.setDefault(True)
        btn_pick.clicked.connect(self._pick)
        btns.addWidget(btn_pick)
        lay.addLayout(btns)

    def _pick(self):
        f, _ = QFileDialog.getOpenFileName(
            self, "Chọn file tiếng Việt (.txt)", str(self._script.parent),
            "Văn bản (*.txt);;Tất cả (*)")
        if f:
            self.answer = Path(f)
            self.accept()

    def _reuse(self):
        self.answer = "reuse"
        self.accept()


def ask_rate_limit(info: dict, parent=None) -> "str | None":
    """Groq rate limit → "continue" | "local" | None (cancel the video)."""
    wait = int(round(info["wait"]))
    box = QMessageBox(parent)
    box.setWindowTitle("Groq đang giới hạn số request")
    head = ("Sau khi đợi, Groq vẫn từ chối nhiều request liên tiếp." if info.get("again")
            else "Groq báo đã vượt giới hạn số request.")
    box.setText(f"{head}\nĐã dịch {info['done']}/{info['total']} câu. "
                f"Groq yêu cầu đợi khoảng {wait} giây trước khi gọi tiếp.")
    btn_wait = box.addButton(f"Tiếp tục Groq (đợi ~{wait}s)", QMessageBox.AcceptRole)
    btn_local = box.addButton("Dịch phần còn lại bằng MarianMT", QMessageBox.AcceptRole)
    if not info.get("can_local"):
        btn_local.setEnabled(False)
        btn_local.setToolTip('Chưa cài dịch offline: pip install -e ".[offline]"')
    btn_cancel = box.addButton("Hủy video này", QMessageBox.RejectRole)
    box.setDefaultButton(btn_wait)
    box.exec()
    clicked = box.clickedButton()
    if clicked is btn_wait:
        return "continue"
    if clicked is btn_local:
        return "local"
    return None
