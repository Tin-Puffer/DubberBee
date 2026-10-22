"""PiperTTS synthesis engine — fully offline, ONNX-based Vietnamese TTS."""
from __future__ import annotations

import asyncio
import logging
import wave
from pathlib import Path

from dubber.core.tts.base import TTSEngine, TTSResult, tts_cache_key

log = logging.getLogger(__name__)

# Default length_scale = 1.0 (piper native speed).
# rate string "+X%" → length_scale = 1 / (1 + X/100)
_DEFAULT_LENGTH_SCALE = 1.0


def _rate_to_length_scale(rate: str | None) -> float:
    """Convert edge-tts-style "+X%" rate string to piper length_scale."""
    if not rate or rate in ("+0%", "0%"):
        return _DEFAULT_LENGTH_SCALE
    try:
        pct_str = rate.lstrip("+").rstrip("%")
        pct = float(pct_str)
        if pct == 0.0:
            return _DEFAULT_LENGTH_SCALE
        # Faster speech → smaller length_scale
        return 1.0 / (1.0 + pct / 100.0)
    except ValueError:
        log.warning("Cannot parse piper rate %r, using default", rate)
        return _DEFAULT_LENGTH_SCALE


class PiperTTSEngine(TTSEngine):
    """Synthesize Vietnamese TTS via piper-tts (offline ONNX).

    Args:
        model_path: Path to the .onnx piper model file.
        tts_dir: Directory for caching WAV files.
    """

    engine_id = "piper"

    def __init__(self, model_path: Path, tts_dir: Path) -> None:
        from piper.voice import PiperVoice  # lazy import — heavy on first load

        self._model_path = model_path
        self._voice_name = model_path.stem   # e.g. vi_VN-vais1000-medium
        self._tts_dir = tts_dir
        tts_dir.mkdir(parents=True, exist_ok=True)

        log.info("Loading piper model: %s", model_path.name)
        self._voice = PiperVoice.load(model_path)
        log.info("Piper model loaded (sample_rate=%d)", self._voice.config.sample_rate)
        # (done, total) after each line — progress % in the GUI, and a cancel checkpoint
        self.on_progress = None

    # ── Public API ────────────────────────────────────────────────────────────

    async def synthesize_one(
        self,
        text: str,
        segment_id: int,
        rate: str | None = None,
    ) -> TTSResult:
        effective_rate = rate or "+0%"
        key = tts_cache_key(text, self._voice_name, effective_rate, engine=self.engine_id)
        wav_path = self._tts_dir / f"{key}.wav"

        if wav_path.exists():
            log.debug("TTS cache hit: seg %d → %s", segment_id, wav_path.name)
            return TTSResult(
                segment_id=segment_id,
                wav_path=wav_path,
                duration=_wav_duration(wav_path),
            )

        length_scale = _rate_to_length_scale(rate)
        await asyncio.to_thread(self._synthesize_sync, text, wav_path, length_scale)

        return TTSResult(
            segment_id=segment_id,
            wav_path=wav_path,
            duration=_wav_duration(wav_path),
        )

    async def synthesize_all(
        self,
        items: list[tuple[int, str]],
        request_delay: float = 0.0,  # piper is local — no delay needed
    ) -> list[TTSResult]:
        """Synthesize all segments sequentially (piper is CPU-bound, single model)."""
        results: list[TTSResult] = []
        for idx, (sid, text) in enumerate(items):
            result = await self.synthesize_one(text, sid)
            log.info(
                "Piper TTS [%d/%d] seg %d done (%.2fs)",
                idx + 1, len(items), sid, result.duration,
            )
            results.append(result)
            if self.on_progress:
                self.on_progress(idx + 1, len(items))
        return results

    # ── Internal ──────────────────────────────────────────────────────────────

    def _synthesize_sync(self, text: str, wav_path: Path, length_scale: float) -> None:
        """Blocking piper synthesis; runs in a thread via asyncio.to_thread."""
        from piper.config import SynthesisConfig

        syn_config = SynthesisConfig(length_scale=length_scale) if length_scale != 1.0 else None
        with wave.open(str(wav_path), "wb") as wf:
            self._voice.synthesize_wav(text, wf, syn_config=syn_config)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as wf:
        return wf.getnframes() / wf.getframerate()
