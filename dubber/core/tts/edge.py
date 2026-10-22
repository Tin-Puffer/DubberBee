"""EdgeTTS synthesis engine with async semaphore, retry, and cache."""
from __future__ import annotations

import asyncio
import logging
import random
import subprocess
import wave
from pathlib import Path

from dubber.core.tts.base import TTSEngine, TTSResult, tts_cache_key

log = logging.getLogger(__name__)

FEMALE_VOICE = "vi-VN-HoaiMyNeural"
MALE_VOICE = "vi-VN-NamMinhNeural"
DEFAULT_VOICE = FEMALE_VOICE

_MAX_RETRIES = 3
_BASE_BACKOFF = 1.0


class EdgeTTSEngine(TTSEngine):
    """Synthesize Vietnamese TTS via edge-tts.

    Args:
        voice: Edge-TTS voice name (default: vi-VN-HoaiMyNeural).
        rate: Base speaking-rate modifier, e.g. "+0%" or "+25%".
        tts_dir: Directory to cache WAV files.
        ffmpeg: Path to ffmpeg binary (for MP3→WAV conversion).
        max_concurrent: Max parallel TTS requests.
    """

    def __init__(
        self,
        voice: str = DEFAULT_VOICE,
        rate: str = "+0%",
        tts_dir: Path = Path("."),
        ffmpeg: Path = Path("ffmpeg"),
        max_concurrent: int = 3,
    ) -> None:
        self._voice = voice
        self._rate = rate
        self._tts_dir = tts_dir
        self._ffmpeg = ffmpeg
        self._sem = asyncio.Semaphore(max_concurrent)
        tts_dir.mkdir(parents=True, exist_ok=True)

    # ── Public API ────────────────────────────────────────────────────────────

    async def synthesize_all(
        self,
        items: list[tuple[int, str]],
        request_delay: float = 5.0,
    ) -> list[TTSResult]:
        """Sequential synthesis with delay to avoid edge-tts rate limits.

        Microsoft's unofficial TTS endpoint rate-limits after 2-3 rapid
        requests from the same IP.  Sequential + 5 s gap keeps us under
        the threshold (7 segments ≈ 60 s total wall clock).
        """
        results: list[TTSResult] = []
        for idx, (sid, text) in enumerate(items):
            if idx > 0:
                await asyncio.sleep(request_delay)
            result = await self.synthesize_one(text, sid)
            results.append(result)
            log.info("TTS [%d/%d] seg %d done (%.2fs)", idx + 1, len(items), sid, result.duration)
        return results

    async def synthesize_one(
        self,
        text: str,
        segment_id: int,
        rate: str | None = None,
    ) -> TTSResult:
        effective_rate = rate if rate is not None else self._rate
        key = tts_cache_key(text, self._voice, effective_rate)
        wav_path = self._tts_dir / f"{key}.wav"

        if wav_path.exists():
            log.debug("TTS cache hit: seg %d → %s", segment_id, wav_path.name)
            return TTSResult(
                segment_id=segment_id,
                wav_path=wav_path,
                duration=_wav_duration(wav_path),
            )

        async with self._sem:
            # Double-check after acquiring semaphore (another coroutine may have written it)
            if wav_path.exists():
                return TTSResult(
                    segment_id=segment_id,
                    wav_path=wav_path,
                    duration=_wav_duration(wav_path),
                )
            await self._synthesize_with_retry(text, effective_rate, wav_path)

        return TTSResult(
            segment_id=segment_id,
            wav_path=wav_path,
            duration=_wav_duration(wav_path),
        )

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _synthesize_with_retry(
        self, text: str, rate: str, wav_path: Path
    ) -> None:
        for attempt in range(_MAX_RETRIES):
            try:
                await self._do_synthesize(text, rate, wav_path)
                return
            except Exception as exc:
                if attempt < _MAX_RETRIES - 1:
                    delay = _BASE_BACKOFF * (2 ** attempt) + random.uniform(0, 0.5)
                    short_err = str(exc).split("\n")[-1][:120]
                    log.warning(
                        "TTS attempt %d/%d failed (%s), retry in %.1fs",
                        attempt + 1, _MAX_RETRIES, short_err, delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    raise

    async def _do_synthesize(self, text: str, rate: str, wav_path: Path) -> None:
        """Call edge-tts CLI and convert MP3 → WAV.  Override in tests.

        Uses the CLI (subprocess) instead of the Python asyncio API because
        the Python API shares SSL/WebSocket session state across calls within
        the same event loop on Windows, causing 'NoAudioReceived' on all but
        the first request.  Each subprocess spawns a fresh event loop.
        """
        mp3_tmp = wav_path.with_suffix(".mp3")
        cli = _find_edge_tts_cli()
        cmd = [
            cli, "--voice", self._voice,
            "--rate", rate,
            "--text", text,
            "--write-media", str(mp3_tmp),
        ]
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"edge-tts CLI failed (rc={result.returncode}): "
                f"{result.stderr.decode(errors='replace').strip()}"
            )
        self._convert_mp3_to_wav(mp3_tmp, wav_path)
        mp3_tmp.unlink(missing_ok=True)

    def _convert_mp3_to_wav(self, mp3: Path, wav: Path) -> None:
        """Convert mp3 to 24 kHz mono PCM WAV via ffmpeg."""
        subprocess.run(
            [
                str(self._ffmpeg), "-y", "-i", str(mp3),
                "-ac", "1", "-ar", "24000",
                str(wav),
            ],
            check=True,
            capture_output=True,
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _find_edge_tts_cli() -> str:
    """Locate the edge-tts CLI executable installed in the same venv."""
    import shutil
    import sys

    # 1. Same directory as the Python executable (venv Scripts/bin)
    scripts = Path(sys.executable).parent
    for name in ("edge-tts.exe", "edge-tts"):
        candidate = scripts / name
        if candidate.exists():
            return str(candidate)

    # 2. System PATH
    found = shutil.which("edge-tts")
    if found:
        return found

    raise FileNotFoundError(
        "edge-tts CLI not found. Install with: pip install edge-tts"
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _wav_duration(path: Path) -> float:
    """Return duration in seconds of a PCM WAV file."""
    with wave.open(str(path), "rb") as wf:
        return wf.getnframes() / wf.getframerate()
