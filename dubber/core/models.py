"""Core data models shared across the pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class Segment:
    """A single timed speech segment flowing through the pipeline."""
    id: int
    start: float        # seconds
    end: float          # seconds
    src_text: str = ""
    vi_text: str = ""
    tts_path: Optional[Path] = None
    tts_duration: float = 0.0
    final_start: float = 0.0
    final_end: float = 0.0
    speed: float = 1.0

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class VideoInfo:
    """Metadata extracted from the source video via ffprobe."""
    duration: float     # seconds
    width: int
    height: int
    fps: float
    has_audio: bool
    path: Path
    video_bitrate_kbps: int = 0   # video stream bitrate from ffprobe (0 = unknown)


@dataclass
class JobConfig:
    """Immutable configuration for a single dubbing job."""
    input_path: Path
    job_dir: Path
    output_path: Path
    voice: str = "vi-VN-NamMinhNeural"
    asr_engine: str = "local"               # "local" | "groq"
    audio_mode: str = "duck"                # "duck" | "demucs" | "mute"
    subtitle_blur: bool = False
    blur_strength: int = 25
    font_size: int = 24
    # (x0, y0, x1, y1) normalized 0–1 relative to frame size
    subtitle_region: tuple[float, float, float, float] = (0.0, 0.8, 1.0, 1.0)
    gemini_api_key: str = ""
    groq_api_key: str = ""
    chars_per_sec: float = 14.0
