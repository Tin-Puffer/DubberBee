"""Retry decorator with exponential backoff and jitter."""
from __future__ import annotations

import logging
import random
import time
from functools import wraps
from typing import Callable, Type

log = logging.getLogger(__name__)


def retry(
    exceptions: tuple[Type[Exception], ...] = (Exception,),
    tries: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    jitter: float = 0.5,
) -> Callable:
    """Decorator: retry up to `tries` times with exponential backoff + jitter."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            wait = delay
            for attempt in range(1, tries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    if attempt == tries:
                        raise
                    sleep_time = wait + random.uniform(0, jitter)
                    log.warning(
                        "%s failed (attempt %d/%d): %s. Retrying in %.1fs…",
                        func.__name__, attempt, tries, exc, sleep_time,
                    )
                    time.sleep(sleep_time)
                    wait *= backoff
        return wrapper
    return decorator
