"""TikTok prompt written next to the exported video."""
from __future__ import annotations

import json

from dubber.core.translate.tiktok import SCRIPT_PLACEHOLDER, tiktok_prompt, write_tiktok_txt

ROWS = [
    {"id": 0, "start": 0.0, "end": 2.0, "src_text": "这是美食，", "vi_text": "Đây là món ngon,"},
    {"id": 1, "start": 2.0, "end": 3.0, "src_text": "嗯", "vi_text": ""},
    {"id": 2, "start": 3.0, "end": 5.0, "src_text": "是奶酪。", "vi_text": "là  phô mai.\n"},
]


def test_prompt_holds_the_numbered_script():
    text = tiktok_prompt(ROWS)
    assert SCRIPT_PLACEHOLDER not in text
    assert text.endswith("LỜI THOẠI:\n[1] Đây là món ngon,\n[3] là phô mai.\n")
    assert "ĐÚNG 5 hashtag" in text


def test_write_txt_utf8(tmp_path):
    tj = tmp_path / "translate.json"
    tj.write_text(json.dumps(ROWS, ensure_ascii=False), encoding="utf-8")
    out = write_tiktok_txt(tj, tmp_path / "clip_vi.txt")
    assert out.read_text(encoding="utf-8") == tiktok_prompt(ROWS)
