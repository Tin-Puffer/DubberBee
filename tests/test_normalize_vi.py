"""Tests for Vietnamese clean-up after translation."""
from __future__ import annotations

import unicodedata

import pytest

from dubber.core.translate.normalize_vi import apply_names, normalize_cue, normalize_text


@pytest.mark.parametrize("vi, zh, expected", [
    ("trong những khu rừng ở Bomi,", "在西藏剥密的原始森林里，", "Trong những khu rừng ở Bomi"),
    ("là chìa khóa để tạo nên phô mai", "就是制作奶酪的关键。", "Là chìa khóa để tạo nên phô mai."),
    ("xin chào ,mọi người !  hẹn gặp lại", "", "Xin chào, mọi người! Hẹn gặp lại"),
    ("giá 1,5 triệu lúc 10:30.", "", "Giá 1,5 triệu lúc 10:30."),
    ("vâng. tôi hiểu rồi", "是的。我明白了。", "Vâng. Tôi hiểu rồi."),
    ("“chào”, anh nói", "", "“Chào”, anh nói"),
    ("", "你好。", ""),
])
def test_normalize_text(vi, zh, expected):
    assert normalize_text(vi, zh) == expected


def test_keeps_proper_names_capitalized():
    assert normalize_text("phô mai Bomi ở Tây Tạng") == "Phô mai Bomi ở Tây Tạng"


def test_composed_unicode():
    nfd = unicodedata.normalize("NFD", "tiếng việt")
    assert normalize_text(nfd) == unicodedata.normalize("NFC", "Tiếng việt")


@pytest.mark.parametrize("text", [
    "trong những khu rừng ở Bomi,", "xin chào ,mọi người !", "vâng. tôi hiểu rồi", "a; b:",
])
def test_idempotent(text):
    once = normalize_text(text, "。")
    assert normalize_text(once, "。") == once


def test_apply_names_case_insensitive_whole_words():
    names = {"bomi": "Bomi", "tây tạng": "Tây Tạng"}
    assert apply_names("phô mai bomi ở tây tạng, bomin", names) == "phô mai Bomi ở Tây Tạng, bomin"
    assert normalize_text("phô mai bomi", names=names) == "Phô mai Bomi"


def test_normalize_cue():
    assert normalize_cue("là chìa khóa để tạo nên,") == "Là chìa khóa để tạo nên"
    assert normalize_cue("tây Tạng, có rất nhiều cây.") == "Tây Tạng, có rất nhiều cây."
    assert normalize_cue("chờ đã…") == "Chờ đã…"
