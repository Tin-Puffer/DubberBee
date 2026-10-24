"""TimingFitter: lay the Vietnamese TTS lines on the video timeline.

Rules, in order of priority:
  1. Lines never overlap — each one starts after the previous one has finished plus a pause.
  2. Natural pauses: SENTENCE_GAP after ". ! ? …", CLAUSE_GAP otherwise; where the
     Chinese speaker paused longer, the line still starts at its own Chinese time.
  3. A line may start up to MAX_LAG after its Chinese line; the voice catches up again
     at the speaker's next pause.
  4. To stay within that lag the whole video is sped up evenly (≤ MAX_GLOBAL_SPEED) and
     single lines a bit more (≤ MAX_SPEED in total), with FFmpeg atempo — piper voices
     follow their own rate setting only about half-way, so re-synthesizing was wasted work.
  5. Lines that still don't fit are shortened (optional *shortener*, e.g. Groq) and spoken
     again; whatever is left over only delays the following lines.

TTS engines pad every line with silence (piper: ~0.1 s before, ~0.3 s after); it is
trimmed off so the pauses between lines are the ones chosen here. Every line is also
brought to the same speech loudness (SPEECH_DBFS): VieNeu speaks ~7 dB quieter than piper,
so switching voices no longer changes the voice / music balance.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import subprocess
import wave
from concurrent.futures import ThreadPoolExecutor
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
# Speech loudness every line is brought to (RMS of its voiced 10 ms frames). Measured:
# piper ≈ −11.5 dBFS, VieNeu ≈ −18.6, each within ±1 dB line to line
SPEECH_DBFS = -14.0
MAX_GAIN_DB = 12.0
# Peaks above this are softly limited (never past full scale): VieNeu peaks ~17 dB over
# its speech level, so a +4.6 dB lift would otherwise clip
LIMIT_FROM = 0.7
SHORTEN_ROUNDS = 2
# Trims / atempo runs at once (FFmpeg processes; numpy releases the GIL)
WORKERS = min(8, os.cpu_count() or 4)

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
        segs = []
        for seg in sorted(translate_data, key=lambda d: (d["start"], d["id"])):
            if seg["id"] in tts_map:
                segs.append(seg)
            elif seg.get("vi_text"):
                log.warning("No TTS result for segment %d — skipping", seg["id"])
        with ThreadPoolExecutor(WORKERS) as pool:
            trimmed = list(pool.map(lambda s: self._trimmed(tts_map[s["id"]].wav_path), segs))
        lines = [_Line(seg["id"], float(seg["start"]), float(seg["end"]), seg["vi_text"],
                       seg.get("src_text", ""), wav, dur)
                 for seg, (wav, dur) in zip(segs, trimmed)]
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
        with ThreadPoolExecutor(WORKERS) as pool:
            audio = list(pool.map(self._at_speed, lines, [s.speed for s in slots]))
        results: list[FittingResult] = []
        prev_end, prev_line = -math.inf, None
        for line, (wav, dur, speed) in zip(lines, audio):
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

    def _at_speed(self, line: _Line, speed: float) -> tuple[Path, float, float]:
        """(wav, duration, speed actually applied) of *line* at *speed* — atempo; on
        failure the line keeps its normal speed (placement still avoids overlaps)."""
        if speed <= 1.005:
            return line.wav, line.dur, 1.0
        dst = self._speed_dir / f"{line.wav.stem}_x{speed:.3f}.wav"
        try:
            if not dst.exists():
                part = dst.with_name(dst.stem + ".part.wav")   # never reuse a half-written file
                _apply_atempo(self._ffmpeg, line.wav, part, speed)
                part.replace(dst)
            return dst, _wav_duration(dst), speed
        except Exception as exc:
            log.warning("Speed-up failed for seg %d: %s", line.id, exc)
            return line.wav, line.dur, 1.0

    def _trimmed(self, src: Path) -> tuple[Path, float]:
        """*src* without the engine's leading / trailing silence, at the common speech
        loudness (cached in speed_dir)."""
        dst = self._speed_dir / f"{src.stem}_tn.wav"
        if not dst.exists():
            samples, sr, width = _read_pcm(src)
            part = dst.with_name(dst.stem + ".part.wav")
            _write_pcm(part, normalize_speech(trim_silence(samples, sr), sr), sr, width)
            part.replace(dst)
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


def _voiced_rms(x: np.ndarray, sr: int) -> float:
    """RMS of the 10 ms frames that carry speech (0.0 when there are none)."""
    frame = max(1, sr // 100)
    n = len(x) // frame
    if n == 0:
        return 0.0
    rms = np.sqrt(np.mean(x[: n * frame].reshape(n, frame) ** 2, axis=1))
    peak = float(np.abs(x).max())
    voiced = rms[rms > max(peak * 0.05, 1e-4)]   # relative: very quiet voices count too
    return float(np.sqrt(np.mean(voiced ** 2))) if len(voiced) else 0.0


def normalize_speech(x: np.ndarray, sr: int) -> np.ndarray:
    """Bring the speech to SPEECH_DBFS (gain ≤ ±MAX_GAIN_DB), soft-limit the peaks."""
    x = x.astype(np.float32)
    level = _voiced_rms(x, sr)
    if level <= 0.0:
        return x
    gain_db = float(np.clip(SPEECH_DBFS - 20 * np.log10(level), -MAX_GAIN_DB, MAX_GAIN_DB))
    y = x * np.float32(10 ** (gain_db / 20))
    over = np.abs(y) > LIMIT_FROM
    if over.any():
        head = 1.0 - LIMIT_FROM
        a = np.abs(y[over])
        y[over] = np.sign(y[over]) * (LIMIT_FROM + head * np.tanh((a - LIMIT_FROM) / head))
    return y


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
