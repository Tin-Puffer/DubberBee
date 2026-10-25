"""Locate SenseVoice / VAD models: bundle dir first, user-data dir, spike/models fallback."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from platformdirs import user_data_dir

from dubber import RELEASES_REPO
from dubber.core.utils.ffmpeg_locator import _project_root

SHERPA_MODEL_NAME = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"
SILERO_VAD_NAME = "silero_vad.onnx"
FSMN_VAD_DIR = "fsmn-vad"   # ModelScope iic/speech_fsmn_vad_zh-cn-16k-common-onnx
PIPER_MODEL_NAME = "vi_VN-thanh_nien_tu_tin-medium.onnx"
PIPER_SUBDIR = "piper"

# Every model the app needs is mirrored as an asset of this GitHub release (same file
# name as upstream; extra packs below). Tried first, the original source is the fallback.
MODELS_MIRROR_URL = f"https://github.com/{RELEASES_REPO}/releases/download/models-v1"
VIENEU_ARCHIVE = "vieneu-v3-turbo-onnx.tar"   # vieneu/{VieNeu-TTS-v3-Turbo,MOSS-…}/…

# VieNeu-TTS v3 Turbo (ONNX/CPU) + its MOSS audio codec, pinned commits. Stored as plain
# files (not the HF cache): onnxruntime refuses external .data reached through symlinks.
VIENEU_SUBDIR = "vieneu"
VIENEU_REPOS = {
    # local folder: (repo id, commit, files)
    "VieNeu-TTS-v3-Turbo": (
        "pnnbao-ump/VieNeu-TTS-v3-Turbo", "61b85e3d937fbbacb387714180e8182823512523",
        ["config.json", "speaker_encoder.onnx", "denoiser.onnx", "onnx_update/*"],
    ),
    "MOSS-Audio-Tokenizer-Nano-ONNX": (
        "OpenMOSS-Team/MOSS-Audio-Tokenizer-Nano-ONNX", "ceff0d0749bfb3fa2d61149794ec6feef0d1e1ae",
        ["*.onnx", "*.data", "*.json"],
    ),
}


def _bundle_models_dir() -> Path:
    """When running as PyInstaller bundle, models are in resources/models/ inside _internal/."""
    if getattr(sys, 'frozen', False):
        # sys._MEIPASS is the _internal/ folder where PyInstaller extracts datas
        return Path(sys._MEIPASS) / "resources" / "models"
    return Path()  # empty path — won't match any candidate


def _user_models_dir() -> Path:
    return Path(user_data_dir("dubber", "dubber")) / "models"


def _spike_models_dir() -> Path:
    try:
        return _project_root() / "spike" / "models"
    except Exception:
        return Path()


def _search_bases() -> list[Path]:
    return [_bundle_models_dir(), _user_models_dir(), _spike_models_dir()]


def find_sherpa_model_dir() -> Path:
    """Return the sherpa-onnx SenseVoice model directory.

    Checks bundle dir first, then user-data dir, then spike/models (dev fallback).
    Raises FileNotFoundError if not found.
    """
    for base in _search_bases():
        candidate = base / SHERPA_MODEL_NAME
        if candidate.is_dir() and (candidate / "tokens.txt").exists():
            return candidate
    raise FileNotFoundError(
        f"SenseVoice model '{SHERPA_MODEL_NAME}' not found.\n"
        "Run:  python spike/download_models.py  to download it."
    )


def find_silero_vad() -> Optional[Path]:
    """Return path to silero_vad.onnx, or None if absent (VAD becomes optional)."""
    for base in _search_bases():
        candidate = base / SILERO_VAD_NAME
        if candidate.is_file():
            return candidate
    return None


def find_fsmn_vad_dir() -> Optional[Path]:
    """Return the FSMN-VAD model directory, or None if absent (Silero is the fallback)."""
    for base in _search_bases():
        d = base / FSMN_VAD_DIR
        if ((d / "config.yaml").is_file() and (d / "am.mvn").is_file()
                and ((d / "model_quant.onnx").is_file() or (d / "model.onnx").is_file())):
            return d
    return None


def _vieneu_complete(d: Path) -> bool:
    return ((d / "VieNeu-TTS-v3-Turbo" / "onnx_update" / "vieneu_prefill.onnx").is_file()
            and (d / "MOSS-Audio-Tokenizer-Nano-ONNX" / "moss_audio_tokenizer_decode_full.onnx").is_file())


def find_vieneu_model_dir() -> Optional[Path]:
    """Return the vieneu/ models directory (bundle → user-data → spike), or None."""
    for base in _search_bases():
        d = base / VIENEU_SUBDIR
        if _vieneu_complete(d):
            return d
    return None


def download_vieneu_models(dest: Optional[Path] = None) -> Path:
    """Fetch the VieNeu ONNX models (~610 MB) as plain files into *dest*/vieneu/:
    the release mirror's tar first, the pinned HuggingFace commits otherwise."""
    import logging
    base = dest or _user_models_dir()
    root = base / VIENEU_SUBDIR
    try:
        _download_vieneu_archive(base)
        if _vieneu_complete(root):
            return root
    except Exception as exc:
        logging.getLogger(__name__).warning("VieNeu mirror unavailable (%s) — using HuggingFace", exc)
    return download_vieneu_upstream(base)


def download_vieneu_upstream(base: Path) -> Path:
    """The pinned HuggingFace commits into *base*/vieneu/ (the mirror's original source)."""
    from huggingface_hub import snapshot_download
    root = base / VIENEU_SUBDIR
    for folder, (repo, rev, patterns) in VIENEU_REPOS.items():
        snapshot_download(repo, revision=rev, local_dir=root / folder, allow_patterns=patterns)
    return root


def _download_vieneu_archive(base: Path) -> None:
    import shutil
    import tarfile
    import tempfile

    from dubber.core.utils.download import fetch

    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=base) as tmp:
        tar = Path(tmp) / VIENEU_ARCHIVE
        fetch(f"{MODELS_MIRROR_URL}/{VIENEU_ARCHIVE}", tar)
        with tarfile.open(tar) as tf:
            tf.extractall(tmp, filter="data")   # refuses absolute / ".." paths
        target = base / VIENEU_SUBDIR
        shutil.rmtree(target, ignore_errors=True)
        shutil.move(str(Path(tmp) / VIENEU_SUBDIR), str(target))


def find_piper_model(name: str | None = None) -> Path:
    """Return path to a piper Vietnamese ONNX model file.

    Args:
        name: model stem (e.g. "vi_VN-vais1000-medium") or None → default model.

    Checks bundle dir first, then user-data dir, then spike/models/piper (dev fallback).
    Raises FileNotFoundError if not found.
    """
    target = (name + ".onnx") if name else PIPER_MODEL_NAME
    for base in _search_bases():
        candidate = base / PIPER_SUBDIR / target
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"Piper model '{target}' not found in:\n"
        f"  {_bundle_models_dir() / PIPER_SUBDIR}\n"
        f"  {_user_models_dir() / PIPER_SUBDIR}\n"
        f"  {_spike_models_dir() / PIPER_SUBDIR}\n"
        "Download from: https://huggingface.co/rhasspy/piper-voices"
    )


def list_piper_models() -> list[str]:
    """Return list of available piper model stems (without .onnx extension)."""
    stems: list[str] = []
    seen: set[str] = set()
    for base in _search_bases():
        d = base / PIPER_SUBDIR
        if d.is_dir():
            for f in sorted(d.glob("*.onnx")):
                if f.stem not in seen:
                    stems.append(f.stem)
                    seen.add(f.stem)
    return stems or [PIPER_MODEL_NAME.replace(".onnx", "")]
