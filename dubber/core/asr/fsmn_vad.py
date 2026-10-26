"""FSMN-VAD (FunASR, Chinese 16 kHz) on onnxruntime — offline, whole-file mode.

Ported from funasr-onnx 0.4.3 (``vad_bin.Fsmn_vad`` + ``utils/frontend.WavFrontend``,
MIT License, https://github.com/modelscope/FunASR) without its librosa/scipy/numpy<2
dependencies. The post-processing state machine is vendored as ``_fsmn_e2e_vad``.

Model: ModelScope ``iic/speech_fsmn_vad_zh-cn-16k-common-onnx`` (Apache-2.0):
``model_quant.onnx`` + ``config.yaml`` + ``am.mvn`` in one directory.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Tuple

import numpy as np

log = logging.getLogger(__name__)

_CHUNK_FRAMES = 6000   # feature frames per inference call (60 s), as upstream
_FEED_SAMPLES = 16000 * 30   # samples handed to the fbank at a time


class FsmnVAD:
    """Speech intervals for a whole 16 kHz mono signal."""

    def __init__(self, model_dir: Path, num_threads: int = 4) -> None:
        import onnxruntime as ort
        import yaml

        model_dir = Path(model_dir)
        model_file = model_dir / "model_quant.onnx"
        if not model_file.exists():
            model_file = model_dir / "model.onnx"
        cfg = yaml.safe_load((model_dir / "config.yaml").read_text(encoding="utf-8"))

        self._frontend_conf = cfg["frontend_conf"]
        self._vad_conf = cfg["model_conf"]
        enc = cfg["encoder_conf"]
        self._cache_shape = (1, enc["proj_dim"], enc["lorder"] - 1, 1)
        self._n_layers = enc["fsmn_layers"]
        self._cmvn = load_cmvn(model_dir / "am.mvn")

        so = ort.SessionOptions()
        so.intra_op_num_threads = num_threads
        so.log_severity_level = 3
        self._sess = ort.InferenceSession(str(model_file), so, providers=["CPUExecutionProvider"])
        self._in_names = [i.name for i in self._sess.get_inputs()]
        log.info("FSMN-VAD loaded from %s", model_dir)

    # ── Public API ────────────────────────────────────────────────────────────

    def segments(self, samples: np.ndarray) -> List[Tuple[float, float]]:
        """[(start_s, end_s), …] for float32 samples in [-1, 1] at 16 kHz."""
        from dubber.core.asr._fsmn_e2e_vad import E2EVadModel

        # Features are made one inference chunk at a time: for a 30-min video the whole
        # matrix is 180 000 × 400 floats, and building it at once peaked at ~2.5 GB
        fbank = self._fbank(samples)
        n = self._n_lfr(fbank)
        if n == 0:
            return []
        scorer = E2EVadModel(self._vad_conf)
        max_end_sil = self._vad_conf["max_end_silence_time"]
        caches = [np.zeros(self._cache_shape, dtype=np.float32) for _ in range(self._n_layers)]
        wave = samples[None, :]
        out: List[List[int]] = []
        for off in range(0, n, _CHUNK_FRAMES):
            end = min(n, off + _CHUNK_FRAMES)
            is_final = end >= n - 1
            if is_final:
                end = n
            outputs = self._sess.run(None, dict(zip(self._in_names,
                                                    [self._lfr_cmvn(fbank, off, end)[None], *caches])))
            scores, caches = outputs[0], outputs[1:]
            # waveform slice matching these frames (10 ms shift, 25 ms window)
            wave_part = wave[:, off * 160: min(wave.shape[-1], (end - 1) * 160 + 400)]
            for batch in scorer(scores, wave_part, is_final=is_final,
                                max_end_sil=max_end_sil, online=False):
                out.extend(batch)
            if is_final:
                break
        return [(s / 1000.0, e / 1000.0) for s, e in out if e > s]

    def features(self, samples: np.ndarray) -> np.ndarray:
        """fbank → LFR → CMVN, shape (frames, n_mels * lfr_m)."""
        fbank = self._fbank(samples)
        return self._lfr_cmvn(fbank, 0, self._n_lfr(fbank))

    def _fbank(self, samples: np.ndarray) -> np.ndarray:
        """Kaldi fbank, shape (frames, n_mels)."""
        import kaldi_native_fbank as knf

        c = self._frontend_conf
        opts = knf.FbankOptions()
        opts.frame_opts.samp_freq = c["fs"]
        opts.frame_opts.dither = c.get("dither", 0.0)
        opts.frame_opts.window_type = c["window"]
        opts.frame_opts.frame_shift_ms = float(c["frame_shift"])
        opts.frame_opts.frame_length_ms = float(c["frame_length"])
        opts.mel_opts.num_bins = c["n_mels"]
        opts.energy_floor = 0
        opts.frame_opts.snip_edges = True
        opts.mel_opts.debug_mel = False

        fb = knf.OnlineFbank(opts)
        # Fed in pieces: the binding takes a Python list, ~32 bytes a sample
        step = _FEED_SAMPLES
        for i in range(0, len(samples), step):
            fb.accept_waveform(c["fs"], (samples[i:i + step] * (1 << 15)).tolist())
        n = fb.num_frames_ready
        mat = np.empty((n, c["n_mels"]), dtype=np.float32)
        for i in range(n):
            mat[i] = fb.get_frame(i)
        return mat

    def _n_lfr(self, fbank: np.ndarray) -> int:
        return int(np.ceil(fbank.shape[0] / self._frontend_conf["lfr_n"]))

    def _lfr_cmvn(self, fbank: np.ndarray, start: int, stop: int) -> np.ndarray:
        """LFR + CMVN rows [start, stop)."""
        c = self._frontend_conf
        if fbank.shape[0] == 0:
            return np.zeros((0, c["n_mels"] * c["lfr_m"]), dtype=np.float32)
        feats = apply_lfr(fbank, c["lfr_m"], c["lfr_n"], start, stop)
        return apply_cmvn(feats, self._cmvn)


# ── Frontend helpers (from funasr_onnx/utils/frontend.py) ─────────────────────

def apply_lfr(inputs: np.ndarray, lfr_m: int, lfr_n: int,
              start: int = 0, stop: "int | None" = None) -> np.ndarray:
    """Low frame rate: stack lfr_m frames every lfr_n, repeating edge frames.

    Only output rows [start, stop) are built (all by default)."""
    if lfr_m == 1 and lfr_n == 1:
        return inputs[start:stop].astype(np.float32)
    t = inputs.shape[0]
    stop = int(np.ceil(t / lfr_n)) if stop is None else stop
    left = (lfr_m - 1) // 2
    # Upstream pads with the first frame on the left and the last one on the right
    idx = np.arange(start, stop)[:, None] * lfr_n + np.arange(lfr_m) - left
    return inputs[np.clip(idx, 0, t - 1)].reshape(len(idx), -1).astype(np.float32)


def apply_cmvn(inputs: np.ndarray, cmvn: np.ndarray) -> np.ndarray:
    dim = inputs.shape[1]
    return ((inputs + cmvn[0:1, :dim]) * cmvn[1:2, :dim]).astype(np.float32, copy=False)


def load_cmvn(path: Path) -> np.ndarray:
    """am.mvn (Kaldi nnet text) → array (2, dim): [AddShift means, Rescale vars]."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    means: list = []
    variances: list = []
    for i, line in enumerate(lines):
        item = line.split()
        if not item or i + 1 >= len(lines):
            continue
        nxt = lines[i + 1].split()
        if item[0] == "<AddShift>" and nxt and nxt[0] == "<LearnRateCoef>":
            means = nxt[3:-1]
        elif item[0] == "<Rescale>" and nxt and nxt[0] == "<LearnRateCoef>":
            variances = nxt[3:-1]
    # float32: float64 here made every feature matrix float64 too (twice the memory)
    return np.array([np.array(means, dtype=np.float32), np.array(variances, dtype=np.float32)])
