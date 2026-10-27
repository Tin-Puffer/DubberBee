"""Live mix preview: a short window of a video's audio tracks, mixed at the GUI's volumes.

prepare() cuts WINDOW_S seconds of every track the export can use (original audio, music /
Chinese-voice stems, Vietnamese voice, the user's own music) into float arrays — reusing
the job cache when the video was already processed, else separating just that window and
reading sample lines with the chosen voice. mix() then combines them by the same rules as
VideoRenderer.render_final + Pipeline.run_m5, so moving a slider is heard at once. Each
track's loudness is measured (whole file when there is one, like the export) so the dB
levels mean the same as in the export (core.audio.levels).
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Optional

import numpy as np

log = logging.getLogger(__name__)

SR = 48000            # the export's audio rate (render resamples to 48 kHz)
WINDOW_S = 20.0
KEEP_WINDOWS = 10     # separated windows kept in the preview cache
TARGET_DB = -16.0     # the export's loudnorm target (I=-16), approximated by RMS
SAMPLE_TEXT = "Đây là giọng đọc mẫu, giúp bạn nghe thử độ to so với nhạc nền."
MAX_LINES = 8         # translated lines read for a window (no dub.wav yet)
TRACKS = ("orig", "music", "cn", "dub", "bgm")


@dataclass
class PreviewTracks:
    start: float                       # window start on the source video (s)
    frames: int
    arrays: Dict[str, np.ndarray] = field(default_factory=dict)   # name → (frames, 2)
    dub_source: str = ""               # "dub" (voice of the last run) | "lines" | "sample"
    stems_source: str = ""             # "cache" | "mdxnet" | "ffmpeg" | "" (none)
    lufs: Dict[str, Optional[float]] = field(default_factory=dict)   # name → loudness

    def get(self, name: str) -> Optional[np.ndarray]:
        return self.arrays.get(name)


def preview_dir() -> Path:
    from platformdirs import user_cache_dir
    return Path(user_cache_dir("dubber", "dubber")) / "mix-preview"


def window_bounds(duration: float, trim: tuple = (0.0, None)) -> tuple[float, float]:
    """(first, last) allowed window start for a video of *duration* s (trim applied)."""
    lo = max(0.0, trim[0] or 0.0)
    hi = trim[1] if trim[1] else duration
    return lo, max(lo, hi - WINDOW_S)


def decode(path: Path, start: float, dur: float, loop: bool = False) -> np.ndarray:
    """*dur* s of *path* from *start* as float32 (frames, 2) at SR, zero-padded."""
    from dubber.core.utils.ffmpeg_locator import find_ffmpeg, no_window
    cmd = [str(find_ffmpeg()), "-v", "error"]
    if loop:
        cmd += ["-stream_loop", "-1"]
    cmd += ["-ss", f"{max(0.0, start):.3f}", "-t", f"{dur:.3f}", "-i", str(path),
            "-vn", "-ac", "2", "-ar", str(SR), "-f", "f32le", "-"]
    out = subprocess.run(cmd, capture_output=True, **no_window())
    if out.returncode != 0:
        raise RuntimeError(f"FFmpeg không đọc được {Path(path).name}: "
                           f"{out.stderr.decode('utf-8', 'replace')[-300:]}")
    data = np.frombuffer(out.stdout, dtype=np.float32).reshape(-1, 2)
    return _fit(data, round(dur * SR))


def _fit(a: np.ndarray, frames: int) -> np.ndarray:
    if len(a) >= frames:
        return np.ascontiguousarray(a[:frames])
    return np.vstack([a, np.zeros((frames - len(a), 2), np.float32)])


def current_job_dir(video: Path, trim: "tuple | None") -> Optional[Path]:
    """The video's job dir when its cache belongs to this exact file + trim (read only)."""
    from dubber.core.utils.paths import input_sig, job_dir_for
    d = job_dir_for(video)
    try:
        same = json.loads((d / "input.json").read_text(encoding="utf-8")) == \
            input_sig(video, trim)
    except (OSError, ValueError):
        same = False
    return d if same else None


def prepare(video: Path, start: float, *, trim: tuple = (0.0, None), engine: str = "piper",
            voice: "str | None" = None, overlap: "float | None" = None,
            bgm_path: "str | None" = None,
            on_status: "Callable[[str], None] | None" = None) -> PreviewTracks:
    """Every track of the window [start, start + WINDOW_S) of *video* (source timeline)."""
    status = on_status or (lambda _msg: None)
    trimming = bool(trim and ((trim[0] or 0) > 0 or trim[1] is not None))
    job = current_job_dir(video, tuple(trim) if trimming else None)
    offset = (trim[0] or 0.0) if trimming else 0.0      # job files start at the trim start
    frames = round(WINDOW_S * SR)
    tr = PreviewTracks(start=start, frames=frames)

    status("Đang cắt đoạn âm thanh…")
    tr.arrays["orig"] = decode(video, start, WINDOW_S)
    files = {"orig": video}       # tracks measured on their whole file, as the export does

    sep = job / "separated" if job else None
    if sep and _cached_stems_usable(sep):
        tr.arrays["cn"] = decode(sep / "vocals.wav", start - offset, WINDOW_S)
        tr.arrays["music"] = decode(sep / "instrumental.wav", start - offset, WINDOW_S)
        files.update(cn=sep / "vocals.wav", music=sep / "instrumental.wav")
        tr.stems_source = "cache"
    else:
        try:
            vocals, music, how = _separate_window(video, start, overlap, status)
            tr.arrays["cn"], tr.arrays["music"] = vocals, music
            tr.stems_source = how
        except Exception as exc:   # still previewable: original audio for both
            log.warning("Preview separation failed: %s", exc)

    if job and (job / "dub.wav").exists():
        tr.arrays["dub"] = decode(job / "dub.wav", start - offset, WINDOW_S)
        files["dub"] = job / "dub.wav"
        tr.dub_source = "dub"
    else:
        status("Đang đọc câu mẫu bằng giọng đã chọn…")
        lines = _window_lines(job, start - offset) if job else []
        try:
            tr.arrays["dub"] = _read_lines(lines or [(0.5, SAMPLE_TEXT)], engine, voice,
                                           frames, repeat=not lines)
            tr.dub_source = "lines" if lines else "sample"
        except Exception as exc:
            log.warning("Preview voice failed: %s", exc)

    if bgm_path and Path(bgm_path).exists():
        try:
            tr.arrays["bgm"] = _music_window(Path(bgm_path), start)
            files["bgm"] = Path(bgm_path)
        except Exception as exc:
            log.warning("Preview music failed: %s", exc)

    from dubber.core.audio import levels
    status("Đang đo độ to các track…")
    for name, a in tr.arrays.items():
        try:
            tr.lufs[name] = (levels.measure_file(files[name]) if name in files
                             else levels.measure_array(a, SR))
        except Exception as exc:
            log.warning("Preview loudness of %s failed: %s", name, exc)
    return tr


def _cached_stems_usable(sep: Path) -> bool:
    from dubber.core.audio.separate import mdxnet_available, separation_method
    if not ((sep / "vocals.wav").exists() and (sep / "instrumental.wav").exists()):
        return False
    # the export redoes FFmpeg-fallback stems once MDX-Net is installed: so does the preview
    return separation_method(sep) == "mdxnet" or not mdxnet_available()


def _window_key(video: Path, start: float, overlap) -> str:
    src = video.resolve()
    st = src.stat()
    raw = f"{src}|{st.st_size}|{st.st_mtime_ns}|{start:.2f}|{overlap}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _separate_window(video: Path, start: float, overlap, status) -> tuple:
    """Separate only this window (stems cached per window in preview_dir())."""
    from dubber.core.audio.separate import DEFAULT_OVERLAP, separate_tracks, separation_method
    from dubber.core.utils.ffmpeg_locator import find_ffmpeg, no_window
    from dubber.core.utils.gpu import gpu_lock
    overlap = DEFAULT_OVERLAP if overlap is None else overlap
    d = preview_dir() / _window_key(video, start, overlap)
    d.mkdir(parents=True, exist_ok=True)
    wav = d / "window.wav"
    if not wav.exists():
        part = d / "window.part.wav"
        r = subprocess.run([str(find_ffmpeg()), "-y", "-v", "error", "-ss", f"{start:.3f}",
                            "-t", f"{WINDOW_S:.3f}", "-i", str(video), "-vn", "-ac", "2",
                            "-ar", "44100", "-c:a", "pcm_s16le", str(part)],
                           capture_output=True, **no_window())
        if r.returncode != 0:
            raise RuntimeError("FFmpeg không cắt được đoạn âm thanh")
        part.replace(wav)
    if not gpu_lock.acquire(blocking=False):
        status("Đang chờ video khác dùng GPU xong…")
        gpu_lock.acquire()
    try:
        if not (d / "separated" / "vocals.wav").exists():
            status("Đang tách nhạc đoạn này (lần đầu, khoảng 10–20 giây)…")
        vocals, music = separate_tracks(wav, d / "separated", overlap=overlap)
    finally:
        gpu_lock.release()
    (d / "used").touch()
    _prune(d.parent)
    return (decode(vocals, 0, WINDOW_S), decode(music, 0, WINDOW_S),
            separation_method(d / "separated"))


def _prune(root: Path) -> None:
    """Keep the KEEP_WINDOWS most recently used windows."""
    dirs = sorted((p for p in root.iterdir() if p.is_dir()),
                  key=lambda p: (p / "used").stat().st_mtime if (p / "used").exists() else 0,
                  reverse=True)
    for old in dirs[KEEP_WINDOWS:]:
        shutil.rmtree(old, ignore_errors=True)


def _window_lines(job: Path, start: float) -> list:
    """(seconds into the window, Vietnamese text) of the translated lines in it."""
    tj = job / "translate.json"
    try:
        rows = json.loads(tj.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for r in rows:
        t = float(r.get("start", 0)) - start
        if 0 <= t < WINDOW_S - 0.5 and (r.get("vi_text") or "").strip():
            out.append((t, r["vi_text"].strip()))
    return out[:MAX_LINES]


def _read_lines(lines: list, engine: str, voice, frames: int, repeat: bool) -> np.ndarray:
    """The lines read with the chosen voice, each at its time (no overlaps); *repeat*: the
    one sample line again and again through the window."""
    import asyncio
    from dubber.core.tts.factory import make_tts_engine
    eng = make_tts_engine(engine, voice, preview_dir() / "tts")
    results = asyncio.run(eng.synthesize_all([(i, text) for i, (_, text) in enumerate(lines)]))
    clips = {r.segment_id: decode(r.wav_path, 0, r.duration) for r in results}
    out = np.zeros((frames, 2), np.float32)
    if repeat:
        clip, t = clips[0], round(lines[0][0] * SR)
        while t < frames:
            n = min(len(clip), frames - t)
            out[t:t + n] += clip[:n]
            t += len(clip) + round(1.2 * SR)
        return out
    free = 0
    for i, (sec, _) in enumerate(lines):
        clip = clips.get(i)
        if clip is None:
            continue
        t = max(round(sec * SR), free)
        if t >= frames:
            break
        n = min(len(clip), frames - t)
        out[t:t + n] += clip[:n]
        free = t + len(clip)
    return out


def _music_window(path: Path, start: float) -> np.ndarray:
    """The user's music where it plays in the export (looped from the video's start)."""
    from dubber.core.audio.music import probe_duration
    length = probe_duration(path)
    return decode(path, start % length if length > 0 else 0.0, WINDOW_S, loop=True)


# ── Mixing ────────────────────────────────────────────────────────────────────

def track_gains(tr: PreviewTracks, v: dict) -> Dict[str, float]:
    """Gain per track for GUI levels *v* — run_m5's background rules:

    music + Chinese voice at the same level → the original track; at different levels
    → both stems; one of them → its stem. Without stems the original track stands in.
    Each gain = the track brought to levels.REF_LUFS, then moved by its dB value.

    v: {vi_on, vi, music_on, music, cn_on, cn, bgm_on, bgm} (levels in dB).
    """
    from dubber.core.audio import levels
    db = dict.fromkeys(TRACKS)
    music_on, cn_on = v.get("music_on"), v.get("cn_on")
    m, c = v.get("music", levels.MUSIC_DB), v.get("cn", levels.CN_DB)
    stems = tr.get("music") is not None and tr.get("cn") is not None
    if music_on and cn_on:
        if m == c or not stems:
            db["orig"] = m
        else:
            db["music"], db["cn"] = m, c
    elif music_on or cn_on:
        if stems:
            db["music" if music_on else "cn"] = m if music_on else c
        else:
            db["orig"] = m if music_on else c
    if v.get("vi_on", True) and tr.get("dub") is not None:
        db["dub"] = v.get("vi", levels.VI_DB)
    if v.get("bgm_on") and tr.get("bgm") is not None:
        db["bgm"] = v.get("bgm", levels.BGM_DB)
    return {name: 0.0 if d is None else levels.gain_for(tr.lufs.get(name), d)
            for name, d in db.items()}


def mix(tr: PreviewTracks, gains: Dict[str, float], i: int = 0,
        j: "int | None" = None) -> np.ndarray:
    """Frames [i, j) of the mix (no loudness step: see loudness_gain)."""
    j = tr.frames if j is None else j
    out = np.zeros((j - i, 2), np.float32)
    for name, gain in gains.items():
        a = tr.get(name)
        if gain and a is not None:
            out += a[i:j] * np.float32(gain)
    return out


def loudness_gain(tr: PreviewTracks, gains: Dict[str, float]) -> float:
    """Gain bringing the whole window near the export's loudness target, like its
    loudnorm step: turning one track down makes the rest louder, as in the video."""
    full = mix(tr, gains)
    rms = float(np.sqrt(np.mean(full ** 2))) if full.size else 0.0
    if rms < 1e-5:
        return 1.0
    return float(min(10 ** ((TARGET_DB - 20 * np.log10(rms)) / 20), 8.0))
