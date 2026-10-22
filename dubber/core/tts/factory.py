"""Pick the TTS engine (Piper / VieNeu-TTS) for a job."""
from __future__ import annotations

from pathlib import Path

from dubber.core.tts.base import TTSEngine

TTS_ENGINES = ("piper", "vieneu")


def make_tts_engine(engine: str, voice: str | None, tts_dir: Path) -> TTSEngine:
    """engine "piper": voice = model stem (None → default model).
    engine "vieneu": voice = "preset:<name>" | "clone:<voice_id>" (None → default voice).
    """
    if engine == "piper":
        from dubber.core.asr.model_manager import find_piper_model
        from dubber.core.tts.piper_tts import PiperTTSEngine
        return PiperTTSEngine(model_path=find_piper_model(voice), tts_dir=tts_dir)
    if engine == "vieneu":
        from dubber.core.tts.vieneu_tts import VieNeuTTSEngine
        return VieNeuTTSEngine(voice=voice, tts_dir=tts_dir)
    raise ValueError(f"Unknown TTS engine: {engine!r}")
