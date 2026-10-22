"""Abstract translator interface and shared types."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from dubber.core.asr.base import ASRSegment

CHARS_PER_SEC: float = 14.0


@dataclass
class TranslateSegment:
    id: int
    zh: str        # source Chinese text
    max_chars: int


@dataclass
class TranslateResult:
    id: int
    vi: str        # translated Vietnamese text


class Translator(ABC):
    # Optional checkpoint set by the pipeline: raises JobCancelled when the user cancels
    cancel_check = None

    @abstractmethod
    def translate(self, segments: List[TranslateSegment]) -> List[TranslateResult]:
        """Translate a list of segments and return results in the same order."""
        ...


def make_segments(asr: List[ASRSegment], chars_per_sec: float = CHARS_PER_SEC) -> List[TranslateSegment]:
    """Convert ASR segments to translation input, computing max_chars per segment."""
    return [
        TranslateSegment(
            id=i,
            zh=s.text,
            max_chars=max(8, round((s.end - s.start) * chars_per_sec)),
        )
        for i, s in enumerate(asr)
    ]


def write_translate_json(
    asr: List[ASRSegment],
    results: List[TranslateResult],
    path: Path,
) -> None:
    """Write translate.json: each segment has id, start, end, src_text, vi_text."""
    import json

    vi_by_id: Dict[int, str] = {r.id: r.vi for r in results}
    data = [
        {
            "id": i,
            "start": s.start,
            "end": s.end,
            "src_text": s.text,
            "vi_text": vi_by_id.get(i, ""),
        }
        for i, s in enumerate(asr)
    ]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
