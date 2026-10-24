"""TikTok post prompt: written next to the exported video, ready to paste into ChatGPT."""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

SCRIPT_PLACEHOLDER = "<<< DÁN LỜI THOẠI VÀO ĐÂY >>>"

TIKTOK_PROMPT = """\
Bạn là chuyên gia nội dung TikTok Việt Nam, chuyên kênh thuyết minh / lồng tiếng video
Trung Quốc. Dưới đây là lời thoại tiếng Việt (đánh số theo câu) của một video tôi đã lồng tiếng.

Đọc hết lời thoại, xác định: thể loại, nhân vật chính, tình huống mở đầu, cú twist hoặc
khoảnh khắc hấp dẫn nhất. Sau đó viết MỘT bản đăng TikTok tốt nhất (chỉ một phương án):

- Tiêu đề: dưới 80 ký tự. Móc câu gây tò mò từ tình huống hoặc bí ẩn trong video
  (câu hỏi, điều nghịch lý, "chuyện gì xảy ra khi…"), KHÔNG tiết lộ cái kết.
- Mô tả: 2–4 câu ngắn. Câu đầu thu hút; câu giữa gợi điểm hấp dẫn nhất mà không spoil;
  câu cuối là câu hỏi dễ trả lời để mọi người bình luận (đoán kết, chọn phe, "nếu là bạn…").
  Lồng tự nhiên 1–2 từ khóa người xem hay tìm (ví dụ: truyện kỳ bí, truyện ngắn,
  phim Trung Quốc…). Tối đa 3 emoji.
- Hashtag: ĐÚNG 5 hashtag, không hơn không kém: 1 hashtag rộng (#xuhuong hoặc #fyp),
  4 hashtag đúng thể loại và nội dung video. Viết liền, không dấu, không dấu cách.
- Chữ ảnh bìa: 3–6 chữ, gây tò mò, viết HOA được.
- Bình luận ghim: 1 câu để tôi tự bình luận và ghim, khơi thảo luận hoặc hỏi người xem
  có muốn xem phần tiếp theo không.

Quy tắc:
- Chỉ dựa trên lời thoại, không bịa chi tiết không có trong video, không giật tít sai sự thật.
- Văn phong tự nhiên, gần gũi, không sến.
- Tránh từ dễ bị TikTok hạn chế hiển thị (máu me, giết, tự tử, chết chóc, 18+…); nếu nội
  dung có những chi tiết đó, dùng cách nói nhẹ hơn.
- Trả lời DUY NHẤT một khối code dạng ```text, bên trong đúng 5 dòng theo mẫu dưới,
  không viết gì bên ngoài khối code (không chào, không giải thích, không gợi ý thêm):

Tiêu đề: ...
Mô tả: ...
Hashtag: #... #... #... #... #...
Chữ ảnh bìa: ...
Bình luận ghim: ...

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
