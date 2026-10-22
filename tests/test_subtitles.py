"""Tests for subtitle line breaking, chunk merging and display timing."""
from __future__ import annotations

from dubber.core.video.subtitles import (
    GAP_BRIDGE, MAX_CHUNK_CHARS, MAX_HOLD, MAX_LINE_CHARS, MIN_DISPLAY, READ_CPS,
    _merge_short_chunks, _retime, _wrap_text,
)


# ── line breaking ─────────────────────────────────────────────────────────────

def test_short_text_stays_one_line():
    assert _wrap_text("Xin chào mọi người") == "Xin chào mọi người"


def test_two_lines_are_balanced_and_no_orphan():
    text = "Người dân lấy nguyên liệu tại chỗ gọt vỏ cây và rửa sạch nhựa cây đó"
    l1, l2 = _wrap_text(text).split(r"\N")
    assert abs(len(l1) - len(l2)) <= 8
    assert len(l2.split()) > 2 and len(l1.split()) > 2


def test_prefers_break_after_comma():
    text = "Sáng sớm khi ngư dân ra biển, họ phát hiện một chiếc đồng hồ quý"
    assert _wrap_text(text).split(r"\N")[0].endswith(",")


def test_never_drops_text():
    text = " ".join(["từ"] * 40)  # 119 chars — more than two lines
    wrapped = _wrap_text(text)
    assert "…" not in wrapped
    assert wrapped.replace(r"\N", " ").split() == text.split()
    assert all(len(l) <= MAX_LINE_CHARS for l in wrapped.split(r"\N"))


# ── merging ───────────────────────────────────────────────────────────────────

def test_merge_short_chunk_respects_length_cap():
    long_a = "a" * 40
    long_b = "b" * 40  # merged would be 81 > MAX_CHUNK_CHARS
    out = _merge_short_chunks([(0.0, 0.5, long_a), (0.5, 3.0, long_b)], 1.2)
    assert len(out) == 2
    assert all(len(t) <= MAX_CHUNK_CHARS for *_, t in out)


def test_merge_short_chunk_when_it_fits():
    out = _merge_short_chunks([(0.0, 0.5, "Vâng."), (0.5, 3.0, "Tôi hiểu rồi.")], 1.2)
    assert out == [(0.0, 3.0, "Vâng. Tôi hiểu rồi.")]


# ── display timing ────────────────────────────────────────────────────────────

def test_hold_into_silence_for_reading():
    text = "x" * 34  # needs 2.0 s at READ_CPS
    (s, e, _), = _retime([(10.0, 11.0, text)])
    assert e == 10.0 + len(text) / READ_CPS


def test_hold_is_capped():
    text = "x" * 200
    (s, e, _), = _retime([(0.0, 1.0, text)])
    assert e == 1.0 + MAX_HOLD


def test_min_display_for_tiny_text():
    (s, e, _), = _retime([(0.0, 0.3, "Ừ")])
    assert e - s >= MIN_DISPLAY


def test_never_overlaps_next_subtitle():
    out = _retime([(0.0, 1.0, "x" * 60), (1.5, 3.0, "tiếp")])
    assert out[0][1] == 1.5


def test_small_gap_is_bridged():
    gap = GAP_BRIDGE / 2
    out = _retime([(0.0, 2.0, "Câu một"), (2.0 + gap + 0.0, 4.0, "Câu hai")])
    assert out[0][1] == out[1][0]


def test_large_gap_kept():
    out = _retime([(0.0, 2.0, "Câu một"), (5.0, 7.0, "Câu hai")])
    assert out[0][1] < 5.0
