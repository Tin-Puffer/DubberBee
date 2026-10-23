"""Tell the user a video needs them or is done while they work in another app."""
from __future__ import annotations

import logging
import subprocess
import sys

log = logging.getLogger(__name__)

# AppleScript reads title / text as arguments: nothing in them is ever parsed as script
_MAC_SCRIPT = ("on run argv\n"
               "display notification (item 2 of argv) with title (item 1 of argv) "
               "sound name \"Glass\"\n"
               "end run")


def enabled() -> bool:
    from dubber.core.utils.settings import load_settings
    return load_settings().get("notify", True)


def notify(window, title: str, text: str) -> None:
    """System notification + Dock bounce / taskbar flash (only when the window isn't the
    one the user is looking at). Never raises."""
    try:
        if window is not None and window.isActiveWindow():
            return
        if not enabled():
            return
        from PySide6.QtWidgets import QApplication
        if window is not None:
            QApplication.alert(window, 0)   # bounce / flash until the window is activated
        if sys.platform == "darwin":
            subprocess.Popen(["osascript", "-e", _MAC_SCRIPT, title, text],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif sys.platform == "win32":
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
    except Exception:   # a missing notification must never break the queue
        log.debug("notification failed", exc_info=True)
