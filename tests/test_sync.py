"""Sync: split long ASR segments at real token times; subtitles follow the dubbed voice."""
from __future__ import annotations

import json
from pathlib import Path

from dubber.core.asr.base import ASRSegment
from dubber.core.asr.postprocess import _split_one, merge_short, _LEAD_S, _TAIL_S


def _seg_with_tokens():
    # 10 s segment: "一二三四，" spoken 0.0–1.0 then a long gap, "五六七八。" at 7.0–8.0
    toks = [("一", 0.0), ("二", 0.25), ("三", 0.5), ("四", 0.75), ("，", 1.0),
            ("五", 7.0), ("六", 7.25), ("七", 7.5), ("八", 7.75), ("。", 8.0)]
    return ASRSegment(start=0.0, end=10.0, text="一二三四，五六七八。", token_times=toks)


def test_split_uses_real_punctuation_time_not_char_ratio():
    a, b = _split_one(_seg_with_tokens(), max_s=6.0)
    assert a.text == "一二三四，" and b.text == "五六七八。"
    assert a.end == 1.0 + _TAIL_S          # proportional split would say 5.0 s
    assert b.start == 7.0 - _LEAD_S


def test_split_without_token_times_keeps_old_behaviour():
    seg = _seg_with_tokens()
    seg.token_times = None
    a, b = _split_one(seg, max_s=6.0)
    assert a.end == 5.0                      # char-proportional fallback


def test_short_segment_after_long_pause_is_not_merged():
    segs = [ASRSegment(0.0, 3.0, "前面"), ASRSegment(5.0, 5.6, "好")]
    assert len(merge_short(segs, 1.0)) == 2


def test_short_segment_right_after_is_merged():
    segs = [ASRSegment(0.0, 3.0, "前面"), ASRSegment(3.2, 3.8, "好")]
    (m,) = merge_short(segs, 1.0)
    assert (m.start, m.end, m.text) == (0.0, 3.8, "前面好")


def test_subtitles_follow_fit_json_not_asr_timing(tmp_path):
    from dubber.core.audio.fit import FittingResult
    from dubber.core.pipeline import _load_fit_json, _write_fit_json
    translate = [{"id": 0, "start": 1.0, "end": 3.0, "vi_text": "Xin chào"},
                 {"id": 1, "start": 3.0, "end": 5.0, "vi_text": "Tạm biệt"}]
    fitted = [FittingResult(0, "Xin chào", Path("."), 2.4, 1.0, 3.4, 1.0, "late"),
              FittingResult(1, "Tạm biệt", Path("."), 1.5, 3.5, 5.0, 1.0, "fits")]
    _write_fit_json(fitted, tmp_path / "fit.json")
    got = _load_fit_json(tmp_path / "fit.json", translate)
    assert [(f.final_start, f.final_end) for f in got] == [(1.0, 3.4), (3.5, 5.0)]


def test_stale_fit_json_is_ignored(tmp_path):
    from dubber.core.pipeline import _load_fit_json
    (tmp_path / "fit.json").write_text(json.dumps(
        [{"id": 0, "vi_text": "cũ", "final_start": 0, "final_end": 1, "speed": 1, "status": "fits"}]))
    assert _load_fit_json(tmp_path / "fit.json", [{"id": 0, "vi_text": "mới"}]) is None


def test_shortened_lines_are_saved_to_translate_json_and_stored_copy(tmp_path):
    from dubber.core.audio.fit import FittingResult
    from dubber.core.pipeline import _save_shortened
    from dubber.core.utils.job_cache import sig_matches, write_sig
    rows = [{"id": 0, "start": 0.0, "end": 1.0, "src_text": "长", "vi_text": "Một câu rất dài."},
            {"id": 1, "start": 1.0, "end": 2.0, "src_text": "短", "vi_text": "Ngắn."}]
    tj = tmp_path / "translate.json"
    tj.write_text(json.dumps(rows), encoding="utf-8")
    sig = {"asr": "x", "translator": "auto"}
    write_sig(tj, sig)
    (tmp_path / "translations").mkdir()
    stored = tmp_path / "translations" / "auto.json"
    stored.write_text(json.dumps(rows), encoding="utf-8")
    write_sig(stored, sig)
    fitted = [FittingResult(0, "Câu ngắn.", Path("."), 1, 0, 1, 1, "shortened"),
              FittingResult(1, "Ngắn.", Path("."), 1, 1, 2, 1, "fits")]
    _save_shortened(fitted, rows, tj, "auto")
    for p in (tj, stored):
        assert [r["vi_text"] for r in json.loads(p.read_text(encoding="utf-8"))] == \
            ["Câu ngắn.", "Ngắn."]
        assert sig_matches(p, sig)       # a rerun still reuses the (shortened) translation
