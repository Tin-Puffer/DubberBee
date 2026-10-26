"""Mix TTS segments into a single dub.wav silent track."""
from __future__ import annotations

import logging
import wave
from pathlib import Path

import numpy as np

from dubber.core.audio.fit import FittingResult

log = logging.getLogger(__name__)


def build_dub_wav(
    segments: list[FittingResult],
    total_duration: float,
    output_path: Path,
    sample_rate: int | None = None,
) -> Path:
    """Assemble all TTS segments on a silent timeline and write dub.wav.

    Args:
        segments: Fitted segment list from TimingFitter.
        total_duration: Length of the original video in seconds.
        output_path: Where to write the output WAV.
        sample_rate: Output sample rate; default = the TTS voice's own rate, so the
            speech is never resampled here (piper 22 050 Hz, VieNeu 48 000 Hz).

    Returns:
        The path to the written WAV file.
    """
    clips = []
    for seg in segments:
        try:
            clips.append((seg, *_read_wav_float32(seg.tts_path)))
        except Exception as exc:
            log.warning("Cannot read TTS for seg %d (%s): %s", seg.id, seg.tts_path, exc)
    if sample_rate is None:
        sample_rate = clips[0][2] if clips else 24_000

    total_samples = int(total_duration * sample_rate) + 1
    track = np.zeros(total_samples, dtype=np.float32)

    for seg, seg_samples, sr in clips:
        if sr != sample_rate:
            seg_samples = _resample(seg_samples, sr, sample_rate)

        start_sample = int(seg.final_start * sample_rate)
        end_sample = min(start_sample + len(seg_samples), total_samples)
        length = end_sample - start_sample
        if length <= 0:
            log.warning("Seg %d starts after the end of the video — dropped", seg.id)
            continue
        if length < len(seg_samples):
            log.warning("Seg %d runs past the end of the video — cut", seg.id)
        track[start_sample:end_sample] += seg_samples[:length]

    _write_wav_float32(output_path, track, sample_rate)
    log.info("dub.wav written: %d segments, %.1fs @ %d Hz → %s",
             len(segments), total_duration, sample_rate, output_path)
    return output_path


# ── WAV I/O ───────────────────────────────────────────────────────────────────

def _read_wav_float32(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        n = wf.getnframes()
        bps = wf.getsampwidth()
        raw = wf.readframes(n)

    if bps == 2:
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif bps == 4:
        samples = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    elif bps == 1:
        samples = np.frombuffer(raw, dtype=np.uint8).astype(np.float32) / 128.0 - 1.0
    else:
        raise ValueError(f"Unsupported sample width: {bps}")
    return samples, sr


def _write_wav_float32(path: Path, samples: np.ndarray, sr: int) -> None:
    # In pieces: a 30-min track at 48 kHz is 350 MB as float32, each full-length
    # temporary copy as much again
    step = sr * 60
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        for i in range(0, len(samples), step):
            part = np.clip(samples[i:i + step], -1.0, 1.0) * 32767
            wf.writeframes(part.astype(np.int16).tobytes())


def _resample(samples: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    """Linear-interpolation resample (only for mixed-rate input; normally unused)."""
    if src_sr == dst_sr or len(samples) == 0:
        return samples
    n_out = int(len(samples) * dst_sr / src_sr)
    t_out = np.arange(n_out) * (src_sr / dst_sr)
    return np.interp(t_out, np.arange(len(samples)), samples).astype(np.float32)
