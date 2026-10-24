"""One lock for the Apple GPU (torch MPS / CoreML) shared by threads.

Two threads submitting Metal work at once abort the whole app ("failed assertion
_status < MTLCommandBufferStatusCommitted") — e.g. the queue's stems prefetch
(audio-separator) while the running video translates with MarianMT / NLLB on MPS.
Every GPU-heavy step holds this lock for its whole run.
"""
from __future__ import annotations

import threading

gpu_lock = threading.RLock()
