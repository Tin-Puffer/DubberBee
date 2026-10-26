"""M7: Audio source separation using MDX-Net via audio-separator (ONNX-based).

Separates audio into:
  - vocals.wav      → Chinese voice track
  - instrumental.wav → background music track

Falls back to FFmpeg center-channel cancellation if audio-separator unavailable.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import threading
from pathlib import Path

from dubber.core.utils.ffmpeg_locator import no_window
from dubber.core.utils.job_cache import sig_path, write_sig

log = logging.getLogger(__name__)

MODEL_NAME = "UVR-MDX-NET-Inst_HQ_5.onnx"   # MDX-Net ONNX, vocals + instrumental
# audio-separator's defaults; "overlap" is chosen per video (OVERLAPS)
MDX_PARAMS = {"hop_length": 1024, "segment_size": 256, "overlap": 0.25, "batch_size": 1,
              "enable_denoise": False}
# Share of each ~6 s chunk that overlaps the next one: more = smoother joins (music stem),
# slower. Seconds for a 2-min clip on an M1 Pro (CoreML): 0.1 → 20, 0.25 → 27,
# 0.5 → 33, 0.75 → 62. 0.1 audibly hurt the music; 0.5 is near 0.75 at half the time.
OVERLAPS = (0.1, 0.25, 0.5, 0.75)
DEFAULT_OVERLAP = 0.5
# Stems cached before the overlap was recorded were made with audio-separator's default
LEGACY_OVERLAP = 0.25
# Longer audio is separated in pieces of this many seconds: audio-separator holds the whole
# file's spectrogram in memory (M1 Pro: 4.9 GB for 8 s, 7.4 GB for 10 min, a 30-min video
# overflowed 16 GB RAM); in 2-min pieces 10 min stays at 5.9 GB. The joins are inaudible
CHUNK_S = 120


_locks: "dict[str, threading.RLock]" = {}
_locks_guard = threading.Lock()


def separation_lock(job_dir: Path) -> threading.RLock:
    """Lock for one job dir's stereo audio + stems (pipeline run vs queue prefetch)."""
    key = str(Path(job_dir).resolve())
    with _locks_guard:
        return _locks.setdefault(key, threading.RLock())


def separate_tracks(
    audio_path: Path,
    output_dir: Path,
    progress_cb=None,
    overlap: float = DEFAULT_OVERLAP,
) -> tuple[Path, Path]:
    """Separate audio into vocals and instrumental.

    Returns (vocals_wav, instrumental_wav).
    Caches results in output_dir — reused only when made with the same model + *overlap*.
    """
    cached = cached_stems(output_dir, overlap)
    if cached:
        log.info("Reusing cached separation: %s, %s (overlap %s)",
                 cached[0].name, cached[1].name, overlap)
        return cached

    vocals_out       = output_dir / "vocals.wav"
    instrumental_out = output_dir / "instrumental.wav"
    fallback_marker = output_dir / ".ffmpeg_fallback"
    sig = {"model": MODEL_NAME, "overlap": overlap}
    if vocals_out.exists() and instrumental_out.exists():
        log.info("Cached separation was made with other settings (or by the FFmpeg "
                 "fallback) — separating again (overlap %s)", overlap)
    for p in (vocals_out, instrumental_out, fallback_marker, sig_path(vocals_out)):
        p.unlink(missing_ok=True)

    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        from dubber.core.utils.gpu import gpu_lock
        with gpu_lock:   # one GPU user at a time (CoreML / MPS) — see utils/gpu.py
            _separate_mdxnet(audio_path, output_dir, vocals_out, instrumental_out, progress_cb,
                             overlap)
        write_sig(vocals_out, sig)
    except ImportError as exc:
        log.warning(
            "audio-separator not available (%s) — falling back to FFmpeg center-channel "
            "trick; separation will be poor (install: pip install -e \".[offline]\")", exc,
        )
        _separate_ffmpeg_fallback(audio_path, vocals_out, instrumental_out)
        fallback_marker.touch()

    return vocals_out, instrumental_out


def cached_stems(output_dir: Path, overlap: float = DEFAULT_OVERLAP
                 ) -> "tuple[Path, Path] | None":
    """(vocals, instrumental) in *output_dir* when separate_tracks would reuse them."""
    vocals, instrumental = output_dir / "vocals.wav", output_dir / "instrumental.wav"
    if not (vocals.exists() and instrumental.exists()):
        return None
    if (output_dir / ".ffmpeg_fallback").exists():
        # Don't keep reusing poor fallback stems once the real separator is installed
        return None if _mdxnet_available() else (vocals, instrumental)
    if _stems_sig(vocals) == {"model": MODEL_NAME, "overlap": overlap}:
        return vocals, instrumental
    return None


def _stems_sig(vocals: Path) -> dict:
    """Settings the cached stems were made with (stems from before this was recorded:
    the model at audio-separator's default overlap)."""
    import json
    try:
        return json.loads(sig_path(vocals).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"model": MODEL_NAME, "overlap": LEGACY_OVERLAP}


def separation_method(output_dir: Path) -> str:
    """How the cached stems in *output_dir* were made: "mdxnet" or "ffmpeg" (fallback)."""
    return "ffmpeg" if (output_dir / ".ffmpeg_fallback").exists() else "mdxnet"


def mdxnet_available() -> bool:
    """True if audio-separator (MDX-Net) can be imported."""
    return _mdxnet_available()


# Release-mirror asset names of the model + the lists audio-separator needs to identify it
UVR_FILES = {
    MODEL_NAME: MODEL_NAME,
    "download_checks.json": "uvr-download_checks.json",
    "mdx_model_data.json": "uvr-mdx_model_data.json",
    "vr_model_data.json": "uvr-vr_model_data.json",
}


def download_uvr_model(dest: "Path | None" = None) -> Path:
    """Make sure the MDX-Net model + lists are in *dest* (default: the app's UVR dir):
    release mirror first, audio-separator's own download otherwise. Returns the dir."""
    from dubber.core.asr.model_manager import MODELS_MIRROR_URL
    from dubber.core.utils.download import fetch

    d = dest or _uvr_model_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        for local, asset in UVR_FILES.items():
            if not (d / local).is_file():
                fetch(f"{MODELS_MIRROR_URL}/{asset}", d / local)
    except Exception as exc:
        log.warning("UVR mirror unavailable (%s) — downloading via audio-separator", exc)
    if not all((d / f).is_file() for f in UVR_FILES):
        download_uvr_upstream(d)
    return d


def download_uvr_upstream(d: "Path | None" = None) -> Path:
    """audio-separator's own download (its model repo) — the mirror's original source."""
    import tempfile
    from audio_separator.separator import Separator
    d = d or _uvr_model_dir()
    d.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as out:
        # load_model downloads the model and the lists it needs to identify it
        Separator(output_dir=out, model_file_dir=str(d),
                  log_level=logging.WARNING).load_model(MODEL_NAME)
    return d


def _mdxnet_available() -> bool:
    # Only looked up, not imported: importing it loads torch (~270 MB) into the GUI, while
    # the separation itself runs in a child process. A broken install raises ImportError
    # there, and separate_tracks falls back to FFmpeg
    from importlib.util import find_spec
    try:
        return find_spec("audio_separator") is not None
    except (ImportError, ValueError):
        return False


def _uvr_model_dir() -> Path:
    """Persistent directory for UVR MDX-Net model files (survives across runs)."""
    import sys
    if getattr(sys, 'frozen', False):
        # Bundled exe: resources/models/uvr/ inside PyInstaller's _internal/ when the build
        # carries models, else the user-data models dir (downloaded on first run)
        bundled = Path(sys._MEIPASS) / "resources" / "models" / "uvr"
        if (bundled / MODEL_NAME).is_file():
            return bundled
        from dubber.core.asr.model_manager import _user_models_dir
        return _user_models_dir() / "uvr"
    try:
        from platformdirs import user_cache_dir
        return Path(user_cache_dir("dubber", "dubber")) / "uvr-models"
    except ImportError:
        # Fallback to same drive as Python (avoids /tmp on Windows)
        import sys as _sys
        return Path(_sys.executable).parent.parent / "uvr-models"


# Separation runs in a child process: audio-separator + CoreML need ~5 GB at their peak,
# and ~1.6 GB of it stayed in the app until it was closed. When the child exits it is all
# given back, and a cancel stops the child at once. Tests turn it off
SEPARATE_IN_CHILD = True


def _separate_mdxnet(
    audio_path: Path,
    output_dir: Path,
    vocals_out: Path,
    instrumental_out: Path,
    progress_cb=None,
    overlap: float = DEFAULT_OVERLAP,
) -> None:
    """MDX-Net separation in a child process; progress / errors / log lines come back.

    *progress_cb* is also called (with the last value) every half second, so a cancel
    check in it can stop the child. Raises ImportError when audio-separator can't load.
    """
    args = (audio_path, output_dir, vocals_out, instrumental_out, overlap)
    if not SEPARATE_IN_CHILD:
        _separate_mdxnet_here(*args[:4], progress_cb, overlap)
        return
    if not _mdxnet_available():
        raise ImportError("audio_separator is not installed")
    import multiprocessing
    import queue as _queue

    ctx = multiprocessing.get_context("spawn")
    q = ctx.Queue()
    proc = ctx.Process(target=_separate_child, args=(args, q, logging.getLogger().level),
                       name="dubber-separate")
    proc.start()
    done = False
    try:
        last = None
        while True:
            try:
                msg = q.get(timeout=0.5)
            except _queue.Empty:
                if not proc.is_alive():
                    raise RuntimeError("Music separation stopped unexpectedly "
                                       f"(exit code {proc.exitcode}) — out of memory?")
                if progress_cb and last is not None and 0 < last < 1:
                    progress_cb(last)
                continue
            if isinstance(msg, logging.LogRecord):
                logging.getLogger(msg.name).handle(msg)
                continue
            kind, value = msg
            if kind == "progress":
                last = value
                if progress_cb:
                    progress_cb(value)
            elif kind == "done":
                done = True
                break
            else:
                is_import, text = value
                raise (ImportError if is_import else RuntimeError)(text)
    finally:
        if done:
            proc.join(30)
        if proc.is_alive():
            proc.terminate()
        proc.join(5)
        q.close()


def _separate_child(args, q, log_level: int) -> None:
    """Child process: run the separation, send progress / log records / result to *q*."""
    import os
    import sys
    from logging.handlers import QueueHandler
    # A windowed build has no stdout / stderr, and audio-separator's tqdm writes to them
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    root = logging.getLogger()
    root.handlers[:] = [QueueHandler(q)]
    root.setLevel(log_level)
    try:
        audio_path, output_dir, vocals_out, instrumental_out, overlap = args
        _separate_mdxnet_here(audio_path, output_dir, vocals_out, instrumental_out,
                              lambda pct: q.put(("progress", pct)), overlap)
        q.put(("done", None))
    except BaseException as exc:   # noqa: BLE001 — everything goes back to the parent
        q.put(("error", (isinstance(exc, ImportError), f"{type(exc).__name__}: {exc}")))


def _separate_mdxnet_here(
    audio_path: Path,
    output_dir: Path,
    vocals_out: Path,
    instrumental_out: Path,
    progress_cb=None,
    overlap: float = DEFAULT_OVERLAP,
) -> None:
    import os
    # audio-separator checks for ffmpeg in PATH at init time
    ffmpeg_dir = str(_find_ffmpeg().parent)
    if ffmpeg_dir not in os.environ.get("PATH", ""):
        os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")

    from audio_separator.separator import Separator

    model_dir = _uvr_model_dir()
    model_dir.mkdir(parents=True, exist_ok=True)

    log.info("Loading MDX-Net model: %s (model dir: %s)", MODEL_NAME, model_dir)
    if progress_cb:
        progress_cb(0.0)   # triggers step_started in GUI
        progress_cb(0.05)

    sep = Separator(
        output_dir=str(output_dir),
        model_file_dir=str(model_dir),
        output_format="WAV",
        log_level=logging.WARNING,
        mdx_params={**MDX_PARAMS, "overlap": overlap},
        chunk_duration=CHUNK_S,
    )
    sep.load_model(MODEL_NAME)

    if progress_cb:
        progress_cb(0.15)

    log.info("Separating: %s", audio_path.name)
    out_files = sep.separate(str(audio_path))
    log.info("Separation output: %s", out_files)

    if progress_cb:
        progress_cb(0.90)

    # audio-separator names files like: stem_(Vocals)_Model.wav
    vocals_src       = _find_stem(output_dir, "Vocals",       out_files)
    instrumental_src = _find_stem(output_dir, "Instrumental", out_files)

    if vocals_src and vocals_src != vocals_out:
        shutil.move(str(vocals_src), str(vocals_out))
    if instrumental_src and instrumental_src != instrumental_out:
        shutil.move(str(instrumental_src), str(instrumental_out))

    if progress_cb:
        progress_cb(1.0)


def _find_stem(output_dir: Path, keyword: str, filenames: list) -> Path | None:
    """Find the output file matching a keyword (Vocals / Instrumental)."""
    for name in filenames:
        p = Path(name)
        if not p.is_absolute():
            p = output_dir / p
        if keyword.lower() in p.name.lower() and p.exists():
            return p
    # fallback: search directory
    for p in output_dir.glob(f"*{keyword}*"):
        return p
    return None


def _separate_ffmpeg_fallback(
    audio_path: Path,
    vocals_out: Path,
    instrumental_out: Path,
) -> None:
    """Fallback: FFmpeg center-channel cancellation for stereo audio."""
    ffmpeg = _find_ffmpeg()
    channels = _probe_channels(audio_path, ffmpeg)

    if channels >= 2:
        instr_af = "pan=stereo|c0=0.5*c0-0.5*c1|c1=0.5*c1-0.5*c0"
        # Vocals approximation: band-pass voice frequencies
        vocal_af = "highpass=f=200,lowpass=f=4000"
    else:
        instr_af = "acopy"
        vocal_af = "acopy"

    for out, af in [(instrumental_out, instr_af), (vocals_out, vocal_af)]:
        subprocess.run(
            [str(ffmpeg), "-y", "-i", str(audio_path), "-af", af, str(out)],
            capture_output=True, check=True, **no_window(),
        )


def _find_ffmpeg() -> Path:
    from dubber.core.utils.ffmpeg_locator import find_ffmpeg
    return find_ffmpeg()


def _probe_channels(audio_wav: Path, ffmpeg: Path) -> int:
    ffprobe = ffmpeg.parent / ("ffprobe.exe" if ffmpeg.name.endswith(".exe") else "ffprobe")
    result = subprocess.run(
        [str(ffprobe), "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=channels", "-of", "csv=p=0", str(audio_wav)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", **no_window(),
    )
    try:
        return int(result.stdout.strip())
    except ValueError:
        return 1
