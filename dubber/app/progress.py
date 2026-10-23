"""Whole-video progress and time left, from the pipeline's step signals."""
from __future__ import annotations

import time
from typing import Callable, Dict, Optional

# Share of a typical run spent in each step (separation and TTS dominate)
STEP_WEIGHTS: Dict[str, float] = {
    "Separate": 0.25, "ASR": 0.10, "Translate": 0.10, "TTS": 0.35, "Mux": 0.05, "Render": 0.15,
}
ORDER = list(STEP_WEIGHTS)
MIN_FRACTION_FOR_ETA = 0.03
MIN_SECONDS_FOR_ETA = 10.0


def fmt_clock(seconds: float) -> str:
    """1:05:09 / 4:07 / 0:12."""
    s = max(0, int(round(seconds)))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_left(seconds: float) -> str:
    """Rough time left: "~3 giờ 10 phút", "~41 phút", "dưới 1 phút"."""
    m = int(round(seconds / 60))
    if m < 1:
        return "dưới 1 phút"
    h, m = divmod(m, 60)
    return f"~{h} giờ {m} phút" if h and m else (f"~{h} giờ" if h else f"~{m} phút")


class RunProgress:
    """Fractions per step → overall %, elapsed (minus time spent waiting on the user),
    and an estimate of the time left."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._frac = {k: 0.0 for k in ORDER}
        self._step_start: Dict[str, float] = {}
        self._step_time: Dict[str, float] = {}
        self._start = clock()
        self._paused_total = 0.0
        self._paused_at: Optional[float] = None
        self.current: Optional[str] = None

    # ── events ────────────────────────────────────────────────────────────────

    def started(self, step: str) -> None:
        if step not in self._frac:
            return
        # steps before this one are over (skipped or cached when they never reported)
        for k in ORDER[:ORDER.index(step)]:
            self._frac[k] = 1.0
        self.current = step
        self._step_start[step] = self.elapsed()

    def progress(self, step: str, pct: int) -> None:
        if step in self._frac:
            self._frac[step] = max(self._frac[step], min(1.0, pct / 100))

    def done(self, step: str) -> None:
        if step in self._frac:
            self._frac[step] = 1.0
            if step in self._step_start:
                self._step_time[step] = self.elapsed() - self._step_start[step]

    def pause(self) -> None:
        """The video waits on the user (translation review): the clock stops."""
        if self._paused_at is None:
            self._paused_at = self._clock()

    def resume(self) -> None:
        if self._paused_at is not None:
            self._paused_total += self._clock() - self._paused_at
            self._paused_at = None

    # ── readings ──────────────────────────────────────────────────────────────

    def elapsed(self) -> float:
        now = self._paused_at if self._paused_at is not None else self._clock()
        return now - self._start - self._paused_total

    def fraction(self) -> float:
        return sum(STEP_WEIGHTS[k] * f for k, f in self._frac.items())

    def step_elapsed(self, step: str) -> Optional[float]:
        """Seconds the step took (done) or has taken so far (running); None if not run."""
        if step in self._step_time:
            return self._step_time[step]
        if step in self._step_start and step == self.current:
            return self.elapsed() - self._step_start[step]
        return None

    def eta(self) -> Optional[float]:
        """Seconds left, or None while there is too little to go on."""
        frac, el = self.fraction(), self.elapsed()
        if frac < MIN_FRACTION_FOR_ETA or el < MIN_SECONDS_FOR_ETA or frac >= 1.0:
            return None
        return el / frac - el
