"""Run slow work (ffprobe, ffmpeg, model calls) off the GUI thread + a small busy bar.

    runner = TaskRunner(self)
    runner.run(lambda: probe_info(path), on_done=self._show, on_fail=self._error)

Callbacks are called on the GUI thread.
"""
from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QWidget


class Task(QObject):
    """Runs fn() when its QThread starts; done(result) / failed(message).

    For callers that manage the QThread themselves (connect done/failed to bound methods
    of a GUI-thread object so they run on the GUI thread).
    """
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[[], object]):
        super().__init__()
        self._fn = fn

    def run(self):
        try:
            self.done.emit(self._fn())
        except Exception as exc:   # shown by the caller
            self.failed.emit(str(exc))


class _Task(QObject):
    """Runs fn() in its QThread and reports back with the task id (TaskRunner)."""
    done = Signal(int, object)
    failed = Signal(int, str)

    def __init__(self, tid: int, fn: Callable[[], object]):
        super().__init__()
        self._tid, self._fn = tid, fn

    def run(self):
        try:
            self.done.emit(self._tid, self._fn())
        except Exception as exc:   # shown by the caller
            self.failed.emit(self._tid, str(exc) or exc.__class__.__name__)


# Every started (thread, task) until its thread has finished — even if the TaskRunner (or
# the window owning it) is destroyed first: deleting a running QThread aborts the process.
_ALIVE: set = set()


class TaskRunner(QObject):
    """Starts each function on its own QThread; on_done / on_fail run on the GUI thread.

    The runner must live on the GUI thread (create it there): its slots are bound methods,
    so Qt queues the task's signals onto the GUI thread (and drops them if the runner is
    gone by then).
    """

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._jobs: dict[int, tuple] = {}
        self._next = 0

    def run(self, fn: Callable[[], object],
            on_done: Optional[Callable[[object], None]] = None,
            on_fail: Optional[Callable[[str], None]] = None) -> int:
        _ALIVE.difference_update({x for x in _ALIVE if x[0].isFinished()})
        tid, self._next = self._next, self._next + 1
        thread, task = QThread(), _Task(tid, fn)
        task.moveToThread(thread)
        thread.started.connect(task.run)
        task.done.connect(self._done)
        task.failed.connect(self._failed)
        # the thread stops by itself once fn() has reported back
        task.done.connect(thread.quit)
        task.failed.connect(thread.quit)
        self._jobs[tid] = (thread, task, on_done, on_fail)
        _ALIVE.add((thread, task))
        thread.start()
        return tid

    def busy(self) -> bool:
        return bool(self._jobs)

    def wait_all(self, ms: int = 10000) -> None:
        """Let running tasks finish (call before the owner is destroyed)."""
        for thread, *_ in list(self._jobs.values()):
            thread.quit()
            thread.wait(ms)

    def _finish(self, tid: int):
        thread, task, on_done, on_fail = self._jobs.pop(tid)
        thread.quit()
        thread.wait()
        _ALIVE.discard((thread, task))
        return on_done, on_fail

    def _done(self, tid: int, result):
        if tid in self._jobs:
            on_done, _ = self._finish(tid)
            if on_done:
                on_done(result)

    def _failed(self, tid: int, msg: str):
        if tid in self._jobs:
            _, on_fail = self._finish(tid)
            if on_fail:
                on_fail(msg)


class BusyBar(QWidget):
    """Thin running bar + text; hidden while idle."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(8)
        self._bar = QProgressBar(self)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(6)
        self._bar.setFixedWidth(140)
        self._label = QLabel("", self)
        self._label.setStyleSheet("color: #8ab4f8; font-size: 11px;")
        lay.addWidget(self._bar)
        lay.addWidget(self._label, 1)
        self.hide()

    def start(self, text: str) -> None:
        self._bar.setRange(0, 0)          # indeterminate
        self._label.setText(text)
        self.show()

    def set_progress(self, done: int, total: int, text: str) -> None:
        self._bar.setRange(0, max(1, total))
        self._bar.setValue(done)
        self._label.setText(text)
        self.show()

    def stop(self) -> None:
        self.hide()
