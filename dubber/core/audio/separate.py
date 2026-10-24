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
    vocals_out       = output_dir / "vocals.wav"
    instrumental_out = output_dir / "instrumental.wav"

    fallback_marker = output_dir / ".ffmpeg_fallback"
    sig = {"model": MODEL_NAME, "overlap": overlap}

    if vocals_out.exists() and instrumental_out.exists():
        if fallback_marker.exists():
            # Don't keep reusing poor fallback stems once the real separator is installed
            if not _mdxnet_available():
                log.info("Reusing cached (FFmpeg fallback) separation")
                return vocals_out, instrumental_out
            log.info("Cached separation came from the FFmpeg fallback — redoing with MDX-Net")
        elif _stems_sig(vocals_out) == sig:
            log.info("Reusing cached separation: %s, %s (overlap %s)",
                     vocals_out.name, instrumental_out.name, overlap)
            return vocals_out, instrumental_out
        else:
            log.info("Cached separation was made with other settings — separating again "
                     "(overlap %s)", overlap)
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
    import urllib.request
    from dubber.core.asr.model_manager import MODELS_MIRROR_URL

    d = dest or _uvr_model_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        for local, asset in UVR_FILES.items():
            if not (d / local).is_file():
                tmp = d / (local + ".part")
                urllib.request.urlretrieve(f"{MODELS_MIRROR_URL}/{asset}", tmp)
                tmp.replace(d / local)
    except Exception as exc:
        log.warning("UVR mirror unavailable (%s) — downloading via audio-separator", exc)
    if not all((d / f).is_file() for f in UVR_FILES):
        import tempfile
        from audio_separator.separator import Separator
        with tempfile.TemporaryDirectory() as out:
            # load_model downloads the model and the lists it needs to identify it
            Separator(output_dir=out, model_file_dir=str(d),
                      log_level=logging.WARNING).load_model(MODEL_NAME)
    return d


def _mdxnet_available() -> bool:
    try:
        from audio_separator.separator import Separator  # noqa: F401
        return True
    except ImportError:
        return False


def _uvr_model_dir() -> Path:
    """Persistent directory for UVR MDX-Net model files (survives across runs)."""
    import sys
    if getattr(sys, 'frozen', False):
        # Bundled exe: use resources/models/uvr/ inside PyInstaller's _internal/
        return Path(sys._MEIPASS) / "resources" / "models" / "uvr"
    try:
        from platformdirs import user_cache_dir
        return Path(user_cache_dir("dubber", "dubber")) / "uvr-models"
    except ImportError:
        # Fallback to same drive as Python (avoids /tmp on Windows)
        import sys as _sys
        return Path(_sys.executable).parent.parent / "uvr-models"


def _separate_mdxnet(
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
