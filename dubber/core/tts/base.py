"""TTS abstract interface and cache-key helper."""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TTSResult:
    segment_id: int
    wav_path: Path
    duration: float  # seconds


def tts_cache_key(text: str, voice: str, rate: str, engine: str = "") -> str:
    """16-char hex hash keyed on engine + text + voice + rate.

    *engine* ("piper", "vieneu", …) keeps two engines' voices with the same name apart.
    """
    if engine:
        voice = f"{engine}:{voice}"
    h = hashlib.sha1(f"{text}\x00{voice}\x00{rate}".encode()).hexdigest()
    return h[:16]


class TTSEngine(ABC):
    """Abstract TTS engine."""

    engine_id: str = ""          # "piper", "vieneu", … — part of the TTS cache key
    supports_rate: bool = True   # synthesize_one(rate="+X%") really changes the speed

    @abstractmethod
    async def synthesize_one(
        self,
        text: str,
        segment_id: int,
        rate: str | None = None,
    ) -> TTSResult:
        """Synthesize *text* and return a TTSResult with a WAV path."""

    async def synthesize_all(
        self,
        items: list[tuple[int, str]],
        request_delay: float = 0.3,
    ) -> list[TTSResult]:
        """Synthesize all (segment_id, text) pairs with staggered concurrency.

        Staggering requests avoids rate-limit errors from cloud TTS services.
        """
        import asyncio

        tasks: list[asyncio.Task] = []
        for idx, (sid, text) in enumerate(items):
            if idx > 0:
                await asyncio.sleep(request_delay)
            tasks.append(asyncio.create_task(self.synthesize_one(text, sid)))
        return await asyncio.gather(*tasks)
