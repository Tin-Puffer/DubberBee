"""Source video properties (ffprobe) for display in the GUI."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from math import gcd
from pathlib import Path
from typing import Optional


@dataclass
class VideoInfo:
    width: int = 0              # as displayed (rotation applied)
    height: int = 0
    fps: float = 0.0
    duration: float = 0.0       # seconds
    size_bytes: int = 0
    bitrate_kbps: int = 0       # whole file
    video_codec: str = ""
    video_kbps: int = 0
    pix_fmt: str = ""
    rotation: int = 0
    audio_codec: str = ""       # "" → no audio stream
    sample_rate: int = 0
    channels: int = 0
    audio_kbps: int = 0

    @property
    def aspect(self) -> str:
        """'16:9', '9:16 (dọc)', … — common ratios snapped, others reduced."""
        if not self.width or not self.height:
            return ""
        r = self.width / self.height
        for name, val in (("16:9", 16 / 9), ("9:16", 9 / 16), ("4:3", 4 / 3), ("3:4", 3 / 4),
                          ("1:1", 1.0), ("21:9", 21 / 9)):
            if abs(r - val) < 0.02:
                return name + (" (dọc)" if r < 1 else "")
        g = gcd(self.width, self.height)
        return f"{self.width // g}:{self.height // g}" + (" (dọc)" if r < 1 else "")

    @property
    def bit_depth(self) -> int:
        return 10 if "10" in self.pix_fmt else 12 if "12" in self.pix_fmt else 8


def _int(v) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _rate(v: Optional[str]) -> float:
    try:
        f = Fraction(v)
        return float(f) if f.denominator else 0.0
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def parse_ffprobe(data: dict) -> VideoInfo:
    """Build VideoInfo from `ffprobe -show_format -show_streams -of json` output."""
    fmt = data.get("format", {})
    info = VideoInfo(
        duration=float(fmt.get("duration") or 0),
        size_bytes=_int(fmt.get("size")),
        bitrate_kbps=_int(fmt.get("bit_rate")) // 1000,
    )
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"
              and not s.get("disposition", {}).get("attached_pic")), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if v:
        rot = _int(v.get("tags", {}).get("rotate"))
        for sd in v.get("side_data_list", []) or []:
            if "rotation" in sd:
                rot = _int(sd["rotation"])
        w, h = _int(v.get("width")), _int(v.get("height"))
        if abs(rot) % 180 == 90:
            w, h = h, w
        info.width, info.height, info.rotation = w, h, rot
        info.fps = _rate(v.get("avg_frame_rate")) or _rate(v.get("r_frame_rate"))
        info.video_codec = v.get("codec_name", "")
        info.video_kbps = _int(v.get("bit_rate")) // 1000
        info.pix_fmt = v.get("pix_fmt", "")
    if a:
        info.audio_codec = a.get("codec_name", "")
        info.sample_rate = _int(a.get("sample_rate"))
        info.channels = _int(a.get("channels"))
        info.audio_kbps = _int(a.get("bit_rate")) // 1000
    return info


def probe_info(path: Path) -> VideoInfo:
    from dubber.core.utils.ffmpeg_locator import find_ffprobe, no_window
    out = subprocess.run(
        [str(find_ffprobe()), "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True, **no_window(),
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip()[-300:] or "ffprobe failed")
    return parse_ffprobe(json.loads(out.stdout or "{}"))
