"""First-run model download: the app ships without models; they go to the user-data dir
(survives app updates), from the GitHub release mirror first, the original source otherwise.

    if missing_models(required_only=True):      # the app cannot start without these
        failed = download_models(missing_models(), progress=cb, cancel=token)

Required: ASR, both VADs, the default voice. Everything else is optional: when it cannot be
fetched the app still opens (other voices are just not listed; VieNeu and the separation
model are fetched again when first used).
"""
from __future__ import annotations

import logging
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from dubber import RELEASES_REPO
from dubber.core.asr.model_manager import (
    FSMN_VAD_DIR, MODELS_MIRROR_URL, PIPER_MODEL_NAME, PIPER_SUBDIR, SHERPA_MODEL_NAME,
    SILERO_VAD_NAME, VIENEU_ARCHIVE, VIENEU_SUBDIR, _search_bases, _user_models_dir,
    _vieneu_complete,
)

log = logging.getLogger(__name__)

MIRROR_TAG = MODELS_MIRROR_URL.rsplit("/", 1)[-1]
MIRROR_API = f"https://api.github.com/repos/{RELEASES_REPO}/releases/tags/{MIRROR_TAG}"

# ── upstream sources (fallback when the mirror is unreachable) ────────────────
SHERPA_MODEL_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
    f"{SHERPA_MODEL_NAME}.tar.bz2"
)
SHERPA_FILES = ("model.int8.onnx", "tokens.txt")   # all the app loads from the archive
SILERO_VAD_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx"
)
FSMN_VAD_MODEL_ID = "iic/speech_fsmn_vad_zh-cn-16k-common-onnx"
FSMN_VAD_FILES = ["model_quant.onnx", "config.yaml", "am.mvn"]

_RHASSPY = "https://huggingface.co/rhasspy/piper-voices/resolve/main/vi/vi_VN"
_LAONAMA = "https://huggingface.co/laonama/viet-pipertts/resolve/main/vn"
_ACUT = "https://huggingface.co/vongocanhthi/acut-piper-vietnamese/resolve/main/piper/vi/vi_VN"
PIPER_VOICES = {
    # rhasspy/piper-voices (official)
    "vi_VN-vais1000-medium": f"{_RHASSPY}/vais1000/medium/vi_VN-vais1000-medium.onnx",
    "vi_VN-25hours_single-low": f"{_RHASSPY}/25hours_single/low/vi_VN-25hours_single-low.onnx",
    "vi_VN-vivos-x_low": f"{_RHASSPY}/vivos/x_low/vi_VN-vivos-x_low.onnx",
    # vongocanhthi/acut-piper-vietnamese (community) — app default voice
    "vi_VN-thanh_nien_tu_tin-medium":
        f"{_ACUT}/thanh_nien_tu_tin/medium/vi_VN-thanh_nien_tu_tin-medium.onnx",
    # laonama/viet-pipertts (community)
    **{stem: f"{_LAONAMA}/{stem}.onnx" for stem in [
        "adam1", "calmwoman3688", "kinhphat", "maiphuong", "manhdung", "minhthu",
        "mytam2", "ngochuyen", "ngochuyennew", "ngocngan3701", "vunga", "yannew",
    ]},
}
DEFAULT_PIPER_VOICE = PIPER_MODEL_NAME.removesuffix(".onnx")


@dataclass(frozen=True)
class ModelFile:
    """One release asset. *rel* is where it lands under the models dir (for an archive:
    the folder it unpacks to); *base* overrides the models dir (UVR has its own).
    The original source is either a URL (*upstream*) or a function that fetches the item
    its own way into the models dir (*fallback*)."""
    asset: str
    rel: str
    upstream: Optional[str] = None
    unpack: Optional[str] = None          # "sherpa" | "vieneu"
    base: Optional[Path] = None
    required: bool = False
    fallback: Optional[Callable[[Path], object]] = None

    def present(self) -> bool:
        if self.base is not None:
            return (self.base / self.rel).is_file()
        for b in _search_bases():
            p = b / self.rel
            if self.unpack == "sherpa":
                if all((p / f).is_file() for f in SHERPA_FILES):
                    return True
            elif self.unpack == "vieneu":
                if _vieneu_complete(p):
                    return True
            elif p.is_file():
                return True
        return False


def _uvr_fallback(base: Path) -> None:
    from dubber.core.audio.separate import download_uvr_upstream
    download_uvr_upstream(base)


def _vieneu_fallback(base: Path) -> None:
    from dubber.core.asr.model_manager import download_vieneu_upstream
    download_vieneu_upstream(base)


def catalog(voices: Optional[list[str]] = None, vieneu: bool = True) -> list[ModelFile]:
    """Everything the app can use (default: all 16 voices + VieNeu), required first —
    a cancelled first run already has ASR + the default voice."""
    from dubber.core.audio.separate import UVR_FILES, _uvr_model_dir
    stems = voices or list(PIPER_VOICES)
    stems = sorted(stems, key=lambda s: s != DEFAULT_PIPER_VOICE)
    items = [
        ModelFile(f"{SHERPA_MODEL_NAME}.tar.bz2", SHERPA_MODEL_NAME, SHERPA_MODEL_URL, "sherpa",
                  required=True),
        *(ModelFile(f"fsmn-vad-{f}", f"{FSMN_VAD_DIR}/{f}",
                    f"https://modelscope.cn/api/v1/models/{FSMN_VAD_MODEL_ID}/repo"
                    f"?Revision=master&FilePath={f}", required=True) for f in FSMN_VAD_FILES),
        ModelFile(SILERO_VAD_NAME, SILERO_VAD_NAME, SILERO_VAD_URL, required=True),
    ]
    for stem in stems:
        url = PIPER_VOICES.get(stem)
        req = stem == DEFAULT_PIPER_VOICE
        items.append(ModelFile(f"{stem}.onnx", f"{PIPER_SUBDIR}/{stem}.onnx", url, required=req))
        items.append(ModelFile(f"{stem}.onnx.json", f"{PIPER_SUBDIR}/{stem}.onnx.json",
                               url and url + ".json", required=req))
    uvr = _uvr_model_dir()
    items += [ModelFile(asset, local, base=uvr, fallback=_uvr_fallback)
              for local, asset in UVR_FILES.items()]
    if vieneu:
        items.append(ModelFile(VIENEU_ARCHIVE, VIENEU_SUBDIR, unpack="vieneu",
                               fallback=_vieneu_fallback))
    return items


def missing_models(required_only: bool = False) -> list[ModelFile]:
    return [m for m in catalog() if (m.required or not required_only) and not m.present()]


def mirror_info() -> dict[str, tuple[int, Optional[str]]]:
    """asset name → (size, sha256) from the mirror release; {} when GitHub is unreachable
    (download still works, just without a known total / checksum)."""
    from dubber.core.utils.download import get_json, get_text
    info: dict[str, tuple[int, Optional[str]]] = {}
    try:
        for a in get_json(MIRROR_API).get("assets", []):
            digest = a.get("digest") or ""
            info[a["name"]] = (int(a.get("size") or 0),
                               digest.split(":", 1)[1] if digest.startswith("sha256:") else None)
    except Exception as exc:
        log.warning("Model list unavailable (%s)", exc)
    try:   # checksums file: not rate-limited like the API
        for line in get_text(f"{MODELS_MIRROR_URL}/SHA256SUMS.txt").splitlines():
            parts = line.split()
            if len(parts) == 2:
                size, _ = info.get(parts[1], (0, None))
                info[parts[1]] = (size, parts[0])
    except Exception as exc:
        log.warning("SHA256SUMS.txt unavailable (%s)", exc)
    return info


def download_models(items: list[ModelFile], dest: Optional[Path] = None,
                    progress: Optional[Callable[[int, int, str], None]] = None,
                    cancel: Optional[Callable[[], bool]] = None) -> list[ModelFile]:
    """Fetch *items* into *dest* (default: user-data models dir). progress(done, total,
    asset) — total 0 when unknown. A required item failing from every source raises;
    optional ones are skipped and returned. Finished files stay, so calling again
    continues."""
    from dubber.core.utils.download import DownloadCancelled
    base = dest or _user_models_dir()
    info = mirror_info()
    total = sum(info.get(m.asset, (0, None))[0] for m in items)
    if any(m.asset not in info for m in items):
        total = 0
    finished = 0
    failed: list[ModelFile] = []
    for m in items:
        target_base = m.base or base
        size, sha = info.get(m.asset, (0, None))
        if m.present():   # fetched along with an earlier item (fallbacks fetch a whole set)
            finished += size
            continue

        def cb(done: int, _total: int, name: str = m.asset) -> None:
            if progress:
                progress(finished + done, total, name)

        tmp_dir = target_base / ".download"
        file_dest = tmp_dir / m.asset if m.unpack else target_base / m.rel
        try:
            _fetch_item(m, file_dest, target_base, sha, cb, cancel)
        except DownloadCancelled:
            raise
        except Exception as exc:
            if m.required:
                raise
            log.warning("Optional model %s unavailable (%s) — skipped", m.asset, exc)
            failed.append(m)
        finished += size
    shutil.rmtree(base / ".download", ignore_errors=True)
    return failed


def _fetch_item(m: ModelFile, file_dest: Path, target_base: Path, sha: Optional[str],
                cb: Callable[[int, int], None], cancel: Optional[Callable[[], bool]]) -> None:
    """Mirror first, then the upstream URL or the item's own fallback."""
    from dubber.core.utils.download import DownloadCancelled, fetch
    try:
        fetch(f"{MODELS_MIRROR_URL}/{m.asset}", file_dest, progress=cb, cancel=cancel,
              sha256=sha)
    except DownloadCancelled:
        raise
    except Exception as exc:
        if not (m.upstream or m.fallback):
            raise
        log.warning("Mirror failed for %s (%s) — trying the original source", m.asset, exc)
        file_dest.with_name(file_dest.name + ".part").unlink(missing_ok=True)
        cb(0, 0)
        if m.fallback:
            m.fallback(target_base)
            if not m.present():
                raise FileNotFoundError(f"{m.asset}: nguồn gốc không có file này") from exc
            return
        fetch(m.upstream, file_dest, progress=cb, cancel=cancel)
    if m.unpack:
        _unpack(m, file_dest, target_base)
        file_dest.unlink(missing_ok=True)


def _unpack(m: ModelFile, archive: Path, base: Path) -> None:
    target = base / m.rel
    with tempfile.TemporaryDirectory(dir=base) as tmp:
        with tarfile.open(archive) as tf:
            if m.unpack == "sherpa":
                members = [x for x in tf.getmembers()
                           if Path(x.name).name in SHERPA_FILES and x.isfile()]
                tf.extractall(tmp, members=members, filter="data")
            else:
                tf.extractall(tmp, filter="data")   # refuses absolute / ".." paths
        src = Path(tmp) / m.rel
        shutil.rmtree(target, ignore_errors=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(target))
