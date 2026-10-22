"""Abstract ASR engine interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


@dataclass
class ASRSegment:
    start: float   # seconds
    end: float     # seconds
    text: str
    # (token, absolute start time) per character/punctuation, when the engine provides
    # them — lets long segments be split at the real punctuation time. Not written to asr.json.
    token_times: Optional[List[Tuple[str, float]]] = None


class ASREngine(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def transcribe(self, audio_path: Path) -> List[ASRSegment]:
        """Transcribe a 16 kHz mono WAV file into timed segments."""
        ...

    def full_text(self, audio_path: Path) -> str:
        return "".join(s.text for s in self.transcribe(audio_path))
