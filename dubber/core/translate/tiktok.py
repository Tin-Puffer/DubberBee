"""TikTok post prompt: written next to the exported video, ready to paste into ChatGPT."""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

SCRIPT_PLACEHOLDER = "<<< DÁN LỜI THOẠI VÀO ĐÂY >>>"

TIKTOK_PROMPT = """\
Bạn là chuyên gia nội dung TikTok Việt Nam, chuyên kênh thuyết minh video Trung Quốc.
Dưới đây là lời thoại tiếng Việt của một video tôi đã lồng tiếng.

Hãy viết cho tôi MỘT bản đăng TikTok tốt nhất (chỉ một phương án duy nhất, không đưa lựa chọn khác):
- Tiêu đề: dưới 80 ký tự, có móc câu gây tò mò, đúng sự thật trong video.
- Mô tả: 2–4 câu ngắn. Câu đầu thu hút, giữa nêu điểm hấp dẫn nhất, câu cuối là câu hỏi
  kêu gọi bình luận. Tối đa 3 emoji.
- Hashtag: ĐÚNG 5 hashtag, không hơn không kém: 1 hashtag rộng (như #xuhuong hoặc #fyp),
  4 hashtag đúng chủ đề và nội dung video. Viết liền, không dấu cách.

Quy tắc:
- Chỉ dựa trên lời thoại, không bịa chi tiết không có trong video.
- Văn phong tự nhiên, gần gũi, không sến, không giật tít sai sự thật.
- Trả lời DUY NHẤT một khối code dạng ```text, bên trong đúng 3 dòng theo mẫu dưới,
  không viết gì bên ngoài khối code (không chào, không giải thích, không gợi ý thêm):

Tiêu đề: ...
Mô tả: ...
Hashtag: #... #... #... #... #...

LỜI THOẠI:
<<< DÁN LỜI THOẠI VÀO ĐÂY >>>
"""


def script_lines(rows: List[dict]) -> List[str]:
    """"[n] câu" per translated sentence (n = sentence number; empty ones skipped)."""
    out = []
    for n, row in enumerate(rows, 1):
        vi = " ".join((row.get("vi_text") or "").split())
        if vi:
            out.append(f"[{n}] {vi}")
    return out


def tiktok_prompt(rows: List[dict], template: "str | None" = None) -> str:
    """*template* (default: the user's saved TikTok prompt) with the numbered script at the
    placeholder — or appended at the end when the user removed the placeholder."""
    if template is None:
        from dubber.core.translate.prompts import get_prompt
        template = get_prompt("tiktok")
    script = "\n".join(script_lines(rows))
    if SCRIPT_PLACEHOLDER in template:
        return template.replace(SCRIPT_PLACEHOLDER, script)
    return template.rstrip() + "\n\nLỜI THOẠI:\n" + script + "\n"


def write_tiktok_txt(translate_json: Path, dest: Path) -> Path:
    """Write the prompt with this video's Vietnamese script to *dest* (UTF-8)."""
    rows = json.loads(Path(translate_json).read_text(encoding="utf-8"))
    dest.write_text(tiktok_prompt(rows), encoding="utf-8")
    return dest
