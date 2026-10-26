"""One lock for the Apple GPU (torch MPS / CoreML) shared by threads.

Two threads submitting Metal work at once abort the whole app ("failed assertion
_status < MTLCommandBufferStatusCommitted") — e.g. the queue's stems prefetch
(audio-separator) while the running video translates with MarianMT / NLLB on MPS.
Every GPU-heavy step holds this lock for its whole run.
"""
from __future__ import annotations

import threading

gpu_lock = threading.RLock()


def release_torch_memory() -> None:
    """Give memory cached by torch (MPS / CUDA) back once a model is no longer used.

    Freed tensors otherwise stay in torch's allocator: NLLB kept ~3–7 GB through TTS and
    render. No-op when torch was never imported."""
    import gc
    import sys
    gc.collect()
    torch = sys.modules.get("torch")
    if torch is None:
        return
    try:
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            torch.mps.empty_cache()
    except Exception:   # noqa: BLE001 — best effort
        pass
