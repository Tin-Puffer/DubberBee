"""⚙ Cài đặt: what is set once for the whole app (Groq key, notifications, chatbot
instructions, temporary files) — kept out of the per-video tabs."""
from __future__ import annotations

import sys

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout,
    QWidget,
)

from dubber.app import gui as g
from dubber.app.tasks import TaskRunner


def _btn(text: str, parent) -> QPushButton:
    b = QPushButton(text, parent)
    b.setFixedHeight(28)
    b.setStyleSheet(g._SMALL_BTN_STYLE)
    return b


class GroqKeyRow(QWidget):
    """Key source + lamp + Kiểm tra (one real 1-token request) + Đặt khóa…"""
    lamp_changed = Signal(str, str)   # (color, tooltip) — mirrored by the header chip

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self._lbl_groq = QLabel("", self)
        self._lamp_groq = QLabel("●", self)
        self._lamp_groq.setFixedWidth(16)
        self._btn_check_groq = _btn("Kiểm tra", self)
        self._btn_check_groq.setToolTip("Gửi một request dịch rất nhỏ tới Groq bằng key đang dùng: "
                                        "xanh = hoạt động, đỏ = lỗi")
        self._btn_check_groq.clicked.connect(self._check_groq_key)
        self._btn_groq = _btn("🔑 Nhập API key…", self)
        self._btn_groq.setToolTip("Nhập / xóa Groq API key (lưu trong Keychain của máy)")
        self._btn_groq.clicked.connect(self._edit_groq_key)
        lay.addWidget(self._lbl_groq, 1)
        lay.addWidget(self._lamp_groq)
        lay.addWidget(self._btn_check_groq)
        lay.addWidget(self._btn_groq)
        self._groq_runner = TaskRunner(self)
        self._refresh_groq_key()
        self._set_groq_lamp(None, "Chưa kiểm tra")

    def _refresh_groq_key(self):
        from dubber.core.utils import api_keys
        src = api_keys.source()
        if src == "app":
            text, color = f"✓ Đã lưu ({api_keys.mask(api_keys.get_saved())})", g._GREEN
        elif src == "env":
            text, color = f"Từ biến môi trường ({api_keys.mask(api_keys.current())})", g._TXT_MID
        else:
            text, color = "Chưa có key — chế độ Groq / Tự động sẽ dịch bằng MarianMT", g._AMBER
        self._lbl_groq.setText(text)
        self._lbl_groq.setStyleSheet(f"color: {color}; font-size: 12px;")

    def _edit_groq_key(self):
        from dubber.app.api_key_dialog import GroqKeyDialog
        GroqKeyDialog(self).exec()
        self._refresh_groq_key()
        self._set_groq_lamp(None, "Chưa kiểm tra")   # the key may have changed

    def _set_groq_lamp(self, ok: "bool | None", tip: str) -> None:
        """ok: True = green, False = red, None = grey (not checked yet)."""
        color = g._GREEN if ok else (g._RED if ok is False else g._TXT_MID)
        self._lamp_groq.setStyleSheet(f"color: {color}; font-size: 16px;")
        self._lamp_groq.setToolTip(tip)
        self.lamp_changed.emit(color, tip)

    def _check_groq_key(self):
        from dubber.core.utils import api_keys
        key = api_keys.current()
        if not key:
            self._set_groq_lamp(False, "Chưa có Groq API key — bấm 🔑 Nhập API key…")
            return
        if self._groq_runner.busy():
            return
        self._btn_check_groq.setEnabled(False)
        self._btn_check_groq.setText("Đang kiểm tra…")
        self._set_groq_lamp(None, "Đang kiểm tra…")
        self._groq_runner.run(lambda: api_keys.ping_groq(key),
                              on_done=lambda model: self._groq_checked(
                                  True, f"Key hoạt động — Groq đã phản hồi ({model})"),
                              on_fail=lambda msg: self._groq_checked(False, msg))

    def _groq_checked(self, ok: bool, msg: str) -> None:
        self._btn_check_groq.setEnabled(True)
        self._btn_check_groq.setText("Kiểm tra")
        self._set_groq_lamp(ok, msg)
        self._lbl_groq.setToolTip(msg)
        if not ok:
            self._lbl_groq.setText(f"✗ {msg}")
            self._lbl_groq.setStyleSheet(f"color: {g._RED}; font-size: 12px;")
        else:
            self._refresh_groq_key()


class StorageRow(QWidget):
    """Temporary files: size, where they live, empty them (never automatic)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self._lbl_storage = QLabel("", self)
        self._lbl_storage.setStyleSheet(f"color: {g._TXT_MID}; font-size: 12px;")
        self._lbl_storage.setToolTip(
            "Cache giúp chạy lại cùng video nhanh hơn (không phải nhận dạng / dịch lại).\n"
            "App không tự xóa — bấm “Dọn cache” khi cần giải phóng ổ đĩa.")
        self._btn_workspace = _btn("📁 Workspace…", self)
        self._btn_workspace.setToolTip("Mở / đổi nơi lưu cache, log, báo cáo lỗi, giọng clone")
        self._btn_workspace.clicked.connect(self._open_workspace)
        self._btn_clean = _btn("Dọn cache", self)
        self._btn_clean.clicked.connect(self._clean_storage)
        lay.addWidget(self._lbl_storage, 1)
        lay.addWidget(self._btn_workspace)
        lay.addWidget(self._btn_clean)
        self._busy = False
        self._storage_bytes = 0
        self.refresh_storage()

    def refresh_storage(self) -> None:
        from dubber.core.tts.demo import demo_dir
        from dubber.core.utils.paths import _dir_size, jobs_size
        demos = demo_dir()
        size = jobs_size() + (_dir_size(demos) if demos.is_dir() else 0)
        self._storage_bytes = size
        self._lbl_storage.setText(f"Cache: {g._fmt_bytes(size)} · không tự xóa")
        self._btn_clean.setEnabled(size > 0 and not self._busy)

    def set_busy(self, busy: bool) -> None:
        """No cleaning while a video is being processed (its files are in use)."""
        self._busy = busy
        self._btn_clean.setEnabled(not busy and self._storage_bytes > 0)

    def _open_workspace(self):
        from dubber.app.workspace_dialog import WorkspaceDialog
        dlg = WorkspaceDialog(busy=self._busy, on_logs_changed=g._setup_file_logging,
                              parent=self)
        dlg.exec()
        self.refresh_storage()

    def _clean_storage(self):
        import shutil
        from dubber.core.tts.demo import demo_dir
        from dubber.core.utils.paths import clear_jobs
        if QMessageBox.question(
            self, "Dọn cache",
            f"Xóa toàn bộ cache ({g._fmt_bytes(self._storage_bytes)})?\n"
            "Video đã xuất và giọng clone không bị ảnh hưởng; lần sau chạy lại một video "
            "sẽ phải nhận dạng và dịch lại từ đầu.",
        ) != QMessageBox.Yes:
            return
        clear_jobs()
        shutil.rmtree(demo_dir(), ignore_errors=True)
        self.refresh_storage()


class PromptRow(QWidget):
    """One editable chatbot prompt: name + default/custom state + ✎ Sửa… + ↺ Mặc định."""

    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        from dubber.app.prompt_dialog import TITLES
        self._kind = kind
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(8)
        self._name = QLabel(TITLES[kind], self)
        self._name.setStyleSheet(f"color: {g._TXT_MAIN}; font-size: 12px;")
        self._state = QLabel("", self)
        self.btn_edit = _btn("✎ Sửa…", self)
        self.btn_edit.clicked.connect(self._edit)
        self.btn_reset = _btn("↺ Mặc định", self)
        self.btn_reset.setToolTip("Đưa prompt này về bản mặc định")
        self.btn_reset.clicked.connect(self._reset)
        lay.addWidget(self._name, 1)
        lay.addWidget(self._state)
        lay.addWidget(self.btn_edit)
        lay.addWidget(self.btn_reset)
        self.refresh()

    def refresh(self) -> None:
        from dubber.core.translate.prompts import is_custom
        custom = is_custom(self._kind)
        self._state.setText("đã tùy chỉnh" if custom else "mặc định")
        self._state.setStyleSheet(f"color: {g._AMBER if custom else g._TXT_MID}; font-size: 11px;")
        self.btn_reset.setEnabled(custom)

    def _edit(self) -> None:
        from dubber.app.prompt_dialog import edit_prompt
        edit_prompt(self._kind, self)
        self.refresh()

    def _reset(self) -> None:
        from dubber.core.translate.prompts import reset_prompt
        if QMessageBox.question(self, "Khôi phục mặc định",
                                "Đưa prompt này về bản mặc định? Bản bạn đã sửa sẽ mất.") \
                != QMessageBox.Yes:
            return
        reset_prompt(self._kind)
        self.refresh()


class UpdateRow(QWidget):
    """Version + "check at start" + Kiểm tra cập nhật (the checker lives in the main window)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        from dubber import __version__
        from dubber.core.utils.settings import load_settings
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.setSpacing(8)
        self._lbl = QLabel(f"Phiên bản {__version__}", self)
        self._lbl.setStyleSheet(f"color: {g._TXT_MID}; font-size: 12px;")
        self._btn = _btn("Kiểm tra cập nhật", self)
        self._btn.clicked.connect(self._check)
        self._btn.setEnabled(False)
        row.addWidget(self._lbl, 1)
        row.addWidget(self._btn)
        v.addLayout(row)
        self._chk = QCheckBox("Tự kiểm tra bản mới mỗi khi mở app", self)
        self._chk.setChecked(load_settings().get("check_updates", True))
        self._chk.toggled.connect(self._save)
        v.addWidget(self._chk)
        self._checker = None

    def set_checker(self, checker) -> None:
        self._checker = checker
        self._btn.setEnabled(True)

    def _check(self) -> None:
        from dubber import __version__
        self._lbl.setText(f"Phiên bản {__version__} · đang kiểm tra…")
        self._checker.check(manual=True,
                            on_result=lambda t: self._lbl.setText(f"Phiên bản {__version__} · {t}"))

    def _save(self, on: bool) -> None:
        from dubber.core.utils.settings import load_settings, save_settings
        st = load_settings()
        st["check_updates"] = on
        save_settings(st)


class SettingsDialog(QDialog):
    """Kept alive by the main window (its rows hold state); shown with ⚙ Cài đặt."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cài đặt")
        self.setMinimumWidth(620)
        self.setStyleSheet(g._BASE_STYLE)
        lay = QVBoxLayout(self)
        lay.setSpacing(12)

        def section(title: str, *widgets, note: str = "") -> None:
            box = QGroupBox(title, self)
            v = QVBoxLayout(box)
            v.setContentsMargins(12, 16, 12, 10)
            for w in widgets:
                v.addWidget(w)
            if note:
                n = g._hint_label(box, note)
                v.addWidget(n)
            lay.addWidget(box)

        self.groq = GroqKeyRow(self)
        section("Groq API key (dịch online)", self.groq,
                note="Key được lưu trong Keychain của máy (Windows: Credential Manager), không nằm trong file nào của Dubber Bee.")

        from dubber.app import notify
        self._chk_notify = QCheckBox("Thông báo khi video xong, lỗi hoặc cần thao tác của bạn", self)
        self._chk_notify.setChecked(notify.enabled())
        self._chk_notify.toggled.connect(self._save_notify)
        section("Thông báo", self._chk_notify,
                note=("Âm báo + nháy thanh tác vụ" if sys.platform == "win32"
                      else "Thông báo hệ thống + biểu tượng Dock")
                     + " — chỉ khi bạn đang ở cửa sổ khác.")

        self.prompt_rows = {k: PromptRow(k, self) for k in ("review", "review_timed", "manual", "tiktok")}
        section("Prompt AI", *self.prompt_rows.values(),
                note="Tùy chỉnh prompt gửi ChatGPT / Claude / Gemini theo phong cách của bạn. "
                     "Được lưu cho các lần mở sau · ↺ Mặc định để quay về bản gốc.")

        self.storage = StorageRow(self)
        section("Cache", self.storage)

        self.updates = UpdateRow(self)
        section("Cập nhật", self.updates,
                note="Bản mới tải về từ GitHub và tự thay app cũ — cài đặt, giọng clone, "
                     "cache và dữ liệu đã tải được giữ nguyên.")

        close = QHBoxLayout(); close.addStretch(1)
        btn = _btn("Đóng", self)
        btn.clicked.connect(self.accept)
        close.addWidget(btn)
        lay.addLayout(close)

    def _save_notify(self, on: bool) -> None:
        from dubber.core.utils.settings import load_settings, save_settings
        st = load_settings()
        st["notify"] = on
        save_settings(st)

    def showEvent(self, event):
        super().showEvent(event)
        self.storage.refresh_storage()
        for row in self.prompt_rows.values():
            row.refresh()
