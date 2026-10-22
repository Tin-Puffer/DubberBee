"""AudioExtractor: extract audio and probe video metadata using FFmpeg."""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from dubber.core.models import VideoInfo
from dubber.core.utils.ffmpeg_locator import no_window

log = logging.getLogger(__name__)


class AudioExtractor:
    def __init__(self, ffmpeg: Path, ffprobe: Path) -> None:
        self._ffmpeg = ffmpeg
        self._ffprobe = ffprobe

    def probe(self, video_path: Path) -> VideoInfo:
        """Use ffprobe to extract video duration, resolution, fps, and audio presence."""
        cmd = [
            str(self._ffprobe),
            "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            "-show_format",
            str(video_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, **no_window())
        data = json.loads(result.stdout)

        duration = float(data["format"].get("duration", 0))
        width = height = 0
        fps = 0.0
        has_audio = False
        video_bitrate_kbps = 0

        for stream in data.get("streams", []):
            codec_type = stream.get("codec_type")
            if codec_type == "video":
                width = stream.get("width", 0)
                height = stream.get("height", 0)
                r_frame_rate = stream.get("r_frame_rate", "0/1")
                num, den = r_frame_rate.split("/")
                fps = float(num) / float(den) if float(den) else 0.0
                # Prefer stream-level bitrate; fall back to total format bitrate - 128k audio
                raw = stream.get("bit_rate") or data["format"].get("bit_rate", "0")
                try:
                    bps = int(raw)
                    if bps < 10000:   # suspiciously low — use format total minus audio
                        total_bps = int(data["format"].get("bit_rate", 0))
                        bps = max(0, total_bps - 128_000)
                    video_bitrate_kbps = max(0, bps // 1000)
                except (ValueError, TypeError):
                    pass
            elif codec_type == "audio":
                has_audio = True

        if not has_audio:
            raise ValueError(
                f"No audio stream found in {video_path.name}. "
                "The video must contain audio to be dubbed."
            )

        info = VideoInfo(
            duration=duration,
            width=width,
            height=height,
            fps=fps,
            has_audio=has_audio,
            path=video_path,
            video_bitrate_kbps=video_bitrate_kbps,
        )
        log.info(
            "Probed %s: %.1fs  %dx%d  %.2f fps  %d kbps",
            video_path.name, duration, width, height, fps, video_bitrate_kbps,
        )
        return info

    def extract_audio_stereo(self, video_path: Path, output_wav: Path) -> Path:
        """Extract audio as stereo 44.1 kHz PCM WAV (for music separation)."""
        if output_wav.exists():
            log.info("Reusing cached stereo audio: %s", output_wav.name)
            return output_wav
        output_wav.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            str(self._ffmpeg), "-y",
            "-i", str(video_path),
            "-vn",
            "-ac", "2",
            "-ar", "44100",
            "-c:a", "pcm_s16le",
            str(output_wav),
        ]
        log.info("Extracting stereo audio → %s", output_wav.name)
        result = subprocess.run(cmd, capture_output=True, text=True, **no_window())
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg stereo extraction failed:\n{result.stderr[-2000:]}")
        log.info("Stereo audio: %s  (%.1f MB)", output_wav.name, output_wav.stat().st_size / 1024 / 1024)
        return output_wav

    def extract_audio(self, video_path: Path, output_wav: Path) -> Path:
        """Extract audio as mono 16 kHz PCM WAV (for ASR)."""
        output_wav.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            str(self._ffmpeg),
            "-y",
            "-i", str(video_path),
            "-vn",
            "-ac", "1",
            "-ar", "16000",
            "-c:a", "pcm_s16le",
            str(output_wav),
        ]
        log.info("Extracting audio → %s", output_wav.name)
        result = subprocess.run(cmd, capture_output=True, text=True, **no_window())
        if result.returncode != 0:
            raise RuntimeError(
                f"FFmpeg audio extraction failed:\n{result.stderr[-2000:]}"
            )
        size_kb = output_wav.stat().st_size / 1024
        log.info("Audio extracted: %s  (%.0f KB)", output_wav.name, size_kb)
        return output_wav
