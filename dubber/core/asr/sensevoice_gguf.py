"""ASR engine: SenseVoice via FunASR llama.cpp (GGUF binary, no Python deps)."""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import List

from dubber.core.asr.base import ASREngine, ASRSegment

log = logging.getLogger(__name__)

# Pattern for plain-text output: [0.00s -> 2.50s] text
_SEGMENT_RE = re.compile(r"\[\s*([\d.]+)\s*->\s*([\d.]+)\s*\]\s*(.*)")


def _parse_output(output: str) -> List[ASRSegment]:
    """Parse llama-funasr-sensevoice output (plain text or JSON)."""
    text = output.strip()
    if not text:
        return []

    # Try JSON first
    try:
        data = json.loads(text)
        if isinstance(data, list):
            segments = []
            for item in data:
                segments.append(ASRSegment(
                    start=float(item.get("start", 0)),
                    end=float(item.get("end", 0)),
                    text=str(item.get("text", item.get("txt", ""))).strip(),
                ))
            return segments
    except (json.JSONDecodeError, KeyError):
        pass

    # Try plain-text line format: [start -> end] text
    segments = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _SEGMENT_RE.match(line)
        if m:
            segments.append(ASRSegment(
                start=float(m.group(1)),
                end=float(m.group(2)),
                text=m.group(3).strip(),
            ))
        else:
            # Fallback: treat entire output as one segment
            log.debug("Unrecognized output line: %r", line[:80])

    if not segments and text:
        # Entire stdout is the transcript with no timestamps
        segments = [ASRSegment(start=0.0, end=0.0, text=text)]

    return segments


class FunASRGGUFEngine(ASREngine):
    """Offline SenseVoice-Small via FunASR llama.cpp binary + GGUF models."""

    def __init__(self, binary: Path, model_gguf: Path, vad_gguf: Path) -> None:
        self._binary = Path(binary)
        self._model = Path(model_gguf)
        self._vad = Path(vad_gguf)

    @property
    def name(self) -> str:
        return "funasr-gguf"

    def transcribe(self, audio_path: Path) -> List[ASRSegment]:
        cmd = [
            str(self._binary),
            "-m", str(self._model),
            "--vad", str(self._vad),
            "-a", str(audio_path),
        ]
        log.info("Running: %s", " ".join(cmd))
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"FunASR llama.cpp failed (exit {result.returncode}):\n"
                f"{result.stderr[-2000:]}"
            )
        segments = _parse_output(result.stdout)
        log.debug("funasr-gguf: %d segments", len(segments))
        return segments
