"""VieNeu voice library: the SDK's preset voices + voices cloned by the user.

A cloned voice lives in ``<user_data>/voices/vieneu/<voice_id>/`` (outside the app
bundle, so it survives updates):

- ``reference.wav`` — the cut, mono reference clip (≤ 8 s)
- ``voice.json``    — ``{"speaker_emb": [...], "codes": [[...]]}``, what the SDK's
  ``encode_reference()`` returned, in its voices-file format → never re-encoded
- ``meta.json``     — ``{id, name, created_at, source_file_name, duration, engine_version}``

Voices are addressed as ``"preset:<name>"`` or ``"clone:<voice_id>"``.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from platformdirs import user_data_dir

log = logging.getLogger(__name__)

MAX_REF_S = 8.0    # the SDK keeps at most 8 s of reference
MIN_REF_S = 3.0    # shorter clips clone poorly


@dataclass
class VoiceEntry:
    id: str        # "preset:<name>" | "clone:<voice_id>"
    name: str
    kind: str      # "preset" | "clone"


def voices_dir() -> Path:
    return Path(user_data_dir("dubber", "dubber")) / "voices" / "vieneu"


# ── Presets ───────────────────────────────────────────────────────────────────

def preset_voices() -> List[VoiceEntry]:
    """The SDK's built-in voices (editors' picks first), read without loading a model."""
    import importlib.util
    spec = importlib.util.find_spec("vieneu")
    if spec is None or not spec.origin:
        return []
    path = Path(spec.origin).parent / "assets" / "voices_v3_turbo.json"
    try:
        presets = json.loads(path.read_text(encoding="utf-8")).get("presets", {})
    except (OSError, ValueError):
        return []
    names = list(presets)
    featured = sorted((n for n in names if presets[n].get("featured")),
                      key=lambda n: presets[n]["featured"])
    ordered = featured + [n for n in names if n not in featured]
    return [VoiceEntry(id=f"preset:{n}", name=n, kind="preset") for n in ordered]


# ── Clones ────────────────────────────────────────────────────────────────────

def clone_voices(root: Optional[Path] = None) -> List[VoiceEntry]:
    root = root or voices_dir()
    out = []
    for meta_path in sorted(root.glob("*/meta.json")):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (meta_path.parent / "voice.json").is_file():
            out.append(VoiceEntry(id=f"clone:{meta['id']}", name=meta["name"], kind="clone"))
    return sorted(out, key=lambda v: v.name.lower())


def list_voices(root: Optional[Path] = None) -> List[VoiceEntry]:
    return preset_voices() + clone_voices(root)


def clone_exists(voice_id: str, root: Optional[Path] = None) -> bool:
    d = (root or voices_dir()) / voice_id
    return (d / "voice.json").is_file() and (d / "meta.json").is_file()


def load_voice_data(voice_id: str, root: Optional[Path] = None) -> dict:
    """``{"speaker_emb": float32[192], "codes": int64[T, 16] | None}`` for SDK infer(voice=…)."""
    import numpy as np
    d = (root or voices_dir()) / voice_id
    try:
        data = json.loads((d / "voice.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"Giọng clone '{voice_id}' đã bị xóa — chọn giọng khác") from None
    codes = data.get("codes")
    return {
        "speaker_emb": np.asarray(data["speaker_emb"], dtype=np.float32),
        "codes": None if codes is None else np.asarray(codes, dtype=np.int64),
    }


def _name_taken(name: str, root: Path, except_id: Optional[str] = None) -> bool:
    key = name.strip().casefold()
    return any(v.name.strip().casefold() == key
               for v in clone_voices(root) + preset_voices()
               if v.id != f"clone:{except_id}")


def make_voice_id(name: str) -> str:
    """Slug of the name (accents removed) + short random suffix."""
    ascii_name = unicodedata.normalize("NFKD", name.replace("đ", "d").replace("Đ", "D"))
    ascii_name = ascii_name.encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:32] or "voice"
    return f"{slug}-{uuid.uuid4().hex[:6]}"


def prepare_reference(src: Path, dst: Path, start_s: float = 0.0,
                      max_s: float = MAX_REF_S) -> float:
    """Any audio/video → mono 44.1 kHz PCM16 WAV, at most *max_s* from *start_s*.

    Returns the resulting duration in seconds.
    """
    import wave

    from dubber.core.utils.ffmpeg_locator import find_ffmpeg, no_window
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [str(find_ffmpeg()), "-y", "-ss", f"{max(0.0, start_s):.3f}", "-i", str(src),
           "-t", f"{max_s:.3f}", "-vn", "-ac", "1", "-ar", "44100", "-c:a", "pcm_s16le", str(dst)]
    r = subprocess.run(cmd, capture_output=True, text=True, **no_window())
    if r.returncode != 0 or not dst.exists():
        raise RuntimeError(f"Không đọc được âm thanh từ {src.name}:\n{r.stderr[-800:]}")
    with wave.open(str(dst), "rb") as wf:
        return wf.getnframes() / wf.getframerate()


def add_clone(audio_path: Path, name: str, sdk, start_s: float = 0.0,
              root: Optional[Path] = None) -> VoiceEntry:
    """Cut the reference, encode it once with the SDK and store it in the library.

    *sdk* is a loaded ``vieneu`` v3 Turbo instance (needs ``encode_reference``).
    """
    root = root or voices_dir()
    name = name.strip()
    if not name:
        raise ValueError("Tên giọng không được để trống")
    if _name_taken(name, root):
        raise ValueError(f"Đã có giọng tên '{name}'")

    voice_id = make_voice_id(name)
    d = root / voice_id
    tmp = root / f".{voice_id}.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        ref = tmp / "reference.wav"
        duration = prepare_reference(Path(audio_path), ref, start_s)
        emb, codes = sdk.encode_reference(str(ref))
        (tmp / "voice.json").write_text(json.dumps({
            "speaker_emb": [round(float(x), 6) for x in emb.reshape(-1)],
            "codes": None if codes is None else codes.astype(int).tolist(),
        }), encoding="utf-8")
        _write_meta(tmp, {
            "id": voice_id, "name": name,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source_file_name": Path(audio_path).name,
            "duration": round(duration, 2),
            "engine_version": _vieneu_version(),
        })
        tmp.rename(d)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    log.info("Đã lưu giọng clone '%s' → %s", name, d)
    return VoiceEntry(id=f"clone:{voice_id}", name=name, kind="clone")


def rename(voice_id: str, new_name: str, root: Optional[Path] = None) -> None:
    root = root or voices_dir()
    new_name = new_name.strip()
    if not new_name:
        raise ValueError("Tên giọng không được để trống")
    if _name_taken(new_name, root, except_id=voice_id):
        raise ValueError(f"Đã có giọng tên '{new_name}'")
    meta_path = root / voice_id / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["name"] = new_name
    _write_meta(meta_path.parent, meta)


def delete(voice_id: str, root: Optional[Path] = None) -> None:
    d = (root or voices_dir()) / voice_id
    if not voice_id or not (d / "meta.json").is_file():
        raise ValueError(f"Không có giọng clone '{voice_id}'")
    shutil.rmtree(d)


def _write_meta(d: Path, meta: dict) -> None:
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


def _vieneu_version() -> str:
    try:
        import importlib.metadata
        return importlib.metadata.version("vieneu")
    except Exception:
        return ""
