"""FSMN-VAD port: frontend shapes, post-processing, span helpers, real model (if present)."""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from dubber.core.asr.fsmn_vad import apply_cmvn, apply_lfr, load_cmvn
from dubber.core.asr.sensevoice_sherpa import merge_close_spans, split_long_span

_VAD_CONF = {   # model_conf of iic/speech_fsmn_vad_zh-cn-16k-common-onnx
    "sample_rate": 16000, "detect_mode": 1, "snr_mode": 0, "max_end_silence_time": 800,
    "max_start_silence_time": 3000, "do_start_point_detection": True,
    "do_end_point_detection": True, "window_size_ms": 200, "sil_to_speech_time_thres": 150,
    "speech_to_sil_time_thres": 150, "speech_2_noise_ratio": 1.0, "do_extend": 1,
    "lookback_time_start_point": 200, "lookahead_time_end_point": 100,
    "max_single_segment_time": 20000, "snr_thres": -100.0, "noise_frame_num_used_for_snr": 100,
    "decibel_thres": -100.0, "speech_noise_thres": 0.6, "fe_prior_thres": 0.0001,
    "silence_pdf_num": 1, "sil_pdf_ids": [0], "speech_noise_thresh_low": -0.1,
    "speech_noise_thresh_high": 0.3, "output_frame_probs": False, "frame_in_ms": 10,
    "frame_length_ms": 25,
}


# ── frontend ──────────────────────────────────────────────────────────────────

def test_lfr_stacks_frames():
    feats = np.arange(10 * 80, dtype=np.float32).reshape(10, 80)
    out = apply_lfr(feats, lfr_m=5, lfr_n=1)
    assert out.shape == (10, 400)
    # frame 0 = [f0, f0, f0, f1, f2] (2 frames of left padding)
    assert np.array_equal(out[0, :80], feats[0]) and np.array_equal(out[0, 320:], feats[2])
    # last frame is padded with the final input frame
    assert np.array_equal(out[-1, 320:], feats[-1])



def test_lfr_row_range_matches_whole():
    feats = np.random.default_rng(0).random((50, 80), dtype=np.float32)
    whole = apply_lfr(feats, lfr_m=5, lfr_n=1)
    parts = np.vstack([apply_lfr(feats, 5, 1, a, min(a + 7, 50)) for a in range(0, 50, 7)])
    assert np.array_equal(whole, parts)
    # lfr_n > 1: one row every lfr_n frames, the tail padded with the last frame
    out = apply_lfr(feats, lfr_m=7, lfr_n=6)
    assert out.shape == (9, 560)
    assert np.array_equal(out[-1, -80:], feats[-1])

def test_cmvn_shift_and_scale(tmp_path):
    mvn = tmp_path / "am.mvn"
    mvn.write_text(
        "<Nnet>\n<AddShift> 2 2\n<LearnRateCoef> 0 [ -1 -2 ]\n"
        "<Rescale> 2 2\n<LearnRateCoef> 0 [ 2 0.5 ]\n</Nnet>\n", encoding="utf-8")
    cmvn = load_cmvn(mvn)
    assert cmvn.shape == (2, 2)
    out = apply_cmvn(np.array([[3.0, 4.0]], dtype=np.float32), cmvn)
    assert np.allclose(out, [[4.0, 1.0]])


# ── post-processing state machine ─────────────────────────────────────────────

def test_state_machine_turns_scores_into_segments():
    from dubber.core.asr._fsmn_e2e_vad import E2EVadModel
    T = 600   # 6 s of 10 ms frames; speech at 1.0–3.0 s and 4.0–4.5 s
    sil = np.full(T, 0.95, np.float32)
    sil[100:300] = 0.05
    sil[400:450] = 0.05
    scores = np.zeros((1, T, 248), np.float32)
    scores[0, :, 0] = sil
    scores[0, :, 1] = 1 - sil
    wave_ = (np.random.default_rng(0).standard_normal(T * 160 + 240) * 0.1).astype(np.float32)
    segs = E2EVadModel(_VAD_CONF)(scores, wave_[None, :], is_final=True, max_end_sil=800)[0]
    assert len(segs) == 2
    (s1, e1), (s2, e2) = segs
    assert s1 <= 1000 <= 3000 <= e1 < s2 <= 4000 and e2 >= 4500


# ── span helpers ──────────────────────────────────────────────────────────────

def test_merge_close_spans():
    spans = [(0, 100), (120, 200), (500, 600)]
    assert merge_close_spans(spans, max_gap=30, max_len=1000) == [(0, 200), (500, 600)]
    assert merge_close_spans(spans, max_gap=30, max_len=150) == spans   # 0–200 too long


def test_split_long_span_cuts_at_quiet_point():
    sr = 1000
    x = np.ones(30 * sr, dtype=np.float32)
    x[12_000:12_300] = 0.0   # a pause at 12 s
    pieces = split_long_span(x, 0, len(x), max_len=20 * sr, sr=sr)
    assert len(pieces) == 2
    assert 12_000 <= pieces[0][1] <= 12_300
    assert pieces[-1][1] == len(x)
    assert all(b - a <= 20 * sr for a, b in pieces)


# ── real model (skipped when not downloaded) ──────────────────────────────────

def _real_model():
    from dubber.core.asr.model_manager import find_fsmn_vad_dir
    d = find_fsmn_vad_dir()
    if d is None:
        pytest.skip("FSMN-VAD model not downloaded")
    return d


def test_real_model_on_sample_wav():
    from dubber.core.asr.fsmn_vad import FsmnVAD
    from dubber.core.asr.model_manager import find_sherpa_model_dir
    d = _real_model()
    try:
        wav_path = find_sherpa_model_dir() / "test_wavs" / "zh.wav"
    except FileNotFoundError:
        pytest.skip("SenseVoice test wav not available")
    with wave.open(str(wav_path), "rb") as wf:
        x = np.frombuffer(wf.readframes(wf.getnframes()), np.int16).astype(np.float32) / 32768
    dur = len(x) / 16000
    vad = FsmnVAD(d)
    assert vad.features(x).shape[1] == 400
    segs = vad.segments(x)
    assert len(segs) >= 1
    assert all(0 <= s < e <= dur + 0.05 for s, e in segs)
    # silence in → nothing out
    assert vad.segments(np.zeros(16000 * 3, dtype=np.float32)) == []
