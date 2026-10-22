"""Tests for ASR segment post-processing and asr.json serialisation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dubber.core.asr.base import ASRSegment
from dubber.core.asr.postprocess import (
    MAX_SEGMENT_S,
    MIN_SEGMENT_S,
    drop_empty,
    merge_short,
    postprocess,
    split_long,
    write_asr_json,
)


# ── drop_empty ────────────────────────────────────────────────────────────────

def test_drop_empty_removes_blank():
    segs = [
        ASRSegment(0, 2, "hello"),
        ASRSegment(2, 4, ""),
        ASRSegment(4, 6, "  "),
        ASRSegment(6, 8, "world"),
    ]
    result = drop_empty(segs)
    assert len(result) == 2
    assert result[0].text == "hello"
    assert result[1].text == "world"


def test_drop_empty_all_blank():
    segs = [ASRSegment(0, 1, ""), ASRSegment(1, 2, "  ")]
    assert drop_empty(segs) == []


def test_drop_empty_none_blank():
    segs = [ASRSegment(0, 1, "a"), ASRSegment(1, 2, "b")]
    assert drop_empty(segs) == segs


# ── merge_short ───────────────────────────────────────────────────────────────

def test_merge_short_merges_into_previous():
    segs = [
        ASRSegment(0.0, 5.0, "你好"),
        ASRSegment(5.0, 5.5, "吗"),   # 0.5s < 1.0s → merge
    ]
    result = merge_short(segs)
    assert len(result) == 1
    assert result[0].text == "你好吗"
    assert result[0].start == 0.0
    assert result[0].end == 5.5


def test_merge_short_keeps_leading_short():
    segs = [ASRSegment(0.0, 0.5, "短")]   # no previous — keep as-is
    result = merge_short(segs)
    assert len(result) == 1
    assert result[0].text == "短"


def test_merge_short_ignores_long_segment():
    segs = [
        ASRSegment(0.0, 3.0, "足够长"),
        ASRSegment(3.0, 6.0, "也够长"),
    ]
    result = merge_short(segs)
    assert len(result) == 2


def test_merge_short_custom_threshold():
    segs = [
        ASRSegment(0.0, 5.0, "A"),
        ASRSegment(5.0, 7.0, "B"),  # 2s — above default 1s but below custom 3s
    ]
    result = merge_short(segs, min_s=3.0)
    assert len(result) == 1
    assert result[0].text == "AB"


# ── split_long ────────────────────────────────────────────────────────────────

def test_split_long_no_action_below_max():
    seg = ASRSegment(0.0, 10.0, "大家好。")
    result = split_long([seg])
    assert len(result) == 1


def test_split_long_no_punct_keeps_segment():
    seg = ASRSegment(0.0, 20.0, "大家好欢迎来到今天的节目")
    result = split_long([seg])
    assert len(result) == 1  # no punctuation → can't split


def test_split_long_splits_at_punctuation():
    # Two equal sentences, 20s total → each ~10s (≥ max=8s will trigger split)
    text = "大家好。" + "欢迎来到。"
    seg = ASRSegment(0.0, 20.0, text)
    result = split_long([seg], max_s=8.0)
    assert len(result) == 2
    assert result[0].text == "大家好。"
    assert result[1].text == "欢迎来到。"
    assert result[0].end == pytest.approx(result[1].start, abs=0.01)


def test_split_long_distributes_time_proportionally():
    # 4 chars + punct, 6 chars + punct → ratio 5:7
    text = "一二三四。一二三四五六。"
    dur = 24.0
    seg = ASRSegment(0.0, dur, text)
    result = split_long([seg], max_s=10.0)
    assert len(result) == 2
    total_chars = len(text)
    expected_split = dur * 5 / total_chars
    assert result[0].end == pytest.approx(expected_split, abs=0.01)
    assert result[1].end == pytest.approx(dur, abs=0.01)


def test_split_long_accumulates_before_splitting():
    # Each sentence is ~4s (total 20s, 5 sentences).
    # With max_s=9, first split should happen after two sentences (~8s).
    text = "A。" * 5   # 5 identical sentences, total 20s
    seg = ASRSegment(0.0, 20.0, text)
    result = split_long([seg], max_s=9.0)
    # Should be 2 or 3 chunks, each ≤ max_s
    for chunk in result:
        assert (chunk.end - chunk.start) <= 9.0 + 1e-3


# ── postprocess (full pipeline) ───────────────────────────────────────────────

def test_postprocess_empty_input():
    assert postprocess([]) == []


def test_postprocess_drops_then_merges():
    segs = [
        ASRSegment(0.0, 5.0, "大家好"),
        ASRSegment(5.0, 5.3, ""),       # dropped
        ASRSegment(5.3, 5.7, "吗"),     # 0.4s → merge with prev
    ]
    result = postprocess(segs)
    assert len(result) == 1
    assert "大家好" in result[0].text
    assert "吗" in result[0].text


def test_postprocess_split_then_merge():
    # Long segment gets split, then each piece should be above min_s
    text = "一二三四五六七八九十。一二三四五六七八九十。"
    seg = ASRSegment(0.0, 26.0, text)
    result = postprocess([seg], max_s=12.0)
    assert len(result) >= 2
    for s in result:
        assert s.text.strip()


# ── write_asr_json ────────────────────────────────────────────────────────────

def test_write_asr_json_creates_file(tmp_path):
    segs = [ASRSegment(0.0, 3.5, "大家好"), ASRSegment(3.5, 8.0, "欢迎")]
    out = tmp_path / "asr.json"
    write_asr_json(segs, out)
    assert out.exists()


def test_write_asr_json_valid_schema(tmp_path):
    segs = [ASRSegment(1.23, 4.56, "测试文本")]
    out = tmp_path / "asr.json"
    write_asr_json(segs, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["start"] == pytest.approx(1.23)
    assert data[0]["end"] == pytest.approx(4.56)
    assert data[0]["text"] == "测试文本"


def test_write_asr_json_utf8(tmp_path):
    segs = [ASRSegment(0.0, 1.0, "中文繁體한국어日本語")]
    out = tmp_path / "asr.json"
    write_asr_json(segs, out)
    raw = out.read_bytes()
    text = raw.decode("utf-8")
    assert "中文繁體한국어日本語" in text


def test_write_asr_json_no_unicode_escape(tmp_path):
    segs = [ASRSegment(0.0, 1.0, "大家好")]
    out = tmp_path / "asr.json"
    write_asr_json(segs, out)
    raw = out.read_text(encoding="utf-8")
    assert "\\u" not in raw  # ensure_ascii=False


def test_write_asr_json_empty(tmp_path):
    out = tmp_path / "asr.json"
    write_asr_json([], out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data == []


def test_write_asr_json_fields_are_correct_types(tmp_path):
    segs = [ASRSegment(0.166, 5.21, "测试")]
    out = tmp_path / "asr.json"
    write_asr_json(segs, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert isinstance(data[0]["start"], float)
    assert isinstance(data[0]["end"], float)
    assert isinstance(data[0]["text"], str)


# ── timestamps mode ───────────────────────────────────────────────────────────

from dubber.core.asr.postprocess import (  # noqa: E402
    MAX_SENTENCE_S,
    SILENCE_GAP_S,
    split_by_timestamps,
)


def _tok_seg(text: str, t0: float = 0.0, step: float = 0.25, end: float | None = None,
             gaps: dict | None = None) -> ASRSegment:
    """One VAD segment whose characters are *step* apart; gaps={index: extra_s}."""
    toks, t = [], t0
    for i, ch in enumerate(text):
        t += (gaps or {}).get(i, 0.0)
        toks.append((ch, round(t, 3)))
        t += step
    return ASRSegment(start=t0, end=end if end is not None else round(t + 0.3, 3),
                      text=text, token_times=toks)


def test_ts_splits_at_sentence_end():
    seg = _tok_seg("你好吗。我很好！你呢？")
    out = split_by_timestamps([seg])
    assert [s.text for s in out] == ["你好吗。", "我很好！", "你呢？"]
    assert out[0].start == 0.0
    # sentence 2 starts just before its first character (index 4 → 1.0 s)
    assert out[1].start == pytest.approx(0.9)
    assert all(a.end <= b.start for a, b in zip(out, out[1:]))
    assert out[-1].end <= seg.end


def test_ts_no_hard_end_cuts_at_comma_before_limit():
    # 50 chars × 0.25 s = 12.5 s, commas after char 10, 25 and 40, no 。
    text = "一" * 10 + "，" + "二" * 14 + "，" + "三" * 14 + "，" + "四" * 9
    out = split_by_timestamps([_tok_seg(text)], max_s=MAX_SENTENCE_S)
    assert len(out) == 2
    assert out[0].text.endswith("，")
    assert out[0].text.count("，") == 2   # last comma before 8 s, not the first one
    assert all(s.end - s.start <= MAX_SENTENCE_S for s in out)


def test_ts_long_without_any_punct_stays_whole():
    out = split_by_timestamps([_tok_seg("字" * 40)])   # 10 s, nothing to cut at
    assert len(out) == 1


def test_ts_silence_gap_splits():
    seg = _tok_seg("前面一句后面一句", gaps={4: SILENCE_GAP_S + 0.2})
    out = split_by_timestamps([seg])
    assert [s.text for s in out] == ["前面一句", "后面一句"]


def test_ts_normal_char_spacing_does_not_split():
    seg = _tok_seg("开饭时间早上九点", step=0.6)   # slow speech, no real pause
    assert len(split_by_timestamps([seg])) == 1


def test_ts_keeps_space_tokens_and_decimal_point():
    toks = [("我", 0.0), ("有", 0.3), ("3", 0.6), (".", 0.8), ("5", 1.0), ("个", 1.2),
            (" apple", 1.5), (".", 2.0), (" OK", 2.6), (".", 3.0)]
    seg = ASRSegment(0.0, 3.4, "我有3.5个 apple. OK.", token_times=toks)
    out = split_by_timestamps([seg])
    assert [s.text for s in out] == ["我有3.5个 apple.", "OK."]


def test_ts_end_capped_by_vad_end():
    seg = _tok_seg("好。", end=0.3)
    out = split_by_timestamps([seg])
    assert out[0].end <= 0.3


def test_merge_short_strict_does_not_bridge_pause():
    segs = [ASRSegment(0.0, 3.0, "第一句。"), ASRSegment(3.5, 4.0, "嗯。")]
    out = merge_short(segs, max_gap_s=0.3, max_s=8.0)
    assert len(out) == 2


def test_merge_short_strict_respects_max_len():
    segs = [ASRSegment(0.0, 7.6, "很长的一句。"), ASRSegment(7.7, 8.3, "嗯。")]
    out = merge_short(segs, max_gap_s=0.3, max_s=8.0)
    assert len(out) == 2


def test_merge_short_strict_joins_next_when_previous_too_far():
    segs = [ASRSegment(0.0, 3.0, "第一句。"), ASRSegment(4.0, 4.5, "那个"),
            ASRSegment(4.6, 7.0, "我们走吧。")]
    out = merge_short(segs, max_gap_s=0.3, max_s=8.0)
    assert [s.text for s in out] == ["第一句。", "那个我们走吧。"]
    assert out[1].start == 4.0


def test_postprocess_timestamps_mode_uses_tokens():
    out = postprocess([_tok_seg("你好吗。我很好！", step=0.4)], mode="timestamps")
    assert [s.text for s in out] == ["你好吗。", "我很好！"]


def test_postprocess_falls_back_to_legacy_without_tokens():
    seg = ASRSegment(0.0, 4.0, "你好吗。我很好！")   # no token_times
    out = postprocess([seg], mode="timestamps")
    assert out == postprocess([seg], mode="legacy")
    assert len(out) == 1   # legacy leaves ≤ 6 s segments alone


def test_ts_late_final_punctuation_does_not_stretch_end():
    toks = [("我", 5.5), ("们", 5.8), ("。", 20.2)]
    seg = ASRSegment(5.0, 20.4, "我们。", token_times=toks)
    out = split_by_timestamps([seg])
    assert out[0].end <= 5.8 + 0.5


def test_ts_padded_line_stays_within_limit():
    # 32 chars × 0.25 s: last word at 7.75 s — under 8 s, but not with the padding
    text = "一" * 15 + "，" + "二" * 15 + "。"
    out = split_by_timestamps([_tok_seg(text)], max_s=8.0)
    assert len(out) == 2
    assert all(s.end - s.start <= 8.0 for s in out)
