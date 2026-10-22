"""VieNeu-TTS v3 Turbo engine — natural Vietnamese voices + voice cloning, ONNX on CPU.

SDK: ``vieneu`` 3.8.3 (Apache-2.0, https://github.com/pnnbao97/VieNeu-TTS). Output is
48 kHz; v3 Turbo has no speed control, so ``supports_rate = False`` and TimingFitter
speeds lines up with FFmpeg atempo instead.

Voices are given as ``"preset:<name>"`` (built into the SDK, e.g. "preset:Hải Đăng")
or ``"clone:<voice_id>"`` (voice_library).
"""
from __future__ import annotations

import asyncio
import logging
import re
import threading
import wave
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from dubber.core.tts.base import TTSEngine, TTSResult, tts_cache_key

log = logging.getLogger(__name__)

SAMPLE_RATE = 48_000
DEFAULT_VOICE = "preset:Hải Đăng"

_CJK = re.compile(r"[　-〿㐀-䶿一-鿿＀-￯]")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")

_load_lock = threading.Lock()     # guards the temporary OnnxV3LiteEngine patch
_shared_lock = threading.Lock()
_shared = None
# The SDK instance is shared (pipeline thread + clone dialog thread); its ONNX
# sessions keep per-call state, so calls into it are serialised.
sdk_lock = threading.RLock()


def clean_text(text: str) -> str:
    """Drop what the Vietnamese front-end can't read (leftover Chinese, control chars).

    Numbers, abbreviations etc. are left to the SDK's normaliser (sea-g2p).
    """
    text = _CTRL.sub(" ", _CJK.sub(" ", text))
    return re.sub(r"\s+", " ", text).strip()


def load_vieneu(model_dir: Optional[Path] = None):
    """Load the SDK on CPU/ONNX from local model files (downloading them on first use)."""
    import sys

    from dubber.core.asr.model_manager import download_vieneu_models, find_vieneu_model_dir

    try:
        from vieneu import Vieneu
        from vieneu._v3_turbo_engine import onnx_runtime_lite as orl
    except ImportError as exc:
        if getattr(sys, "frozen", False):
            raise RuntimeError("Bản đóng gói chưa có VieNeu-TTS — chọn bộ đọc Piper.") from exc
        raise RuntimeError('Chưa cài VieNeu-TTS: pip install -e ".[vieneu]"') from exc

    root = model_dir or find_vieneu_model_dir()
    if root is None:
        log.info("VieNeu-TTS: lần đầu cần tải model (~610MB)…")
        root = download_vieneu_models()
    turbo = root / "VieNeu-TTS-v3-Turbo"
    codec = root / "MOSS-Audio-Tokenizer-Nano-ONNX"

    # V3TurboVieNeuTTS doesn't pass codec_dir on to its ONNX engine, which would then
    # fetch the codec into the HF cache (symlinks → onnxruntime rejects its .data).
    with _load_lock:
        orig_init = orl.OnnxV3LiteEngine.__init__

        def _init(eng, *a, **kw):
            kw.setdefault("codec_dir", str(codec))
            orig_init(eng, *a, **kw)

        orl.OnnxV3LiteEngine.__init__ = _init
        try:
            tts = Vieneu(backend="onnx", backbone_repo=str(turbo),
                         onnx_dir=str(turbo / "onnx_update"))
        finally:
            orl.OnnxV3LiteEngine.__init__ = orig_init
    log.info("VieNeu-TTS loaded from %s (backend=%s)", root, tts.backend)
    return tts


def shared_vieneu():
    """One SDK instance per process (≈1.6 GB): pipeline and clone dialog share it."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = load_vieneu()
        return _shared


class VieNeuTTSEngine(TTSEngine):
    """Synthesize Vietnamese via VieNeu-TTS v3 Turbo.

    Args:
        voice: "preset:<name>" or "clone:<voice_id>".
        tts_dir: Directory for caching WAV files.
    """

    engine_id = "vieneu"
    supports_rate = False

    def __init__(self, voice: Optional[str], tts_dir: Path, model_dir: Optional[Path] = None,
                 tts=None) -> None:
        self._voice_spec = voice or DEFAULT_VOICE
        self._tts_dir = tts_dir
        tts_dir.mkdir(parents=True, exist_ok=True)
        self._tts = tts or (load_vieneu(model_dir) if model_dir else shared_vieneu())
        self._voice = self._resolve_voice(self._voice_spec)
        # (done, total) after each line — VieNeu is slow enough that the GUI shows it
        self.on_progress: Optional[Callable[[int, int], None]] = None

    def _resolve_voice(self, spec: str):
        kind, _, name = spec.partition(":")
        if kind == "preset" and name:
            if self._tts.resolve_voice_name(name) is None:
                raise ValueError(f"Không có giọng VieNeu '{name}'")
            return name
        if kind == "clone" and name:
            from dubber.core.tts.voice_library import load_voice_data
            return load_voice_data(name)
        raise ValueError(f"Giọng VieNeu không hợp lệ: {spec!r}")

    # ── Public API ────────────────────────────────────────────────────────────

    async def synthesize_one(
        self,
        text: str,
        segment_id: int,
        rate: str | None = None,   # ignored: v3 Turbo has no speed control
    ) -> TTSResult:
        key = tts_cache_key(text, self._voice_spec, "+0%", engine=self.engine_id)
        wav_path = self._tts_dir / f"{key}.wav"
        if not wav_path.exists():
            await asyncio.to_thread(self._synthesize_sync, text, wav_path)
        else:
            log.debug("TTS cache hit: seg %d → %s", segment_id, wav_path.name)
        return TTSResult(segment_id=segment_id, wav_path=wav_path,
                         duration=_wav_duration(wav_path))

    async def synthesize_all(
        self,
        items: list[tuple[int, str]],
        request_delay: float = 0.0,
    ) -> list[TTSResult]:
        """Synthesize all segments sequentially (CPU-bound, single model)."""
        results: list[TTSResult] = []
        for idx, (sid, text) in enumerate(items):
            result = await self.synthesize_one(text, sid)
            log.info("VieNeu TTS [%d/%d] seg %d done (%.2fs)",
                     idx + 1, len(items), sid, result.duration)
            results.append(result)
            if self.on_progress:
                self.on_progress(idx + 1, len(items))
        return results

    # ── Internal ──────────────────────────────────────────────────────────────

    def _synthesize_sync(self, text: str, wav_path: Path) -> None:
        text = clean_text(text)
        if text:
            with sdk_lock:
                audio = self._tts.infer(text, voice=self._voice)
        else:
            audio = np.zeros(int(0.1 * SAMPLE_RATE), dtype=np.float32)
        write_wav(wav_path, np.asarray(audio, dtype=np.float32), self._tts.sample_rate)


# ── Helpers ───────────────────────────────────────────────────────────────────

def write_wav(path: Path, audio: np.ndarray, sr: int) -> None:
    """Mono PCM16 WAV (what the mixer / FFmpeg / wave readers expect)."""
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
    tmp = path.with_suffix(".tmp.wav")
    with wave.open(str(tmp), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())
    tmp.replace(path)


def _wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as wf:
        return wf.getnframes() / wf.getframerate()
