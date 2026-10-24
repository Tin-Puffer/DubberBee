"""TimingFitter: lay the Vietnamese TTS lines on the video timeline.

Rules, in order of priority:
  1. Lines never overlap — each one starts after the previous one has finished plus a pause.
  2. Natural pauses: SENTENCE_GAP after ". ! ? …", CLAUSE_GAP otherwise; where the
     Chinese speaker paused longer, the line still starts at its own Chinese time.
  3. A line may start up to MAX_LAG after its Chinese line; the voice catches up again
     at the speaker's next pause.
  4. To stay within that lag the whole video is sped up evenly (≤ MAX_GLOBAL_SPEED) and
     single lines a bit more (≤ MAX_SPEED in total — applied once, never stacked).
  5. Lines that still don't fit are shortened (optional *shortener*, e.g. Groq) and spoken
     again; whatever is left over only delays the following lines.

TTS engines pad every line with silence (piper: ~0.1 s before, ~0.3 s after); it is
trimmed off so the pauses between lines are the ones chosen here.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Optional

import numpy as np

from dubber.core.tts.base import TTSEngine, TTSResult
from dubber.core.utils.ffmpeg_locator import no_window

log = logging.getLogger(__name__)

# Pause before a line that follows a finished sentence / an unfinished clause
SENTENCE_GAP = 0.3
CLAUSE_GAP = 0.12
# A line may start this much later than its Chinese line
MAX_LAG = 1.5
# Even speed-up for the whole video; and the most one line is ever sped up
MAX_GLOBAL_SPEED = 1.15
MAX_SPEED = 1.3
# With a shortener: lines needing more than SHORTEN_ABOVE to fit before the next Chinese
# line are rewritten to be spoken at about COMFORT_SPEED
SHORTEN_ABOVE = 1.2
COMFORT_SPEED = 1.1
# Silence kept around the trimmed speech, and the fade over it (no clicks)
EDGE_PAD = 0.03
FADE_S = 0.01
# Frames quieter than this (RMS, 10 ms) are silence when trimming
SILENCE_RMS = 0.01
SHORTEN_ROUNDS = 2

_SENTENCE_END = re.compile(r"[.!?…。！？]['\"”’)\]]*\s*$")


@dataclass
class FittingResult:
    id: int
    vi_text: str
    tts_path: Path
    tts_duration: float
    final_start: float
    final_end: float
    speed: float   # 1.0 = original, >1.0 = sped up
    status: Literal["fits", "sped_up", "shortened", "late"]


@dataclass
class _Line:
    id: int
    start: float          # Chinese line start / end
    end: float
    text: str
    src_text: str
    wav: Path             # trimmed speech at normal speed
    dur: float
    shortened: bool = False

    @property
    def ends_sentence(self) -> bool:
        # The Chinese tells it best: the Vietnamese clean-up ends every line with "."
        return bool(_SENTENCE_END.search(self.src_text or self.text))


@dataclass
class _Slot:
    start: float
    speed: float
    end: float
    overflow: float       # seconds past the latest end that keeps the next line in MAX_LAG


class TimingFitter:
    """Place TTS lines without overlap; see the module docstring for the rules.

    shortener(items) gets [{"id", "vi", "zh", "max_chars"}] and returns {id: shorter vi}.
    """

    def __init__(
        self,
        tts_engine: TTSEngine,
        ffmpeg: Path,
        speed_dir: Path,
        shortener: Optional[Callable[[list[dict]], dict[int, str]]] = None,
    ) -> None:
        self._engine = tts_engine
        self._ffmpeg = ffmpeg
        self._speed_dir = speed_dir
        self._shortener = shortener
        speed_dir.mkdir(parents=True, exist_ok=True)

    def fit(
        self,
        tts_results: list[TTSResult],
        translate_data: list[dict],   # [{id, start, end, vi_text, src_text?}]
        video_duration: float,
    ) -> list[FittingResult]:
        tts_map = {r.segment_id: r for r in tts_results}
        lines: list[_Line] = []
        for seg in sorted(translate_data, key=lambda d: (d["start"], d["id"])):
            tts = tts_map.get(seg["id"])
            if tts is None:
                if seg.get("vi_text"):
                    log.warning("No TTS result for segment %d — skipping", seg["id"])
                continue
            wav, dur = self._trimmed(tts.wav_path)
            lines.append(_Line(seg["id"], float(seg["start"]), float(seg["end"]),
                               seg["vi_text"], seg.get("src_text", ""), wav, dur))
        if not lines:
            return []

        for _ in range(SHORTEN_ROUNDS if self._shortener else 0):
            if not self._shorten(lines, video_duration):
                break
        speed, slots = choose_speed(lines, video_duration)
        log.info("Timing: even speed %.2f×, %d lines still past their window",
                 speed, sum(s.overflow > 0.05 for s in slots))

        return self._render(lines, slots)

    # ── Shortening ────────────────────────────────────────────────────────────

    def _shorten(self, lines: list[_Line], video_duration: float) -> bool:
        """Ask the shortener for lines much too long for their own time; True if any changed.

        Judged per line (until the next Chinese line starts), not on the planned timeline —
        there one long line would make every later line look late.
        """
        items = []
        for i, line in enumerate(lines):
            nxt = lines[i + 1].start - _gap(line) if i + 1 < len(lines) else video_duration
            need = line.dur / max(nxt - line.start, 0.1)
            if need <= SHORTEN_ABOVE:
                continue
            keep = max(0.4, COMFORT_SPEED / need)
            items.append({"id": line.id, "vi": line.text, "zh": line.src_text,
                          "max_chars": max(6, int(len(line.text) * keep))})
        if not items:
            return False
        log.info("Shortening %d line(s) that don't fit", len(items))
        try:
            short = self._shortener(items) or {}
        except Exception as exc:
            log.warning("Shortener failed: %s", exc)
            return False
        changed = False
        for line in lines:
            text = (short.get(line.id) or "").strip()
            if not text or len(text) >= len(line.text):
                continue
            try:
                tts = asyncio.run(self._engine.synthesize_one(text, line.id))
            except Exception as exc:
                log.warning("TTS of shortened line %d failed: %s", line.id, exc)
                continue
            line.text, line.shortened = text, True
            line.wav, line.dur = self._trimmed(tts.wav_path)
            changed = True
        return changed

    # ── Audio ─────────────────────────────────────────────────────────────────

    def _render(self, lines: list[_Line], slots: list[_Slot]) -> list[FittingResult]:
        """Make each line's audio at its planned speed, then place lines on the real durations
        (a sped-up line rarely comes out exactly as planned) — still never overlapping."""
        results: list[FittingResult] = []
        prev_end, prev_line = -math.inf, None
        for line, slot in zip(lines, slots):
            wav, dur, speed = line.wav, line.dur, slot.speed
            if speed > 1.005:
                try:
                    wav, dur = self._speed_up(line, speed)
                except Exception as exc:
                    log.warning("Speed-up failed for seg %d: %s", line.id, exc)
                    speed = 1.0
            else:
                speed = 1.0
            gap = _gap(prev_line)
            start = max(line.start, prev_end + gap)
            if line.shortened:
                status = "shortened"
            elif start - line.start > 0.05:
                status = "late"
            elif speed > 1.0:
                status = "sped_up"
            else:
                status = "fits"
            results.append(FittingResult(
                id=line.id, vi_text=line.text, tts_path=wav, tts_duration=dur,
                final_start=start, final_end=start + dur, speed=speed, status=status,
            ))
            prev_end, prev_line = start + dur, line
        return results

    def _speed_up(self, line: _Line, speed: float) -> tuple[Path, float]:
        """Line audio at *speed*: the engine's own rate first (sounds more natural), atempo
        for what it falls short of — piper voices often follow length_scale only partly."""
        src, dur = line.wav, line.dur
        if getattr(self._engine, "supports_rate", True):
            rate = f"+{math.ceil((speed - 1) * 100)}%"
            tts = asyncio.run(self._engine.synthesize_one(line.text, line.id, rate=rate))
            src, dur = self._trimmed(tts.wav_path)
        rest = line.dur / speed
        if dur <= rest * 1.01:
            return src, dur
        factor = min(dur / rest, MAX_SPEED)
        dst = self._speed_dir / f"{src.stem}_x{factor:.3f}.wav"
        if not dst.exists():
            _apply_atempo(self._ffmpeg, src, dst, factor)
        return dst, _wav_duration(dst)

    def _trimmed(self, src: Path) -> tuple[Path, float]:
        """*src* without the engine's leading / trailing silence (cached in speed_dir)."""
        dst = self._speed_dir / f"{src.stem}_t.wav"
        if not dst.exists():
            samples, sr, width = _read_pcm(src)
            _write_pcm(dst, trim_silence(samples, sr), sr, width)
        return dst, _wav_duration(dst)


# ── Planning (pure) ───────────────────────────────────────────────────────────

def _gap(prev: "_Line | None") -> float:
    if prev is None:
        return 0.0
    return SENTENCE_GAP if prev.ends_sentence else CLAUSE_GAP


def plan_timeline(lines: list[_Line], video_duration: float, speed: float,
                  max_speed: float = MAX_SPEED) -> list[_Slot]:
    """Where each line goes when the whole video is sped up by *speed*.

    A line starts at its Chinese time, or after the previous line + pause if that is
    later. It must end early enough for the next line to start within MAX_LAG of its own
    Chinese time; it is sped up (≤ max_speed) to get there, the rest is overflow.
    A line that doesn't delay the next one at normal speed (a pause follows) isn't sped up.
    """
    slots: list[_Slot] = []
    prev_end, prev = -math.inf, None
    for i, line in enumerate(lines):
        start = max(line.start, prev_end + _gap(prev))
        if i + 1 < len(lines):
            free_end = lines[i + 1].start - _gap(line)
            limit = free_end + MAX_LAG
        else:
            free_end = limit = max(video_duration, line.end)
        room = limit - start
        if start + line.dur <= free_end:
            s = 1.0
        else:
            s = speed if room <= 0 else max(speed, line.dur / room)
            s = min(s, max_speed)
        end = start + line.dur / s
        slots.append(_Slot(start, s, end, max(0.0, end - limit)))
        prev_end, prev = end, line
    return slots


def choose_speed(lines: list[_Line], video_duration: float) -> tuple[float, list[_Slot]]:
    """Lowest even speed (≤ MAX_GLOBAL_SPEED) that keeps every line in its window.

    When none does, the fastest — fewest leftovers to shorten or delay.
    """
    best = None
    for step in range(int(round((MAX_GLOBAL_SPEED - 1) * 100)) + 1):
        speed = 1 + step / 100
        slots = plan_timeline(lines, video_duration, speed)
        if sum(s.overflow for s in slots) <= 0.05:
            return speed, slots
        best = (speed, slots)
    return best


def trim_silence(samples: np.ndarray, sr: int) -> np.ndarray:
    """Drop leading / trailing silence, keep EDGE_PAD around the speech, fade its edges."""
    x = samples.astype(np.float32)
    peak = float(np.abs(x).max()) if len(x) else 0.0
    if peak == 0.0:
        return samples
    frame = max(1, sr // 100)
    n = len(x) // frame
    if n == 0:
        return samples
    rms = np.sqrt(np.mean(x[: n * frame].reshape(n, frame) ** 2, axis=1))
    loud = np.where(rms > min(SILENCE_RMS, peak * 0.05))[0]   # quiet voices too
    if len(loud) == 0:
        return samples
    pad = int(EDGE_PAD * sr)
    a = max(0, loud[0] * frame - pad)
    b = min(len(x), (loud[-1] + 1) * frame + pad)
    out = samples[a:b].astype(np.float32)
    fade = min(int(FADE_S * sr), len(out) // 2)
    if fade:
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        out[:fade] *= ramp
        out[-fade:] *= ramp[::-1]
    return out


# ── Helpers ───────────────────────────────────────────────────────────────────

def _wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as wf:
        return wf.getnframes() / wf.getframerate()


def _read_pcm(path: Path) -> tuple[np.ndarray, int, int]:
    """Mono samples as float32 in −1…1, sample rate, sample width (bytes)."""
    with wave.open(str(path), "rb") as wf:
        sr, width, ch = wf.getframerate(), wf.getsampwidth(), wf.getnchannels()
        raw = wf.readframes(wf.getnframes())
    if width == 2:
        x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif width == 4:
        x = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"Unsupported sample width: {width}")
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, sr, width


def _write_pcm(path: Path, x: np.ndarray, sr: int, width: int = 2) -> None:
    x = np.clip(x, -1.0, 1.0)
    data = (x * 32767).astype(np.int16) if width == 2 else (x * 2147483647).astype(np.int32)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2 if width == 2 else 4)
        wf.setframerate(sr)
        wf.writeframes(data.tobytes())


def _apply_atempo(ffmpeg: Path, src: Path, dst: Path, factor: float) -> None:
    """FFmpeg atempo (0.5–2.0); keeps the source's sample rate."""
    subprocess.run(
        [str(ffmpeg), "-y", "-i", str(src), "-filter:a", f"atempo={factor:.4f}",
         "-ac", "1", "-c:a", "pcm_s16le", str(dst)],
        check=True,
        capture_output=True,
        **no_window(),
    )
