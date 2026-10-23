"""Questions asked while a video runs (Groq rate limit) + small file helpers."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtWidgets import QMessageBox


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


def ask_rate_limit(info: dict, parent=None) -> "str | None":
    """Groq rate limit → "continue" | "local" | None (cancel the video)."""
    wait = int(round(info["wait"]))
    box = QMessageBox(parent)
    box.setWindowTitle("Groq rate limit")
    head = ("Sau thời gian chờ, Groq vẫn từ chối nhiều request liên tiếp." if info.get("again")
            else "Groq báo đã vượt giới hạn request (rate limit).")
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
