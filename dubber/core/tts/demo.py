"""Voice preview: one sample sentence per voice, cached so it plays instantly next time."""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from dubber.core.tts.base import tts_cache_key

DEMO_TEXT = ("Xin chào, tôi là Dubber, trợ lý lồng tiếng tự động. Bạn thấy giọng này thế nào? "
             "Nếu thích, hãy chọn tôi để đọc cho video của bạn nhé!")


def demo_dir() -> Path:
    from platformdirs import user_cache_dir
    return Path(user_cache_dir("dubber", "dubber")) / "voice-demos"


def demo_path(engine: str, voice: "str | None") -> Path:
    key = tts_cache_key(DEMO_TEXT, voice or "default", "+0%", engine=engine)
    return demo_dir() / f"{engine}_{key}.wav"


def make_demo(engine: str, voice: "str | None") -> Path:
    """WAV of DEMO_TEXT read by *voice* of *engine* ("piper" | "vieneu")."""
    from dubber.core.tts.factory import make_tts_engine

    out = demo_path(engine, voice)
    if out.exists():
        return out
    work = demo_dir() / "tmp"
    tts = make_tts_engine(engine, voice, work)
    result = asyncio.run(tts.synthesize_one(DEMO_TEXT, 0))
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(result.wav_path), out)
    shutil.rmtree(work, ignore_errors=True)
    return out
