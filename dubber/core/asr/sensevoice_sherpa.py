"""ASR engine: SenseVoice via sherpa-onnx (ONNX runtime, no PyTorch)."""
from __future__ import annotations

import logging
import wave
from pathlib import Path
from typing import List, Literal, Optional, Tuple

import numpy as np

from dubber.core.asr.base import ASREngine, ASRSegment
from dubber.core.asr.postprocess import MAX_SEGMENT_S, VAD_MAX_SPEECH_S, SplitMode, postprocess

log = logging.getLogger(__name__)

VadKind = Literal["fsmn", "silero"]
VAD_MERGE_GAP_S = 0.3   # FSMN intervals closer than this are one stretch of speech


class SherpaOnnxASR(ASREngine):
    """Offline SenseVoice-Small via sherpa-onnx, cut by FSMN-VAD or Silero VAD.

    vad="fsmn" needs *fsmn_dir*; when it's missing or fails to load, Silero
    (*vad_model*) is used, and without either the whole file is one segment.
    ``vad_used`` tells which one actually ran.
    """

    def __init__(
        self,
        model_dir: Path,
        vad_model: Optional[Path] = None,
        language: str = "zh",
        use_itn: bool = True,
        num_threads: int = 4,
        split_mode: SplitMode = "timestamps",
        vad: VadKind = "fsmn",
        fsmn_dir: Optional[Path] = None,
    ) -> None:
        self._model_dir = Path(model_dir)
        self._split_mode = split_mode
        self._vad_kind = vad
        self._fsmn_dir = Path(fsmn_dir) if fsmn_dir else None
        self._fsmn = None
        self.vad_used = "none"
        self.cancel_check = None   # called per speech chunk; raises to abort
        self._vad_model = Path(vad_model) if vad_model else None
        self._language = language
        self._use_itn = use_itn
        self._num_threads = num_threads
        self._recognizer = None
        self._vad = None

    @property
    def name(self) -> str:
        return "sherpa-onnx"

    def _load(self) -> None:
        if self._recognizer is not None:
            return
        try:
            import sherpa_onnx
        except ImportError:
            raise RuntimeError("sherpa-onnx not installed. Run: pip install sherpa-onnx")

        model_file = self._model_dir / "model.int8.onnx"
        if not model_file.exists():
            model_file = self._model_dir / "model.onnx"
        tokens_file = self._model_dir / "tokens.txt"

        if not model_file.exists():
            raise FileNotFoundError(f"Model not found: {model_file}")
        if not tokens_file.exists():
            raise FileNotFoundError(f"Tokens not found: {tokens_file}")

        self._recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(model_file),
            tokens=str(tokens_file),
            language=self._language,
            use_itn=self._use_itn,
            num_threads=self._num_threads,
            debug=False,
        )
        log.info("sherpa-onnx ASR loaded from %s", self._model_dir)

        if self._vad_kind == "fsmn":
            if self._fsmn_dir and self._fsmn_dir.is_dir():
                try:
                    from dubber.core.asr.fsmn_vad import FsmnVAD
                    self._fsmn = FsmnVAD(self._fsmn_dir, num_threads=self._num_threads)
                    self.vad_used = "fsmn"
                    return
                except Exception as exc:
                    log.warning("FSMN-VAD failed to load (%s) — using Silero VAD", exc)
            else:
                log.warning("FSMN-VAD model not found — using Silero VAD")

        if self._vad_model and self._vad_model.exists():
            vad_cfg = sherpa_onnx.VadModelConfig(
                silero_vad=sherpa_onnx.SileroVadModelConfig(
                    model=str(self._vad_model),
                    threshold=0.5,
                    min_silence_duration=0.4,
                    min_speech_duration=0.25,
                    window_size=512,
                    # timestamps mode: VAD cuts coarsely, sentences are cut on token times
                    max_speech_duration=(VAD_MAX_SPEECH_S if self._split_mode == "timestamps"
                                         else MAX_SEGMENT_S),
                ),
                sample_rate=16000,
                num_threads=self._num_threads,
            )
            self._vad = sherpa_onnx.VoiceActivityDetector(vad_cfg, buffer_size_in_seconds=100)
            self.vad_used = "silero"
            log.info("sherpa-onnx VAD loaded from %s", self._vad_model)
        else:
            log.warning("VAD model not provided — single segment mode")

    def _read_wav(self, audio_path: Path):
        with wave.open(str(audio_path), "rb") as wf:
            assert wf.getnchannels() == 1, "Need mono WAV"
            assert wf.getsampwidth() == 2, "Need 16-bit PCM"
            sample_rate = wf.getframerate()
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        return samples, sample_rate, n_frames / sample_rate

    def _transcribe_chunk(self, samples: np.ndarray, sample_rate: int) -> str:
        return self._decode(samples, sample_rate)[0]

    def _decode(self, samples: np.ndarray, sample_rate: int) -> tuple[str, list]:
        """Return (text, [(token, time_in_chunk), …]) — tokens without special tags.

        Whitespace-only tokens are kept: in mixed text they are the space between words.
        """
        stream = self._recognizer.create_stream()
        stream.accept_waveform(sample_rate, samples)
        self._recognizer.decode_stream(stream)
        r = stream.result
        tokens = [
            (tok, float(t)) for tok, t in zip(getattr(r, "tokens", []), getattr(r, "timestamps", []))
            if tok and not tok.startswith("<|")
        ]
        return r.text.strip(), tokens

    def transcribe(self, audio_path: Path) -> List[ASRSegment]:
        self._load()
        samples, sample_rate, duration = self._read_wav(audio_path)

        if self._fsmn is not None:
            try:
                chunks = self._fsmn_chunks(samples, sample_rate)
            except Exception as exc:
                log.warning("FSMN-VAD failed (%s) — decoding the whole file as one segment", exc)
                chunks = [(0, samples)]
        elif self._vad is not None:
            chunks = self._silero_chunks(samples)
        else:
            text = self._transcribe_chunk(samples, sample_rate)
            return [ASRSegment(start=0.0, end=duration, text=text)]

        segments: List[ASRSegment] = []
        for start_sample, chunk in chunks:
            if self.cancel_check:
                self.cancel_check()
            start = start_sample / sample_rate
            end = start + len(chunk) / sample_rate

            text, tokens = self._decode(chunk, sample_rate)
            if not text:
                continue
            segments.append(ASRSegment(
                start=round(start, 3), end=round(end, 3), text=text,
                token_times=[(tok, start + t) for tok, t in tokens] or None,
            ))
            log.debug("[%.2f-%.2f] %s", start, end, text[:60])

        if not segments:
            log.warning("VAD found no speech segments, falling back to full-audio decode")
            text = self._transcribe_chunk(samples, sample_rate)
            return [ASRSegment(start=0.0, end=duration, text=text)]

        return postprocess(segments, mode=self._split_mode)

    # ── VAD ───────────────────────────────────────────────────────────────────

    def _silero_chunks(self, samples: np.ndarray) -> List[Tuple[int, np.ndarray]]:
        self._vad.reset()   # start offsets would carry over from a previous file
        window = 512
        for i in range(0, len(samples), window):
            self._vad.accept_waveform(samples[i : i + window])
        self._vad.flush()
        chunks = []
        while not self._vad.empty():
            # Copy data before pop() — front reference is invalidated after any method call
            speech = self._vad.front
            chunks.append((speech.start, np.array(speech.samples, dtype=np.float32)))
            self._vad.pop()
        return chunks

    def _fsmn_chunks(self, samples: np.ndarray, sr: int) -> List[Tuple[int, np.ndarray]]:
        max_len = int(VAD_MAX_SPEECH_S * sr)
        spans = [(int(s * sr), min(len(samples), int(e * sr)))
                 for s, e in self._fsmn.segments(samples)]
        spans = merge_close_spans(spans, int(VAD_MERGE_GAP_S * sr), max_len)
        out = []
        for s, e in spans:
            for a, b in split_long_span(samples, s, e, max_len, sr):
                out.append((a, samples[a:b]))
        return out


def merge_close_spans(spans: List[Tuple[int, int]], max_gap: int, max_len: int
                      ) -> List[Tuple[int, int]]:
    """Join spans separated by ≤ max_gap samples, unless the result exceeds max_len."""
    out: List[Tuple[int, int]] = []
    for s, e in spans:
        if out and s - out[-1][1] <= max_gap and e - out[-1][0] <= max_len:
            out[-1] = (out[-1][0], e)
        else:
            out.append((s, e))
    return out


def split_long_span(samples: np.ndarray, s: int, e: int, max_len: int, sr: int
                    ) -> List[Tuple[int, int]]:
    """Cut [s, e) into pieces ≤ max_len at the quietest 100 ms within each piece's reach."""
    win = sr // 10
    out: List[Tuple[int, int]] = []
    while e - s > max_len:
        lo, hi = s + max_len // 3, s + max_len - win
        frames = samples[lo:hi][: (hi - lo) // win * win].reshape(-1, win)
        cut = lo + int(np.argmin((frames ** 2).mean(axis=1))) * win + win // 2
        out.append((s, cut))
        s = cut
    out.append((s, e))
    return out
