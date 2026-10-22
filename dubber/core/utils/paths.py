"""User data directory utilities using platformdirs."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from platformdirs import user_data_dir


def _app_data_dir() -> Path:
    return Path(user_data_dir("dubber", "dubber"))


# A folder picked by the user never receives our files directly: they go into a
# sub-folder of ours, so cleaning can never touch anything else in that folder.
WORKSPACE_SUBDIR = "Dubber workspace"
LOGS_SUBDIR = "Dubber logs"
_JOB_NAME = re.compile(r"[0-9a-f]{12}")


def _custom_dir(key: str) -> "Path | None":
    """User-chosen folder from settings.json (workspace_dir / logs_dir), or None."""
    try:
        from dubber.core.utils.settings import load_settings
        value = load_settings().get(key)
    except Exception:
        value = None
    return Path(value) if value else None


def default_jobs_root() -> Path:
    return _app_data_dir() / "jobs"


def default_logs_root() -> Path:
    from platformdirs import user_log_dir
    return Path(user_log_dir("dubber", "dubber"))


def jobs_root() -> Path:
    """Where job dirs live: <chosen>/Dubber workspace/jobs, or the default."""
    custom = _custom_dir("workspace_dir")
    return custom / WORKSPACE_SUBDIR / "jobs" if custom else default_jobs_root()


def logs_root() -> Path:
    """Where dubber.log and errors/ live: <chosen>/Dubber logs, or the default."""
    custom = _custom_dir("logs_dir")
    return custom / LOGS_SUBDIR if custom else default_logs_root()


def job_dir_for(video_path: Path) -> Path:
    """Stable job directory derived from the resolved video path."""
    h = hashlib.sha1(str(video_path.resolve()).encode()).hexdigest()[:12]
    return jobs_root() / h


def _is_job_dir(d: Path) -> bool:
    """Only ever delete folders that are our own job dirs."""
    return d.is_dir() and (bool(_JOB_NAME.fullmatch(d.name)) or (d / "input.json").exists())


def move_jobs(old_root: Path, new_root: Path) -> int:
    """Move the job dirs from *old_root* to *new_root* (workspace changed); returns count."""
    import shutil
    if not old_root.is_dir() or old_root.resolve() == new_root.resolve():
        return 0
    new_root.mkdir(parents=True, exist_ok=True)
    n = 0
    for d in [d for d in old_root.iterdir() if _is_job_dir(d)]:
        target = new_root / d.name
        shutil.rmtree(target, ignore_errors=True)
        shutil.move(str(d), str(target))
        n += 1
    return n


# Job cache limits — the oldest job dirs go first when any is exceeded
MAX_KEPT_JOBS = 10
MAX_JOB_AGE_DAYS = 7
MAX_JOBS_BYTES = 5 * 1024 ** 3   # 5 GB (a 1-hour video's job is ~3 GB before slimming)

# Rebuilt on every run (cheap) but large — removed once a run is over
_REBUILT_FILES = ("output.mp4", "dubbed.mp4", "audio_stereo.wav", "background_mixed.wav",
                  "dub.wav", "trimmed.part.mp4")
_REBUILT_DIRS = ("tts_speed",)


def prepare_job_dir(video_path: Path, trim: "tuple | None" = None) -> Path:
    """Job dir for *video_path*, reused only for the same source file and trim range.

    Extracted audio, separation stems and trimmed.mp4 inside are only valid for that
    exact input, so any change (file replaced, other trim) starts from an empty dir.
    Everything downstream (asr.json, translate.json, TTS) is guarded by its own
    settings signature (job_cache).
    """
    import json
    import shutil

    src = video_path.resolve()
    st = src.stat()
    sig = {"source": str(src), "size": st.st_size, "mtime_ns": st.st_mtime_ns,
           "trim": list(trim) if trim else None}
    job_dir = job_dir_for(video_path)
    marker = job_dir / "input.json"
    try:
        same = json.loads(marker.read_text(encoding="utf-8")) == sig
    except (OSError, ValueError):
        same = False
    if not same:
        shutil.rmtree(job_dir, ignore_errors=True)
        job_dir.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps(sig, ensure_ascii=False), encoding="utf-8")
    marker.touch()   # last use, for prune_jobs
    return job_dir


def slim_job_dir(job_dir: Path) -> None:
    """Drop the big intermediates a rerun rebuilds anyway; keep what is slow to redo
    (ASR, translations, TTS lines, separation stems, trimmed video)."""
    import shutil
    for name in _REBUILT_FILES:
        (job_dir / name).unlink(missing_ok=True)
    for name in _REBUILT_DIRS:
        shutil.rmtree(job_dir / name, ignore_errors=True)


def _dir_size(d: Path) -> int:
    return sum(f.stat().st_size for f in d.rglob("*") if f.is_file())


def jobs_size() -> int:
    """Bytes used by all job dirs."""
    root = jobs_root()
    return _dir_size(root) if root.is_dir() else 0


def prune_jobs(keep: int = MAX_KEPT_JOBS, max_age_days: float = MAX_JOB_AGE_DAYS,
               max_bytes: int = MAX_JOBS_BYTES) -> None:
    """Delete job dirs beyond the *keep* most recently used, older than *max_age_days*,
    and (oldest first) while all of them together exceed *max_bytes*.
    The most recently used job is always kept."""
    import shutil
    import time

    root = jobs_root()
    if not root.is_dir():
        return

    def last_use(d: Path) -> float:
        m = d / "input.json"
        return (m if m.exists() else d).stat().st_mtime

    dirs = sorted((d for d in root.iterdir() if _is_job_dir(d)), key=last_use, reverse=True)
    if not dirs:
        return
    cutoff = time.time() - max_age_days * 86400
    kept = [dirs[0]]
    for d in dirs[1:]:
        if len(kept) >= keep or last_use(d) < cutoff:
            shutil.rmtree(d, ignore_errors=True)
        else:
            kept.append(d)
    sizes = {d: _dir_size(d) for d in kept}
    total = sum(sizes.values())
    for d in reversed(kept[1:]):          # oldest first, never the newest
        if total <= max_bytes:
            break
        shutil.rmtree(d, ignore_errors=True)
        total -= sizes[d]


def clear_jobs() -> None:
    """Delete every job dir (the "Dọn ngay" button; not while a video runs)."""
    import shutil
    root = jobs_root()
    if root.is_dir():
        for d in [d for d in root.iterdir() if _is_job_dir(d)]:
            shutil.rmtree(d, ignore_errors=True)
