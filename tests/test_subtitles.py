"""Tests for subtitle cue cutting (one line), chunk merging, style and display timing."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from dubber.core.video.subtitles import (
    GAP_BRIDGE, MAX_CUE_CHARS, MAX_HOLD, MIN_DISPLAY, READ_CPS, SubtitleStyle,
    _merge_short_chunks, _retime, _split_to_chunks, build_ass, cue_size_px, hex_to_ass,
    max_line_px,
)


def _fits(text: str) -> bool:
    return len(text) <= MAX_CUE_CHARS


def _cues(path) -> list[str]:
    return [l.split(",", 9)[9].split("}")[-1]
            for l in path.read_text(encoding="utf-8-sig").splitlines()
            if l.startswith("Dialogue: 1,")]


# ── cutting into one-line cues ────────────────────────────────────────────────

@pytest.mark.parametrize("text, expected", [
    # the review cases: cut before "chính là", never after "chính"
    ("Điều đặc biệt nhất của món ăn này chính là cách chế biến độc đáo có một không hai.",
     ["Điều đặc biệt nhất của món ăn này",
      "chính là cách chế biến độc đáo có một không hai."]),
    ("Những cành cây trông bình thường ấy chính là chìa khóa để tạo nên phô mai.",
     ["Những cành cây trông bình thường ấy", "chính là chìa khóa để tạo nên phô mai."]),
    # a comma is the best place to cut
    ("Trong những khu rừng nguyên sinh rậm rạp ở Bomi, Tây Tạng, có rất nhiều cây vân sam cao lớn.",
     ["Trong những khu rừng nguyên sinh rậm rạp ở Bomi,",
      "Tây Tạng, có rất nhiều cây vân sam cao lớn."]),
])
def test_cuts_at_natural_breaks(text, expected):
    assert _split_to_chunks(text, _fits) == expected


def test_never_cuts_inside_a_word():
    out = _split_to_chunks("Phô mai giúp bổ sung năng lượng cho một ngày lao động.", lambda t: len(t) <= 35)
    assert "năng" not in [c.split()[-1] for c in out]
    assert out == ["Phô mai giúp bổ sung năng lượng", "cho một ngày lao động."]


def test_no_cue_ends_with_a_weak_word():
    text = ("Mỗi sáng sớm, Đức Cát Trác Nga đều đến chuồng bò để lấy sữa bò yak tươi, "
            "sau đó đổ sữa vào thùng chứa đầy cành vân sam.")
    for cue in _split_to_chunks(text, _fits):
        assert cue.split()[-1].strip(",.") not in {"chính", "là", "của", "và", "đều", "vừa", "để"}


def test_cutting_keeps_every_word_once():
    text = " ".join(["từ"] * 60) + "."
    out = _split_to_chunks(text, _fits)
    assert " ".join(out).split() == text.split()
    assert all(_fits(c) for c in out)


def test_sentences_become_separate_cues():
    assert _split_to_chunks("Vâng. Tôi hiểu rồi!", _fits) == ["Vâng.", "Tôi hiểu rồi!"]


# ── build_ass: one line, normalized cues, style ───────────────────────────────

def _seg(text, start=0.0, end=6.0):
    return SimpleNamespace(vi_text=text, final_start=start, final_end=end)


def test_ass_is_single_line_and_normalized(tmp_path):
    out = build_ass([_seg("Trong những khu rừng nguyên sinh rậm rạp ở Bomi, Tây Tạng, "
                          "có rất nhiều cây vân sam cao lớn.")], 1920, 1080, tmp_path / "s.ass")
    text = out.read_text(encoding="utf-8-sig")
    assert "WrapStyle: 2" in text and r"\N" not in text
    assert _cues(out) == ["Trong những khu rừng nguyên sinh rậm rạp ở Bomi",
                          "Tây Tạng, có rất nhiều cây vân sam cao lớn."]


def test_ass_cues_fit_the_frame(tmp_path):
    style = SubtitleStyle(font_name="Roboto", font_size=110)   # big font, narrow video
    out = build_ass([_seg("Đây là một câu khá dài để kiểm tra việc cắt theo độ rộng khung hình", 0, 9)],
                    854, 480, tmp_path / "s.ass", style=style)
    cues = _cues(out)
    assert len(cues) > 1
    assert all(cue_size_px(c, style, 480)[0] <= max_line_px(style, 854) for c in cues)


def test_style_outline_colors_and_box(tmp_path):
    style = SubtitleStyle(primary_color=hex_to_ass("#FFFF00"), outline_color=hex_to_ass("#102030"),
                          outline=3.0)
    text = build_ass([_seg("Xin chào")], 1280, 720, tmp_path / "s.ass", style=style).read_text(
        encoding="utf-8-sig")
    default = next(l for l in text.splitlines() if l.startswith("Style: Default"))
    f = default.split(",")
    assert f[3] == "&H0000FFFF" and f[5] == "&H00302010"
    assert f[15] == "1" and f[16] == "2.0"          # BorderStyle 1 + outline scaled 3 × 720/1080
    assert "Style: Box" not in text and "Dialogue: 0," not in text

    boxed = build_ass([_seg("Xin chào")], 1280, 720, tmp_path / "b.ass",
                      style=SubtitleStyle(box=True)).read_text(encoding="utf-8-sig")
    assert "Style: Box" in boxed
    assert ",Box,," in boxed and ",Default,," in boxed   # box layer under the text layer


def test_hex_to_ass():
    assert hex_to_ass("#FFFFFF") == "&H00FFFFFF"
    assert hex_to_ass("#112233") == "&H00332211"
    assert hex_to_ass("#112233", alpha=0x80) == "&H80332211"
    with pytest.raises(ValueError):
        hex_to_ass("red")


# ── merging ───────────────────────────────────────────────────────────────────

def test_merge_short_chunk_only_when_it_still_fits():
    long_a, long_b = "a" * 30, "b" * 30
    out = _merge_short_chunks([(0.0, 0.5, long_a), (0.5, 3.0, long_b)], 1.2, _fits)
    assert len(out) == 2


def test_merge_short_chunk_when_it_fits():
    out = _merge_short_chunks([(0.0, 0.5, "Vâng."), (0.5, 3.0, "Tôi hiểu rồi.")], 1.2, _fits)
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
