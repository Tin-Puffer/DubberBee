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

        feats = self.features(samples)
        n = feats.shape[0]
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
                                                    [feats[None, off:end, :], *caches])))
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
        fb.accept_waveform(c["fs"], (samples * (1 << 15)).tolist())
        n = fb.num_frames_ready
        mat = np.empty((n, c["n_mels"]), dtype=np.float32)
        for i in range(n):
            mat[i] = fb.get_frame(i)
        if n == 0:
            return np.zeros((0, c["n_mels"] * c["lfr_m"]), dtype=np.float32)
        feats = apply_lfr(mat, c["lfr_m"], c["lfr_n"])
        return apply_cmvn(feats, self._cmvn)


# ── Frontend helpers (from funasr_onnx/utils/frontend.py) ─────────────────────

def apply_lfr(inputs: np.ndarray, lfr_m: int, lfr_n: int) -> np.ndarray:
    """Low frame rate: stack lfr_m frames every lfr_n, repeating edge frames."""
    if lfr_m == 1 and lfr_n == 1:
        return inputs.astype(np.float32)
    t = inputs.shape[0]
    t_lfr = int(np.ceil(t / lfr_n))
    left = (lfr_m - 1) // 2
    padded = np.vstack([np.tile(inputs[0], (left, 1)), inputs])
    total = t + left
    rows = []
    for i in range(t_lfr):
        if lfr_m <= total - i * lfr_n:
            rows.append(padded[i * lfr_n: i * lfr_n + lfr_m].reshape(-1))
        else:   # last frames: pad with the final frame
            frame = padded[i * lfr_n:].reshape(-1)
            pad = lfr_m - (total - i * lfr_n)
            rows.append(np.hstack([frame] + [padded[-1]] * pad))
    return np.vstack(rows).astype(np.float32)


def apply_cmvn(inputs: np.ndarray, cmvn: np.ndarray) -> np.ndarray:
    dim = inputs.shape[1]
    return ((inputs + cmvn[0:1, :dim]) * cmvn[1:2, :dim]).astype(np.float32)


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
    return np.array([np.array(means, dtype=np.float64), np.array(variances, dtype=np.float64)])
