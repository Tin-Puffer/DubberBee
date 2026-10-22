"""TimingFitter: fit TTS audio into segment slots via 5-step algorithm."""
from __future__ import annotations

import asyncio
import logging
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Optional

import numpy as np

from dubber.core.tts.base import TTSEngine, TTSResult
from dubber.core.utils.ffmpeg_locator import no_window

log = logging.getLogger(__name__)

# Max seconds a segment may overflow into the silence after its end
MAX_OVERFLOW_S = 0.5
# 8% tolerance before triggering speed-up
OVERFLOW_TOLERANCE = 0.08
# Maximum atempo factor (FFmpeg atempo filter)
MAX_ATEMPO = 1.3
# Gap to leave before the next segment's start
NEXT_SEG_GAP = 0.1


@dataclass
class FittingResult:
    id: int
    vi_text: str
    tts_path: Path
    tts_duration: float
    final_start: float
    final_end: float
    speed: float   # 1.0 = original, >1.0 = sped up
    status: Literal["fits", "overflow", "sped_up", "shortened", "pushed"]


class TimingFitter:
    """Fit TTS results into their original segment time-slots.

    Steps (per segment):
      1. fits       — TTS duration ≤ slot duration
      2. overflow   — TTS overflows by ≤8%, allow it into trailing silence
      3. sped_up    — Re-synthesize at higher rate; then apply ffmpeg atempo ≤1.3×
      4. shortened  — Call optional *shortener* to get a shorter translation; re-TTS
      5. pushed     — Accept as-is, push next segment forward ≤0.5s
    """

    def __init__(
        self,
        tts_engine: TTSEngine,
        ffmpeg: Path,
        speed_dir: Path,
        shortener: Optional[Callable[[str, int], str]] = None,
    ) -> None:
        self._engine = tts_engine
        self._ffmpeg = ffmpeg
        self._speed_dir = speed_dir
        self._shortener = shortener
        speed_dir.mkdir(parents=True, exist_ok=True)

    def fit(
        self,
        tts_results: list[TTSResult],
        translate_data: list[dict],   # [{id, start, end, vi_text}]
        video_duration: float,
    ) -> list[FittingResult]:
        """Fit all TTS results and return FittingResult list."""
        # Build lookup maps
        tts_map = {r.segment_id: r for r in tts_results}
        seg_map = {d["id"]: d for d in translate_data}
        ids = sorted(seg_map)
        n = len(ids)
        results: list[FittingResult] = []

        # Track pushed offsets for each segment start
        pushed_starts = {d["id"]: d["start"] for d in translate_data}

        for idx, seg_id in enumerate(ids):
            seg = seg_map[seg_id]
            tts = tts_map.get(seg_id)
            if tts is None:
                log.warning("No TTS result for segment %d — skipping", seg_id)
                continue

            vi_text: str = seg["vi_text"]
            seg_start: float = pushed_starts[seg_id]
            seg_end: float = seg["end"] + (pushed_starts[seg_id] - seg["start"])
            slot_dur: float = seg["end"] - seg["start"]  # original slot

            # Compute extended slot: can overflow into silence up to next segment
            next_start = pushed_starts[ids[idx + 1]] if idx + 1 < n else video_duration
            extended_end = min(seg["end"] + MAX_OVERFLOW_S, next_start - NEXT_SEG_GAP)
            extended_slot = extended_end - seg_start

            tts_dur = tts.duration
            tts_path = tts.wav_path

            # ── Step 1: fits perfectly ────────────────────────────────────────
            if tts_dur <= slot_dur:
                results.append(FittingResult(
                    id=seg_id, vi_text=vi_text, tts_path=tts_path,
                    tts_duration=tts_dur,
                    final_start=seg_start, final_end=seg_start + tts_dur,
                    speed=1.0, status="fits",
                ))
                continue

            # ── Step 2: overflow within 8% of slot, into silence ─────────────
            if tts_dur <= slot_dur * (1 + OVERFLOW_TOLERANCE):
                if tts_dur <= extended_slot:
                    results.append(FittingResult(
                        id=seg_id, vi_text=vi_text, tts_path=tts_path,
                        tts_duration=tts_dur,
                        final_start=seg_start, final_end=seg_start + tts_dur,
                        speed=1.0, status="overflow",
                    ))
                    continue

            # ── Step 3: speed up ──────────────────────────────────────────────
            # 3a. Re-synthesize with edge-tts rate (+X%) — only for engines with a
            #     speed control; the others (VieNeu Turbo) go straight to atempo
            needed_ratio = tts_dur / extended_slot if extended_slot > 0 else 999
            if needed_ratio <= MAX_ATEMPO and getattr(self._engine, "supports_rate", True):
                rate_pct = min(int((needed_ratio - 1) * 100) + 5, 25)
                rate_str = f"+{rate_pct}%"
                try:
                    fast_tts: TTSResult = asyncio.run(
                        self._engine.synthesize_one(vi_text, seg_id, rate=rate_str)
                    )
                    if fast_tts.duration <= extended_slot:
                        results.append(FittingResult(
                            id=seg_id, vi_text=vi_text, tts_path=fast_tts.wav_path,
                            tts_duration=fast_tts.duration,
                            final_start=seg_start,
                            final_end=seg_start + fast_tts.duration,
                            speed=tts_dur / fast_tts.duration,
                            status="sped_up",
                        ))
                        continue
                    tts_path = fast_tts.wav_path
                    tts_dur = fast_tts.duration
                except Exception as exc:
                    log.warning("Rate re-synthesis failed for seg %d: %s", seg_id, exc)

            if needed_ratio <= MAX_ATEMPO:
                # 3b. Apply ffmpeg atempo
                atempo = min(tts_dur / extended_slot, MAX_ATEMPO)
                if atempo > 1.0:
                    speed_path = self._speed_dir / f"{tts_path.stem}_x{atempo:.2f}.wav"
                    try:
                        _apply_atempo(self._ffmpeg, tts_path, speed_path, atempo)
                        sped_dur = _wav_duration(speed_path)
                        if sped_dur <= extended_slot:
                            results.append(FittingResult(
                                id=seg_id, vi_text=vi_text, tts_path=speed_path,
                                tts_duration=sped_dur,
                                final_start=seg_start,
                                final_end=seg_start + sped_dur,
                                speed=atempo, status="sped_up",
                            ))
                            continue
                        tts_path = speed_path
                        tts_dur = sped_dur
                    except Exception as exc:
                        log.warning("atempo failed for seg %d: %s", seg_id, exc)

            # ── Step 4: shorten translation via optional callback ─────────────
            if self._shortener is not None:
                max_chars = max(8, int(extended_slot * 14))
                try:
                    short_text = self._shortener(vi_text, max_chars)
                    if short_text and short_text != vi_text:
                        short_tts = asyncio.run(
                            self._engine.synthesize_one(short_text, seg_id)
                        )
                        if short_tts.duration <= extended_slot:
                            results.append(FittingResult(
                                id=seg_id, vi_text=short_text,
                                tts_path=short_tts.wav_path,
                                tts_duration=short_tts.duration,
                                final_start=seg_start,
                                final_end=seg_start + short_tts.duration,
                                speed=1.0, status="shortened",
                            ))
                            continue
                        tts_path = short_tts.wav_path
                        tts_dur = short_tts.duration
                        vi_text = short_text
                except Exception as exc:
                    log.warning("Shortener failed for seg %d: %s", seg_id, exc)

            # ── Step 5: push next segment (accept overrun) ────────────────────
            push = min(tts_dur - extended_slot, MAX_OVERFLOW_S)
            if push > 0 and idx + 1 < n:
                next_id = ids[idx + 1]
                pushed_starts[next_id] = pushed_starts[next_id] + push
                log.warning(
                    "Seg %d overruns by %.2fs → pushing seg %d forward %.2fs",
                    seg_id, tts_dur - extended_slot, next_id, push,
                )
            results.append(FittingResult(
                id=seg_id, vi_text=vi_text, tts_path=tts_path,
                tts_duration=tts_dur,
                final_start=seg_start, final_end=seg_start + tts_dur,
                speed=tts_dur / slot_dur if slot_dur > 0 else 1.0,
                status="pushed",
            ))

        return results


# ── Helpers ───────────────────────────────────────────────────────────────────

def _wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as wf:
        return wf.getnframes() / wf.getframerate()


def _apply_atempo(ffmpeg: Path, src: Path, dst: Path, factor: float) -> None:
    """Apply FFmpeg atempo filter (0.5–2.0 range; chain for >2.0)."""
    if factor <= 2.0:
        filters = f"atempo={factor:.4f}"
    else:
        # Chain: atempo=2.0,atempo=remainder
        filters = f"atempo=2.0,atempo={factor/2.0:.4f}"

    subprocess.run(
        [
            str(ffmpeg), "-y", "-i", str(src),
            "-filter:a", filters,
            "-ac", "1", "-ar", "24000",
            str(dst),
        ],
        check=True,
        capture_output=True,
        **no_window(),
    )
