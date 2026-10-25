"""Download the models Dubber needs to run (cross-platform: Windows / macOS / Linux).

Default (what the app needs):
  - sherpa-onnx SenseVoice int8  → ASR
  - FSMN-VAD (FunASR, ModelScope) → VAD (default)
  - silero_vad.onnx              → VAD (fallback)
  - piper Vietnamese voice       → TTS (vi_VN-thanh_nien_tu_tin-medium)

    python spike/download_models.py
    python spike/download_models.py --all-voices   # every voice in PIPER_VOICES
    python spike/download_models.py --vieneu       # also VieNeu-TTS v3 Turbo (~610 MB)
    python spike/download_models.py --all          # everything: 16 voices + VieNeu (~2 GB)
    python spike/download_models.py --spike   # also the M1 FunASR/GGUF comparison (Windows binary)
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
SPIKE_DIR = ROOT / "spike"
MODELS_DIR = SPIKE_DIR / "models"
MODELS_DIR.mkdir(exist_ok=True)

# ── Mirror: every model below is also attached to this repo's GitHub release ─
# (same file name as the upstream URL's basename). Tried first; upstream is the
# fallback. Keeps the project buildable if a community HF repo disappears.
sys.path.insert(0, str(ROOT))
from dubber.core.asr.model_manager import MODELS_MIRROR_URL as MIRROR_URL  # noqa: E402

# ── Sources: shared with the app's first-run download (dubber/core/model_setup.py) ─
from dubber.core.model_setup import (  # noqa: E402
    DEFAULT_PIPER_VOICE, FSMN_VAD_FILES, FSMN_VAD_MODEL_ID, PIPER_VOICES, SHERPA_MODEL_URL,
    SILERO_VAD_URL,
)

SHERPA_MODEL_DIR = MODELS_DIR / "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"
FSMN_VAD_DIR = MODELS_DIR / "fsmn-vad"
PIPER_DIR = MODELS_DIR / "piper"

# ── FunASR llama.cpp binary (Windows x64) ───────────────────────────────────
FUNASR_BIN_URL = (
    "https://github.com/modelscope/FunASR/releases/download/"
    "runtime-llamacpp-v0.2.6/funasr-llamacpp-windows-x64.zip"
)
FUNASR_BIN_DIR = MODELS_DIR / "funasr-llamacpp-windows-x64"

# ── SenseVoice GGUF models (HuggingFace) ────────────────────────────────────
GGUF_DIR = MODELS_DIR / "funasr-gguf"


def _progress_hook(filename: str):
    last = [0]
    def hook(block, block_size, total):
        downloaded = block * block_size
        if total > 0:
            pct = min(100, downloaded * 100 // total)
            mb = downloaded / 1024 / 1024
            total_mb = total / 1024 / 1024
            if pct - last[0] >= 5:
                print(f"  {filename}: {pct}%  ({mb:.0f}/{total_mb:.0f} MB)")
                last[0] = pct
    return hook


def download(url: str, dest: Path, mirror_name: str | None = None) -> Path:
    """Fetch *url* to *dest*, trying the release mirror first (asset named like the
    URL's basename, or *mirror_name* when that basename isn't unique / usable)."""
    if dest.exists():
        print(f"  Already exists: {dest.name}")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {dest.name} …")
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    sources = [f"{MIRROR_URL}/{mirror_name or url.rsplit('/', 1)[-1]}", url]
    for i, src in enumerate(sources):
        try:
            urllib.request.urlretrieve(src, tmp, reporthook=_progress_hook(dest.name))
            break
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            if i == len(sources) - 1:
                raise
            print(f"  Mirror unavailable ({exc}) → falling back to upstream")
    tmp.replace(dest)
    print(f"  Saved: {dest}  ({dest.stat().st_size // 1024 // 1024} MB)")
    return dest


def download_sherpa_model() -> Path:
    if (SHERPA_MODEL_DIR / "tokens.txt").exists():
        print(f"  Already exists: {SHERPA_MODEL_DIR.name}")
        return SHERPA_MODEL_DIR
    archive = MODELS_DIR / "sherpa-sense-voice-int8.tar.bz2"
    download(SHERPA_MODEL_URL, archive)
    if not SHERPA_MODEL_DIR.exists():
        print("Extracting sherpa-onnx model …")
        with tarfile.open(archive, "r:bz2") as tf:
            tf.extractall(MODELS_DIR)
        print(f"  Extracted to {SHERPA_MODEL_DIR}")
    archive.unlink(missing_ok=True)
    return SHERPA_MODEL_DIR


def download_silero_vad() -> Path:
    return download(SILERO_VAD_URL, MODELS_DIR / "silero_vad.onnx")


def download_fsmn_vad() -> Path:
    for name in FSMN_VAD_FILES:
        url = (f"https://modelscope.cn/api/v1/models/{FSMN_VAD_MODEL_ID}/repo"
               f"?Revision=master&FilePath={name}")
        download(url, FSMN_VAD_DIR / name, mirror_name=f"fsmn-vad-{name}")
    return FSMN_VAD_DIR


def download_piper_voice(stem: str = DEFAULT_PIPER_VOICE) -> Path:
    """Download <stem>.onnx + <stem>.onnx.json into spike/models/piper/."""
    if stem not in PIPER_VOICES:
        raise SystemExit(
            f"Unknown piper voice '{stem}'. Available: {', '.join(PIPER_VOICES)}\n"
            "Custom voices: copy <name>.onnx + <name>.onnx.json into spike/models/piper/"
        )
    url = PIPER_VOICES[stem]
    onnx = download(url, PIPER_DIR / f"{stem}.onnx")
    download(url + ".json", PIPER_DIR / f"{stem}.onnx.json")
    return onnx


def download_funasr_binary() -> Path:
    archive = MODELS_DIR / "funasr-llamacpp-windows-x64.zip"
    download(FUNASR_BIN_URL, archive)
    if not FUNASR_BIN_DIR.exists():
        print("Extracting FunASR binary …")
        with zipfile.ZipFile(archive, "r") as zf:
            zf.extractall(MODELS_DIR)
        # Zip might extract to a subdirectory; find the exe
        exes = list(MODELS_DIR.rglob("llama-funasr-sensevoice.exe"))
        if not exes:
            exes = list(MODELS_DIR.rglob("funasr-sensevoice.exe"))
        if exes:
            print(f"  Binary found: {exes[0]}")
        else:
            print("  WARNING: exe not found after extraction")
    return FUNASR_BIN_DIR


def download_gguf_models() -> tuple[Path, Path]:
    """Download SenseVoice q8 + FSMN-VAD from HuggingFace via huggingface_hub."""
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        print("Installing huggingface_hub …")
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install",
                        "huggingface_hub", "-q"], check=True)
        from huggingface_hub import hf_hub_download

    GGUF_DIR.mkdir(exist_ok=True)

    print("Downloading sensevoice-small-q8.gguf from HuggingFace …")
    sv_path = hf_hub_download(
        repo_id="FunAudioLLM/SenseVoiceSmall-GGUF",
        filename="sensevoice-small-q8.gguf",
        local_dir=str(GGUF_DIR),
    )

    print("Downloading fsmn-vad.gguf from HuggingFace …")
    vad_path = hf_hub_download(
        repo_id="FunAudioLLM/SenseVoiceSmall-GGUF",
        filename="fsmn-vad.gguf",
        local_dir=str(GGUF_DIR),
    )

    return Path(sv_path), Path(vad_path)


def find_funasr_exe() -> Path | None:
    """Find the FunASR exe anywhere under MODELS_DIR."""
    for pattern in ["llama-funasr-sensevoice.exe", "funasr-sensevoice.exe",
                    "funasr*.exe", "llama-funasr*.exe"]:
        hits = list(MODELS_DIR.rglob(pattern))
        if hits:
            return hits[0]
    return None


def main() -> None:
    # Windows: a piped / redirected stdout is cp1252 and can't print "──", "✓"…
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--voice", action="append", choices=list(PIPER_VOICES),
                        help=f"piper voice to download (repeatable, default: {DEFAULT_PIPER_VOICE})")
    parser.add_argument("--all-voices", action="store_true",
                        help=f"download all {len(PIPER_VOICES)} piper voices (~60 MB each)")
    parser.add_argument("--vieneu", action="store_true",
                        help="also download the VieNeu-TTS v3 Turbo ONNX models (~610 MB)")
    parser.add_argument("--all", action="store_true",
                        help="everything the app can use: all voices + VieNeu (~2 GB)")
    parser.add_argument("--spike", action="store_true",
                        help="also download M1 spike models (FunASR Windows binary + GGUF)")
    args = parser.parse_args()
    if args.all:
        args.all_voices = args.vieneu = True

    print("=== Downloading Dubber models ===\n")

    print("── sherpa-onnx SenseVoice int8 ──")
    sherpa_dir = download_sherpa_model()
    print(f"  Model dir: {sherpa_dir}\n")

    print("── FSMN-VAD ──")
    print(f"  {download_fsmn_vad()}\n")

    print("── Silero VAD ──")
    print(f"  {download_silero_vad()}\n")

    print("── piper Vietnamese voice ──")
    stems = list(PIPER_VOICES) if args.all_voices else (args.voice or [DEFAULT_PIPER_VOICE])
    for stem in stems:
        print(f"  {download_piper_voice(stem)}")
    print()

    print("── Music / voice separation (UVR MDX-Net) ──")
    from dubber.core.audio.separate import download_uvr_model
    print(f"  {download_uvr_model()}\n")

    if args.vieneu:
        print("── VieNeu-TTS v3 Turbo ──")
        from dubber.core.asr.model_manager import download_vieneu_models
        print(f"  {download_vieneu_models(MODELS_DIR)}\n")

    if args.spike:
        download_spike_models()

    print("=== All downloads complete ===")


def download_spike_models() -> None:
    if sys.platform != "win32":
        print("── FunASR llama.cpp binary: skipped (Windows-only binary) ──\n")
    else:
        print("── FunASR llama.cpp binary ──")
        download_funasr_binary()
        print(f"  Binary: {find_funasr_exe()}\n")

    print("── SenseVoice GGUF models ──")
    sv_gguf, vad_gguf = download_gguf_models()
    print(f"  SenseVoice: {sv_gguf}")
    print(f"  VAD:        {vad_gguf}\n")


if __name__ == "__main__":
    main()
