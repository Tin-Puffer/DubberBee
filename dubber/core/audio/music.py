"""The user's own background music, mixed into the export.

The file is copied into the app's data folder (like the logo), so the choice keeps working
when the original is moved. In the export it loops to the video's length and fades out
over the last FADE_OUT_S seconds.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

FADE_OUT_S = 2.0
EXTENSIONS = ("mp3", "m4a", "aac", "wav", "flac", "ogg", "opus")


def music_dir() -> Path:
    from platformdirs import user_data_dir
    return Path(user_data_dir("dubber", "dubber")) / "music"


def probe_duration(path: Path) -> float:
    """Seconds of audio in *path*; raises ValueError when it has no audio stream."""
    from dubber.core.utils.ffmpeg_locator import find_ffprobe, no_window
    out = subprocess.run(
        [str(find_ffprobe()), "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_type:format=duration", "-of", "default=nw=1",
         str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", **no_window())
    if "codec_type=audio" not in out.stdout:
        raise ValueError("File không có âm thanh")
    for line in out.stdout.splitlines():
        if line.startswith("duration="):
            try:
                return float(line.split("=", 1)[1])
            except ValueError:
                break
    raise ValueError("Không đọc được độ dài file nhạc")


def import_music(src: Path) -> Path:
    """Copy *src* into music_dir() (same content → same file); checks it has audio."""
    probe_duration(src)
    h = hashlib.sha1()
    with open(src, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    dst = music_dir() / f"{h.hexdigest()[:16]}{src.suffix.lower()}"
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        part = dst.with_name(dst.stem + ".part" + dst.suffix)
        shutil.copyfile(src, part)
        part.replace(dst)
    return dst
