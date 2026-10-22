"""Manual translation: Chinese script export, Vietnamese file parsing, pipeline flow."""
from __future__ import annotations

from pathlib import Path

import pytest

from dubber.core.asr.base import ASRSegment
from dubber.core.translate.manual import parse_vietnamese, write_script_txt

SEGS = [ASRSegment(0.0, 1.5, "你好。"), ASRSegment(2.0, 3.25, "再见。"),
        ASRSegment(65.0, 66.5, "谢谢。")]


def test_script_txt_one_sentence_per_line(tmp_path):
    segs = SEGS + [ASRSegment(70.0, 71.0, "多余的\n换行")]
    p = write_script_txt(segs, tmp_path / "zh.txt")
    assert p.read_text(encoding="utf-8") == "你好。\n再见。\n谢谢。\n多余的 换行\n"
    assert parse_vietnamese(p, 4) == ["你好。", "再见。", "谢谢。", "多余的 换行"]   # round-trips


def test_parse_srt_by_number_any_order_multiline(tmp_path):
    f = tmp_path / "vi.srt"
    f.write_text("2\n00:00:02,000 --> 00:00:03,250\nTạm biệt.\n\n"
                 "1\n00:00:00,000 --> 00:00:01,500\nXin chào\nbạn.\n\n"
                 "3\n00:01:05,000 --> 00:01:06,500\nCảm ơn.\n", encoding="utf-8")
    assert parse_vietnamese(f, 3) == ["Xin chào bạn.", "Tạm biệt.", "Cảm ơn."]


def test_parse_srt_missing_and_extra(tmp_path):
    f = tmp_path / "vi.srt"
    f.write_text("1\n00:00:00,000 --> 00:00:01,000\nA\n\n4\n00:00:01,000 --> 00:00:02,000\nB\n")
    with pytest.raises(ValueError, match="thiếu câu số 2, 3.*thừa câu số 4"):
        parse_vietnamese(f, 3)


def test_parse_txt_lines(tmp_path):
    f = tmp_path / "vi.txt"
    f.write_text("﻿Xin chào.\r\n\r\nTạm biệt.\r\n3 người cảm ơn.\r\n", encoding="utf-8")
    assert parse_vietnamese(f, 3) == ["Xin chào.", "Tạm biệt.", "3 người cảm ơn."]
    with pytest.raises(ValueError, match="có 3 dòng, cần đúng 2"):
        parse_vietnamese(f, 2)


def test_parse_numbered_lines_and_utf16(tmp_path):
    f = tmp_path / "vi.txt"
    f.write_text("2) Tạm biệt.\n1. Xin chào.\n[3] Cảm ơn.\n", encoding="utf-16")
    assert parse_vietnamese(f, 3) == ["Xin chào.", "Tạm biệt.", "Cảm ơn."]


# ── pipeline ──────────────────────────────────────────────────────────────────

@pytest.fixture
def env(tmp_path):
    from tests.test_job_cache import _Env
    job = tmp_path / "job"
    job.mkdir()
    (job / "audio.wav").write_bytes(b"RIFF fake audio")
    return _Env(job, tmp_path / "models")


def _run_manual(env, cb):
    from unittest.mock import MagicMock, patch
    from dubber.core.pipeline import Pipeline
    pipe = Pipeline(ffmpeg=Path("ffmpeg"), ffprobe=Path("ffprobe"), job_dir=env.job_dir)
    pipe._extractor = MagicMock()
    pipe.manual_translate = cb
    engine = MagicMock()
    engine.transcribe.side_effect = env._transcribe
    engine.vad_used = env.vad_used = "silero"
    with patch("dubber.core.asr.model_manager.find_sherpa_model_dir",
               return_value=env.models / "sense-voice"), \
         patch("dubber.core.asr.model_manager.find_silero_vad", return_value=env.vad_model), \
         patch("dubber.core.asr.model_manager.find_fsmn_vad_dir", return_value=None), \
         patch("dubber.core.asr.sensevoice_sherpa.SherpaOnnxASR", return_value=engine):
        return pipe.run_m2(Path("in.mp4"), translator="manual", asr_on_vocals=False)


def test_pipeline_manual_flow_retry_then_reuse(env, tmp_path):
    import json
    bad = tmp_path / "bad.txt"; bad.write_text("chỉ một dòng\n", encoding="utf-8")
    good = tmp_path / "good.txt"; good.write_text("Xin chào.\nTạm biệt.\n", encoding="utf-8")
    calls = []

    def cb(script, count, can_reuse, error):
        calls.append((script.name, count, can_reuse, error))
        return bad if len(calls) == 1 else good
    _, tj = _run_manual(env, cb)
    assert calls[0] == ("script_zh.txt", 2, False, None)
    assert "cần đúng 2 dòng" in calls[1][3]                  # bad file → asked again
    data = json.loads(tj.read_text(encoding="utf-8"))
    assert [d["vi_text"] for d in data] == ["Xin chào.", "Tạm biệt."]
    assert [d["src_text"] for d in data] == ["你好。", "再见。"]

    seen = []
    _, tj = _run_manual(env, lambda *a: seen.append(a[2]) or "reuse")   # same video again
    assert seen == [True]                                     # offered, not silently reused
    assert [d["vi_text"] for d in json.loads(tj.read_text(encoding="utf-8"))] == ["Xin chào.", "Tạm biệt."]


def test_pipeline_manual_without_gui_fails_clearly(env):
    with pytest.raises(RuntimeError, match="giao diện"):
        _run_manual(env, None)
