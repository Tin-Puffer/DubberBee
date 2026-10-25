"""First-run model download + in-app update (download → swap folders → restart).

Both run their download on a QThread and show a progress bar; an interrupted download
continues next time (dubber/core/utils/download.py).
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from PySide6.QtCore import QObject, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton,
    QTextBrowser, QVBoxLayout, QWidget,
)

from dubber import __version__
from dubber.app import gui as g

log = logging.getLogger(__name__)


class _Worker(QObject):
    """Runs fn(progress, cancelled) on its QThread; signals arrive on the GUI thread."""
    progress = Signal(object, object, str)   # done, total (bytes; python ints > 2 GiB)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn
        self.stop = threading.Event()

    def run(self):
        from dubber.core.utils.download import DownloadCancelled
        try:
            self.done.emit(self._fn(lambda d, t, name="": self.progress.emit(d, t, name),
                                    self.stop.is_set))
        except DownloadCancelled:
            self.failed.emit("")
        except Exception as exc:
            log.exception("Download failed")
            self.failed.emit(str(exc) or exc.__class__.__name__)


class _DownloadDialog(QDialog):
    """Message + progress bar + buttons; subclasses provide the work and the outcome."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(560)
        self.setStyleSheet(g._BASE_STYLE)
        self._lay = QVBoxLayout(self)
        self._lay.setSpacing(10)
        self._bar = QProgressBar(self)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(8)
        self._bar.hide()
        self._status = QLabel("", self)
        self._status.setWordWrap(True)
        self._status.setStyleSheet(f"color: {g._TXT_MID}; font-size: 12px;")
        self._buttons = QHBoxLayout()
        self._buttons.addStretch(1)
        self._thread: Optional[QThread] = None
        self._worker: Optional[_Worker] = None

    def _finish_layout(self) -> None:
        self._lay.addWidget(self._bar)
        self._lay.addWidget(self._status)
        self._lay.addLayout(self._buttons)

    def _button(self, text: str, slot: Callable, default: bool = False) -> QPushButton:
        b = QPushButton(text, self)
        b.setFixedHeight(30)
        if not default:
            b.setStyleSheet(g._SMALL_BTN_STYLE)
        b.setDefault(default)
        b.clicked.connect(slot)
        self._buttons.addWidget(b)
        return b

    def running(self) -> bool:
        return self._thread is not None

    def _start(self, fn) -> None:
        self._bar.setRange(0, 0)
        self._bar.show()
        self._status.setStyleSheet(f"color: {g._TXT_MID}; font-size: 12px;")
        self._status.setText("Đang kết nối…")
        self._thread, self._worker = QThread(self), _Worker(fn)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.done.connect(self._on_done_thread)
        self._worker.failed.connect(self._on_failed_thread)
        self._thread.start()

    def _cancel(self) -> None:
        if self._worker:
            self._worker.stop.set()
            self._status.setText("Đang dừng…")

    def _stop_thread(self) -> None:
        if self._thread:
            self._thread.quit()
            self._thread.wait()
            self._thread = self._worker = None

    def _on_progress(self, done: int, total: int, name: str) -> None:
        mb = 1024 * 1024
        if total:
            self._bar.setRange(0, 1000)
            self._bar.setValue(int(done * 1000 / total))
            text = f"{done / mb:,.0f} / {total / mb:,.0f} MB"
        else:
            self._bar.setRange(0, 0)
            text = f"{done / mb:,.0f} MB"
        self._status.setText(f"{text}  ·  {name}" if name else text)

    def _on_done_thread(self, result) -> None:
        self._stop_thread()
        self._bar.hide()
        self.on_done(result)

    def _on_failed_thread(self, msg: str) -> None:
        self._stop_thread()
        self._bar.hide()
        if msg:
            self._status.setStyleSheet(f"color: {g._RED}; font-size: 12px;")
            self._status.setText(f"Lỗi: {msg}\nKiểm tra mạng rồi bấm Thử lại — phần đã tải được giữ lại.")
        else:
            self._status.setText("Đã dừng — phần đã tải được giữ lại, lần sau tải tiếp.")
        self.on_failed(msg)

    def on_done(self, result) -> None: ...
    def on_failed(self, msg: str) -> None: ...

    def reject(self) -> None:   # Esc / window close while downloading = cancel first
        if self.running():
            self._cancel()
            return
        super().reject()


# ── first run: models ─────────────────────────────────────────────────────────

class ModelSetupDialog(_DownloadDialog):
    """Shown before the main window when required models are missing. Accepted = the app
    can start (optional models that could not be fetched are fetched later, on use)."""

    def __init__(self, parent=None):
        super().__init__("Tải dữ liệu lần đầu", parent)
        from dubber.core.asr.model_manager import _user_models_dir
        head = QLabel("<b>Dubber Bee cần tải dữ liệu về máy (khoảng 1,8 GB)</b>", self)
        body = g._hint_label(self, (
            "Gồm: nhận dạng giọng nói tiếng Trung, 16 giọng đọc tiếng Việt, VieNeu-TTS, "
            "tách nhạc nền. Chỉ tải <b>một lần</b> — các bản cập nhật sau dùng lại, "
            "không phải tải nữa. Bị ngắt giữa chừng thì lần sau tải tiếp.<br><br>"
            f"Lưu tại: <code>{_user_models_dir()}</code>"))
        body.setTextFormat(Qt.RichText)
        self._lay.addWidget(head)
        self._lay.addWidget(body)
        self._btn_quit = self._button("Thoát", self.reject)
        self._btn_enter = self._button("Vào app", self.accept)
        self._btn_enter.hide()
        self._btn_go = self._button("Tải về", self._go, default=True)
        self._btn_stop = self._button("Dừng", self._cancel)
        self._btn_stop.hide()
        self._finish_layout()

    def _go(self) -> None:
        from dubber.core.model_setup import download_models, missing_models
        for b in (self._btn_go, self._btn_quit, self._btn_enter):
            b.hide()
        self._btn_stop.show()
        self._start(lambda progress, cancel: download_models(
            missing_models(), progress=progress, cancel=cancel))

    def on_done(self, failed) -> None:
        from dubber.core.model_setup import missing_models
        left = missing_models(required_only=True)
        if left:
            self._status.setStyleSheet(f"color: {g._RED}; font-size: 12px;")
            self._status.setText(f"Còn thiếu {len(left)} file bắt buộc: {left[0].asset}")
            self.on_failed("missing")
            return
        if not failed:
            self.accept()
            return
        self._status.setStyleSheet(f"color: {g._AMBER}; font-size: 12px;")
        self._status.setText(
            f"Đã tải xong phần chính. Chưa tải được: {_describe(failed)}.\n"
            "Vẫn dùng app được — giọng đọc thiếu tạm không hiện; VieNeu / tách nhạc sẽ tự "
            "tải lại khi bạn dùng tới. Bấm Thử lại để tải lại ngay.")
        self.on_failed("optional")

    def on_failed(self, msg: str) -> None:
        from dubber.core.model_setup import missing_models
        self._btn_stop.hide()
        self._btn_quit.show()
        # ASR + VAD + the default voice are there: the rest can wait
        self._btn_enter.setVisible(not missing_models(required_only=True))
        self._btn_go.setText("Thử lại" if msg else "Tải tiếp")
        self._btn_go.show()


def _describe(items: list) -> str:
    from dubber.core.model_setup import PIPER_VOICES
    voices = {m.asset.split(".onnx")[0] for m in items if m.asset.split(".onnx")[0] in PIPER_VOICES}
    parts = [f"{len(voices)} giọng đọc"] if voices else []
    if any(m.unpack == "vieneu" for m in items):
        parts.append("VieNeu-TTS")
    if any(m.fallback is not None and m.unpack is None for m in items):
        parts.append("model tách nhạc")
    return ", ".join(parts) or ", ".join(m.asset for m in items)


def ensure_models() -> bool:
    """Before the main window: download models when a required one is missing (first run).
    False = the user quit."""
    from dubber.core.model_setup import missing_models
    missing = missing_models(required_only=True)
    if not missing:
        return True
    log.info("Missing models: %s", ", ".join(m.asset for m in missing))
    return ModelSetupDialog().exec() == QDialog.Accepted


# ── updates ───────────────────────────────────────────────────────────────────

class UpdateDialog(_DownloadDialog):
    """New version: notes + Cập nhật ngay / Để sau / Bỏ qua bản này."""

    def __init__(self, rel, main_window: QWidget, is_busy: Callable[[], bool]):
        super().__init__("Có bản cập nhật", main_window)
        from dubber.core import updater
        self._rel = rel
        self._main = main_window
        self._is_busy = is_busy
        mb = rel.size / 1024 / 1024
        head = QLabel(f"<b>Dubber Bee {rel.version}</b> đã có — bạn đang dùng {__version__}"
                      + (f" · tải khoảng {mb:,.0f} MB" if mb else ""), self)
        self._lay.addWidget(head)
        if rel.notes.strip():
            notes = QTextBrowser(self)
            notes.setMarkdown(rel.notes)
            notes.setOpenExternalLinks(True)
            notes.setMinimumHeight(180)
            self._lay.addWidget(notes)
        self._reason = updater.cannot_update_reason()
        if self._reason:
            self._lay.addWidget(g._hint_label(self, self._reason))
        else:
            self._lay.addWidget(g._hint_label(
                self, "App sẽ tự tải, đóng lại và mở bản mới. Cài đặt, giọng clone, cache "
                      "và dữ liệu đã tải đều được giữ nguyên."))
        self._btn_skip = self._button("Bỏ qua bản này", self._skip)
        self._btn_later = self._button("Để sau", self.reject)
        if self._reason:
            self._btn_go = self._button("Mở trang tải", self._open_page, default=True)
        else:
            self._btn_go = self._button("Cập nhật ngay", self._go, default=True)
        self._btn_stop = self._button("Dừng", self._cancel)
        self._btn_stop.hide()
        self._finish_layout()

    def _open_page(self) -> None:
        QDesktopServices.openUrl(QUrl(self._rel.page))
        self.accept()

    def _skip(self) -> None:
        from dubber.core.utils.settings import load_settings, save_settings
        st = load_settings()
        st["skip_update"] = self._rel.version
        save_settings(st)
        self.reject()

    def _go(self) -> None:
        from dubber.core import updater
        if self._is_busy():
            QMessageBox.information(self, "Đang có video chạy",
                                    "Đợi video chạy xong (hoặc dừng lại) rồi cập nhật.")
            return
        for b in (self._btn_go, self._btn_skip, self._btn_later):
            b.hide()
        self._btn_stop.show()
        rel = self._rel
        self._start(lambda progress, cancel: updater.download(
            rel, progress=lambda d, t: progress(d, t, ""), cancel=cancel))

    def on_done(self, new_app) -> None:
        from dubber.core import updater
        self._btn_stop.hide()
        if self._is_busy():   # a video was started while downloading
            self._status.setText("Đã tải xong. Đợi video chạy xong rồi bấm Cập nhật ngay.")
            self._btn_go.show()
            self._btn_later.show()
            return
        self._status.setText("Đã tải xong — đang khởi động lại…")
        try:
            updater.apply(new_app, self._rel.version)
        except Exception as exc:
            log.exception("Update apply failed")
            self._status.setStyleSheet(f"color: {g._RED}; font-size: 12px;")
            self._status.setText(f"Không cập nhật được: {exc}")
            self._btn_later.show()
            return
        self.accept()
        self._main.close()
        QApplication.quit()

    def on_failed(self, msg: str) -> None:
        self._btn_stop.hide()
        self._btn_go.setText("Thử lại" if msg else "Tải tiếp")
        self._btn_go.show()
        self._btn_later.show()


class UpdateChecker(QObject):
    """Owned by the main window: silent check at start, or on demand from ⚙ Cài đặt."""

    def __init__(self, main_window: QWidget, is_busy: Callable[[], bool]):
        super().__init__(main_window)
        from dubber.app.tasks import TaskRunner
        self._main = main_window
        self._is_busy = is_busy
        self._runner = TaskRunner(self)
        self._dialog: Optional[UpdateDialog] = None

    def check(self, manual: bool = False, on_result: Optional[Callable[[str], None]] = None):
        """manual: also report "no update" / errors through on_result(text)."""
        from dubber.core import updater
        if self._runner.busy() or (self._dialog and self._dialog.isVisible()):
            return

        def done(rel):
            from dubber.core.utils.settings import load_settings
            if rel is None:
                if on_result:
                    on_result(f"Bạn đang dùng bản mới nhất ({__version__}).")
                return
            if not manual and load_settings().get("skip_update") == rel.version:
                return
            if on_result:
                on_result(f"Có bản {rel.version}.")
            self._dialog = UpdateDialog(rel, self._main, self._is_busy)
            self._dialog.show()

        def fail(msg):
            log.warning("Update check failed: %s", msg)
            if on_result:
                on_result(f"Không kiểm tra được ({msg}).")

        self._runner.run(updater.check, on_done=done, on_fail=fail)

    def wait(self) -> None:
        self._runner.wait_all(3000)
