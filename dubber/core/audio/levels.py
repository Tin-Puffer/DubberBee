"""Track levels in dB: every track is first brought to the same loudness (REF_LUFS), then
moved by the Audio tab's dB value — so −20 dB on the Chinese voice sounds 20 dB under the
Vietnamese voice at 0 dB in every video, whatever level the source stems came out at.

Loudness is EBU R128 integrated (FFmpeg's ebur128 filter, the measure loudnorm uses);
whole-file measurements are cached by path + size + mtime.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
from pathlib import Path
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

REF_LUFS = -16.0          # every track before its dB value (= the export's loudnorm target)
MAX_BOOST_DB = 18.0       # a near-silent stem is not raised further (noise)
MAX_CUT_DB = 18.0
SILENT_LUFS = -60.0       # quieter than this: nothing to normalize

# Defaults (dB, relative to REF_LUFS), matched on a reference narration channel: the voice
# alone on top, music ~30 dB under it while speaking (−26 here: stems are measured on their
# loud parts), the Chinese voice off by default (at ≥ −10 dB it fights the Vietnamese voice)
VI_DB, MUSIC_DB, CN_DB, BGM_DB = 0, -26, -30, -20
VI_RANGE = (-12, 6)
BG_RANGE = (-40, 6)

_I_RE = re.compile(r"I:\s+(-?[\d.]+|-inf)\s+LUFS")
_cache_lock = threading.Lock()


def db_to_gain(db: float) -> float:
    return float(10 ** (db / 20))


def gain_for(lufs: Optional[float], db: float) -> float:
    """Linear gain bringing a track measured at *lufs* to REF_LUFS + *db*."""
    if lufs is None or lufs < SILENT_LUFS:
        return db_to_gain(db)
    norm = min(MAX_BOOST_DB, max(-MAX_CUT_DB, REF_LUFS - lufs))
    return db_to_gain(norm + db)


def _parse(stderr: str) -> Optional[float]:
    found = _I_RE.findall(stderr)
    if not found or found[-1] == "-inf":
        return None
    return float(found[-1])


def _run(cmd: list, data: "bytes | None" = None) -> Optional[float]:
    from dubber.core.utils.ffmpeg_locator import no_window
    out = subprocess.run(cmd, input=data, capture_output=True, **no_window())
    if out.returncode != 0:
        log.warning("Loudness measure failed: %s", out.stderr.decode("utf-8", "replace")[-300:])
        return None
    return _parse(out.stderr.decode("utf-8", "replace"))


def _cache_path() -> Path:
    from platformdirs import user_cache_dir
    return Path(user_cache_dir("dubber", "dubber")) / "loudness.json"


def _cache_key(path: Path) -> str:
    st = path.stat()
    return f"{path.resolve()}|{st.st_size}|{st.st_mtime_ns}"


def measure_file(path: Path, ffmpeg: "Path | None" = None) -> Optional[float]:
    """Integrated loudness (LUFS) of the audio in *path*; None: silent / unreadable."""
    path = Path(path)
    try:
        key = _cache_key(path)
    except OSError:
        return None
    cache_file = _cache_path()
    with _cache_lock:
        try:
            cache = json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}
        if key in cache:
            return cache[key]
    if ffmpeg is None:
        from dubber.core.utils.ffmpeg_locator import find_ffmpeg
        ffmpeg = find_ffmpeg()
    lufs = _run([str(ffmpeg), "-hide_banner", "-nostats", "-i", str(path), "-vn",
                 "-af", "ebur128", "-f", "null", "-"])
    log.info("Loudness %s: %s LUFS", path.name, "silent" if lufs is None else f"{lufs:.1f}")
    with _cache_lock:
        try:
            cache = json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}
        cache[key] = lufs
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(cache), encoding="utf-8")
        except OSError:
            pass
    return lufs


def measure_array(a: np.ndarray, sr: int) -> Optional[float]:
    """Integrated loudness (LUFS) of float32 (frames, 2) samples."""
    if a is None or not a.size or not np.any(a):
        return None
    from dubber.core.utils.ffmpeg_locator import find_ffmpeg
    return _run([str(find_ffmpeg()), "-hide_banner", "-nostats", "-f", "f32le", "-ar", str(sr),
                 "-ac", "2", "-i", "-", "-af", "ebur128", "-f", "null", "-"],
                np.ascontiguousarray(a, dtype=np.float32).tobytes())
