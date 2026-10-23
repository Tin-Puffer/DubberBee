"""Set / test / remove the Groq API key (stored in the OS credential store)."""
from __future__ import annotations

from PySide6.QtCore import QThread
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout,
)

from dubber.core.utils import api_keys
from dubber.app.theme import FS_HINT, SP_L, SP_M

_GREEN = "#4caf50"
_RED = "#f44336"
_DIM = "#888"


class GroqKeyDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Groq API key")
        self.setMinimumWidth(520)
        self._task = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(SP_L, SP_L, SP_L, SP_L)
        lay.setSpacing(SP_M)
        intro = QLabel(
            "Groq cho chất lượng dịch Trung → Việt tốt nhất (cần Internet, gói miễn phí có giới hạn).<br>"
            "Lấy API key tại <a href='https://console.groq.com/keys'>console.groq.com/keys</a> "
            "→ <i>Create API Key</i>, rồi dán vào đây.", self)
        intro.setOpenExternalLinks(True)
        intro.setWordWrap(True)
        lay.addWidget(intro)

        self._status = QLabel(self)
        self._status.setWordWrap(True)
        lay.addWidget(self._status)

        row = QHBoxLayout()
        self.edit = QLineEdit(self)
        self.edit.setEchoMode(QLineEdit.Password)
        self.edit.setPlaceholderText("gsk_…")
        self.edit.textChanged.connect(self._refresh)
        self.btn_show = QPushButton("Hiện", self)
        self.btn_show.setCheckable(True)
        self.btn_show.toggled.connect(lambda on: (
            self.edit.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password),
            self.btn_show.setText("Ẩn" if on else "Hiện")))
        row.addWidget(self.edit, 1)
        row.addWidget(self.btn_show)
        lay.addLayout(row)

        self._result = QLabel("", self)
        self._result.setWordWrap(True)
        lay.addWidget(self._result)

        note = QLabel("Key được lưu trong Keychain (Windows: Credential Manager), không nằm trong "
                      "file nào của Dubber Bee và không ghi vào log.", self)
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {_DIM}; font-size: {FS_HINT}px;")
        lay.addWidget(note)

        btns = QHBoxLayout()
        self.btn_delete = QPushButton("Xóa key đã lưu", self)
        self.btn_delete.clicked.connect(self._delete)
        btns.addWidget(self.btn_delete)
        btns.addStretch(1)
        self.btn_test = QPushButton("Kiểm tra", self)
        self.btn_test.clicked.connect(self._test)
        self.btn_save = QPushButton("Lưu", self)
        self.btn_save.setDefault(True)
        self.btn_save.clicked.connect(self._save)
        close = QPushButton("Đóng", self)
        close.clicked.connect(self.reject)
        for b in (self.btn_test, self.btn_save, close):
            btns.addWidget(b)
        lay.addLayout(btns)
        self._show_status()
        self._refresh()

    # ── state ─────────────────────────────────────────────────────────────────

    def _show_status(self):
        src = api_keys.source()
        if src == "app":
            text = f"✓ Đã lưu trong Dubber Bee: {api_keys.mask(api_keys.get_saved())}"
            color = _GREEN
        elif src == "env":
            text = (f"Đang dùng key từ biến môi trường: {api_keys.mask(api_keys.current())} "
                    "(lưu vào Dubber Bee để không phụ thuộc cách mở app)")
            color = _DIM
        else:
            text, color = "Chưa có Groq API key — chế độ Groq / Tự động sẽ dịch bằng MarianMT (offline)", _RED
        self._status.setText(text)
        self._status.setStyleSheet(f"color: {color};")
        self.btn_delete.setEnabled(src == "app")

    def _refresh(self, *_):
        has = bool(self.edit.text().strip())
        busy = self._task is not None
        self.btn_save.setEnabled(has and not busy)
        self.btn_test.setEnabled(has and not busy)

    def _say(self, text: str, color: str = _DIM):
        self._result.setText(text)
        self._result.setStyleSheet(f"color: {color};")

    # ── actions ───────────────────────────────────────────────────────────────

    def _test(self):
        from dubber.app.tasks import Task as _Task
        value = self.edit.text().strip()
        self._say("Đang kiểm tra với Groq…")
        thread, task = QThread(self), _Task(lambda: api_keys.check_groq(value))
        task.moveToThread(thread)
        thread.started.connect(task.run)
        task.done.connect(self._test_ok)        # bound methods → GUI thread
        task.failed.connect(self._test_failed)
        task.done.connect(thread.quit)
        task.failed.connect(thread.quit)
        thread.finished.connect(self._test_thread_done)
        self._task = (thread, task)
        self._refresh()
        thread.start()

    def _test_ok(self, _r):
        self._say("✓ Key hoạt động", _GREEN)

    def _test_failed(self, msg: str):
        self._say(f"✗ {msg}", _RED)

    def _test_thread_done(self):
        thread, _ = self._task
        self._task = None
        thread.deleteLater()
        self._refresh()

    def _save(self):
        value = self.edit.text().strip()
        if not value:
            return
        if api_keys.save(value):
            self._say("✓ Đã lưu — dùng ngay cho các video sau", _GREEN)
        else:
            self._say("Không lưu được vào Keychain — key chỉ dùng được tới khi tắt app", _RED)
        self.edit.clear()
        self._show_status()

    def _delete(self):
        if QMessageBox.question(self, "Xóa Groq API key", "Xóa Groq API key đã lưu trong Dubber Bee?") \
                != QMessageBox.Yes:
            return
        api_keys.delete()
        self._say("Đã xóa key", _DIM)
        self._show_status()

    def done(self, result):
        if self._task is not None:     # let a running check finish first
            self._task[0].quit()
            self._task[0].wait(20_000)
        super().done(result)
