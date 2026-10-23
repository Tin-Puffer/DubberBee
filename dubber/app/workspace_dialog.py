"""Where Dubber keeps its files: open each folder, or move job caches / logs elsewhere.

A folder the user picks never gets our files directly — they go into
"<picked>/Dubber workspace/jobs" or "<picked>/Dubber logs", so automatic cleaning
can never touch anything else in it.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtWidgets import (
    QDialog, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QVBoxLayout,
)

_DIM = "#888"


def open_folder(path: Path) -> None:
    """Show *path* in Finder / Explorer (created first if missing)."""
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        subprocess.Popen(["explorer", str(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class WorkspaceDialog(QDialog):
    """Rows: job caches (changeable), logs & error reports (changeable), cloned voices,
    models (open only). Changing is disabled while a video is being processed."""

    def __init__(self, busy: bool = False, on_logs_changed: Optional[Callable[[], None]] = None,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Workspace")
        self.setMinimumWidth(900)
        self._busy = busy
        self._on_logs_changed = on_logs_changed
        self._edits: dict = {}

        lay = QVBoxLayout(self)
        intro = QLabel(
            "Nơi Dubber Bee lưu file. Bấm <b>Mở</b> để xem / xóa bằng tay; <b>Đổi…</b> để dùng "
            "thư mục khác (Dubber Bee tạo thư mục con riêng bên trong, không đụng tới file khác).",
            self)
        intro.setWordWrap(True)
        lay.addWidget(intro)

        self._grid = QGridLayout()
        self._grid.setColumnStretch(1, 1)
        lay.addLayout(self._grid)
        self._row = 0
        self._add("workspace", "Cache của video", "workspace_dir",
                  note="Không tự xóa — dọn bằng nút “Dọn cache”")
        self._add("logs", "Log & báo cáo lỗi", "logs_dir", extra=("Báo cáo lỗi", "errors"))
        self._add("voices", "Giọng đã clone", None,
                  note="Xóa giọng bằng nút 🗑 trong app")
        self._add("models", "Model", None)

        if busy:
            warn = QLabel("Đang có video chạy — tạm thời không đổi được thư mục.", self)
            warn.setStyleSheet("color: #ffb74d;")
            lay.addWidget(warn)

        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton("Đóng", self)
        close.setDefault(True)
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)
        self._refresh()

    # ── rows ──────────────────────────────────────────────────────────────────

    def _add(self, kind: str, label: str, key: "str | None", note: str = "",
             extra: "tuple | None" = None):
        r = self._row
        self._grid.addWidget(QLabel(label, self), r, 0)
        edit = QLineEdit(self)
        edit.setReadOnly(True)
        edit.setMinimumWidth(420)
        self._edits[kind] = edit
        self._grid.addWidget(edit, r, 1)
        buttons = QHBoxLayout()
        b_open = QPushButton("Mở", self)
        b_open.clicked.connect(lambda: open_folder(self.path(kind)))
        buttons.addWidget(b_open)
        if extra:
            text, sub = extra
            b_extra = QPushButton(text, self)
            b_extra.clicked.connect(lambda: open_folder(self.path(kind) / sub))
            buttons.addWidget(b_extra)
        if key:
            b_change = QPushButton("Đổi…", self)
            b_change.clicked.connect(lambda: self._change(kind, key))
            b_default = QPushButton("Mặc định", self)
            b_default.clicked.connect(lambda: self._set(kind, key, None))
            for b in (b_change, b_default):
                b.setEnabled(not self._busy)
                buttons.addWidget(b)
        self._grid.addLayout(buttons, r, 2)
        self._row += 1
        if note:
            n = QLabel(note, self)
            n.setStyleSheet(f"color: {_DIM}; font-size: 11px;")
            self._grid.addWidget(n, self._row, 1)
            self._row += 1

    def path(self, kind: str) -> Path:
        from dubber.core.utils import paths
        if kind == "workspace":
            return paths.jobs_root()
        if kind == "logs":
            return paths.logs_root()
        if kind == "voices":
            from dubber.core.tts.voice_library import voices_dir
            return voices_dir()
        from dubber.core.asr.model_manager import _user_models_dir
        return _user_models_dir()

    def _refresh(self):
        for kind, edit in self._edits.items():
            edit.setText(str(self.path(kind)))
            edit.setToolTip(str(self.path(kind)))
            edit.setCursorPosition(0)

    # ── changing ──────────────────────────────────────────────────────────────

    def _change(self, kind: str, key: str):
        start = str(self.path(kind).parent)
        chosen = QFileDialog.getExistingDirectory(self, "Chọn thư mục", start)
        if chosen:
            self._set(kind, key, chosen)

    def _set(self, kind: str, key: str, value: "str | None"):
        from dubber.core.utils.paths import _is_job_dir, move_jobs
        from dubber.core.utils.settings import load_settings, save_settings
        old = self.path(kind)
        st = load_settings()
        if st.get(key) == value:
            return
        st[key] = value
        save_settings(st)
        new = self.path(kind)
        new.mkdir(parents=True, exist_ok=True)
        if kind == "workspace" and old.is_dir() and any(_is_job_dir(d) for d in old.iterdir()):
            if QMessageBox.question(
                self, "Chuyển cache",
                f"Chuyển cache hiện có sang thư mục mới?\n{new}\n\n"
                "Chọn “No” thì chúng ở lại thư mục cũ (có thể xóa bằng tay).",
            ) == QMessageBox.Yes:
                move_jobs(old, new)
        if kind == "logs" and self._on_logs_changed:
            self._on_logs_changed()
        self._refresh()
