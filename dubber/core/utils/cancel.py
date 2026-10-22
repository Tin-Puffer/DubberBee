"""Cooperative cancellation of a running job (GUI "Hủy" button)."""
from __future__ import annotations

import threading
import time


class JobCancelled(Exception):
    """Raised at the next checkpoint after the user cancelled the job."""


class CancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self._event.is_set():
            raise JobCancelled("Đã hủy")

    def sleep(self, seconds: float) -> None:
        """time.sleep that ends early (raising JobCancelled) when cancelled."""
        if self._event.wait(max(0.0, seconds)):
            raise JobCancelled("Đã hủy")


def check(cancel_check) -> None:
    """Call an optional checkpoint callable."""
    if cancel_check is not None:
        cancel_check()


def sleep(seconds: float, cancel_sleep=None) -> None:
    """Sleep via the token's interruptible sleep when one is given."""
    if cancel_sleep is not None:
        cancel_sleep(seconds)
    else:
        time.sleep(seconds)
