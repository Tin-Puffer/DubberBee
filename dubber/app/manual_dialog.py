"""Dialogs shown while a video waits on the user: manual translation, Groq rate limit."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout,
)

from dubber.app.tasks import BusyBar, TaskRunner

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
        self._count = count
        self._runner = TaskRunner(self)

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

        self._err = QLabel("", self)
        self._err.setWordWrap(True)
        self._err.setStyleSheet(f"color: {_RED};")
        self._err.setVisible(bool(error))
        if error:
            self._err.setText(f"File vừa chọn chưa dùng được: {error}")
        lay.addWidget(self._err)
        self._busy = BusyBar(self)
        lay.addWidget(self._busy)

        btns = QHBoxLayout()
        self._btn_cancel = QPushButton("Hủy video này", self)
        self._btn_cancel.clicked.connect(self.reject)
        btns.addWidget(self._btn_cancel)
        btns.addStretch(1)
        self._btn_reuse = None
        if can_reuse:
            self._btn_reuse = QPushButton("Dùng lại bản dịch trước", self)
            self._btn_reuse.setToolTip("Bản tiếng Việt đã tải lên lần trước cho đúng video + đoạn cắt này")
            self._btn_reuse.clicked.connect(self._reuse)
            btns.addWidget(self._btn_reuse)
        self._btn_pick = QPushButton("Chọn file tiếng Việt…", self)
        self._btn_pick.setDefault(True)
        self._btn_pick.clicked.connect(self._pick)
        btns.addWidget(self._btn_pick)
        lay.addLayout(btns)

    def _pick(self):
        f, _ = QFileDialog.getOpenFileName(
            self, "Chọn file tiếng Việt (.txt)", str(self._script.parent),
            "Văn bản (*.txt);;Tất cả (*)")
        if not f:
            return
        # Check the file here (worker thread) so a wrong one is reported in place
        from dubber.core.translate.manual import parse_vietnamese
        path, count = Path(f), self._count
        self._set_busy(True)
        self._err.hide()
        self._runner.run(lambda: parse_vietnamese(path, count),
                         on_done=lambda _lines: self._picked(path),
                         on_fail=self._pick_failed)

    def _picked(self, path: Path):
        self._set_busy(False)
        self.answer = path
        self.accept()

    def _pick_failed(self, msg: str):
        self._set_busy(False)
        self._err.setText(f"File vừa chọn chưa dùng được: {msg}")
        self._err.show()

    def _set_busy(self, busy: bool):
        if busy:
            self._busy.start("Đang đọc và kiểm tra file…")
        else:
            self._busy.stop()
        for b in (self._btn_pick, self._btn_cancel, self._btn_reuse):
            if b is not None:
                b.setEnabled(not busy)

    def reject(self):
        if self._runner.busy():   # wait for the file check
            return
        super().reject()

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
