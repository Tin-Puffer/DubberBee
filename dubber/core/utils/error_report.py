"""Write a self-contained error report when a job fails (for the user to read / send).

Saved to <logs dir>/errors/<time>_<video>.txt. Holds the message, the full
traceback, the job's settings, the environment and the tail of dubber.log.
Never includes environment variables (API keys live there).
"""
from __future__ import annotations

import json
import platform
import re
import sys
from datetime import datetime
from pathlib import Path

LOG_TAIL_LINES = 150


def errors_dir() -> Path:
    from dubber.core.utils.paths import logs_root
    return logs_root() / "errors"


def _log_tail(n: int = LOG_TAIL_LINES) -> str:
    from dubber.core.utils.paths import logs_root
    log = logs_root() / "dubber.log"
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "(không đọc được dubber.log)"
    return "\n".join(lines[-n:])


def _app_version() -> str:
    if getattr(sys, "frozen", False):
        return "bản đóng gói"
    try:
        import subprocess
        from dubber.core.utils.ffmpeg_locator import no_window
        root = Path(__file__).resolve().parents[3]
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=5, **no_window())
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return "?"


def build_report(video: Path, message: str, traceback_text: str, settings: dict) -> str:
    now = datetime.now()
    return "\n".join([
        "=== Dubber Bee — báo cáo lỗi ===",
        f"Thời gian : {now:%Y-%m-%d %H:%M:%S}",
        f"Video     : {video}",
        f"Lỗi       : {message}",
        "",
        "── Cấu hình đã chạy ──",
        json.dumps(settings, ensure_ascii=False, indent=1, default=str),
        "",
        "── Môi trường ──",
        f"Dubber Bee {_app_version()} · Python {platform.python_version()} · "
        f"{platform.system()} {platform.release()} ({platform.machine()})",
        "",
        "── Traceback ──",
        traceback_text.rstrip() or "(không có)",
        "",
        f"── {LOG_TAIL_LINES} dòng log cuối (dubber.log) ──",
        _log_tail(),
        "",
    ])


def write_error_report(video: Path, message: str, traceback_text: str,
                       settings: dict) -> Path:
    d = errors_dir()
    d.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^\w.-]+", "_", Path(video).stem)[:60] or "video"
    path = d / f"{datetime.now():%Y%m%d-%H%M%S}_{stem}.txt"
    path.write_text(build_report(Path(video), message, traceback_text, settings),
                    encoding="utf-8")
    return path
