"""Translation review: sentences worth a second look, chatbot copy / paste-back format.

Rows are translate.json entries: {id, start, end, src_text, vi_text}. Sentence numbers
shown to the user (and to a chatbot) are 1-based: number n is rows[n - 1].
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Tuple

# Comfortable Vietnamese speech; TimingFitter speeds a line up by at most 1.3×
SYLLABLES_PER_SEC: float = 5.0
MAX_SPEEDUP: float = 1.3

DEFAULT_INSTRUCTIONS = """\
Bạn là biên tập viên kiêm biên kịch lời thuyết minh phim Trung Quốc sang tiếng Việt, chuyên tối ưu lời thoại cho TTS và phụ đề video.

Yêu cầu:
1. Đọc toàn bộ nội dung trước khi sửa để hiểu câu chuyện, ngữ cảnh và mối liên hệ giữa các câu. Tiếng Trung có thể bị nhận dạng sai hoặc thiếu chữ, hãy suy luận theo ngữ cảnh.
2. Phóng tác tự nhiên, không dịch máy móc. Được phép viết lại cấu trúc câu, chọn từ, đảo ý, rút gọn hoặc lược bỏ chi tiết phụ để lời thoại hấp dẫn, dễ hiểu như người Việt kể chuyện. Giữ đúng nội dung cốt lõi, không tự bịa tình tiết.
3. Ưu tiên thời lượng TTS. Mỗi dòng có thời lượng riêng, mục tiêu khoảng 3–4 từ mỗi giây. Dòng có ghi chú ⚠ dài bắt buộc rút gọn. Chủ động chọn từ ngữ và lượng thông tin phù hợp, không cố dịch đủ từng chữ.
4. Xử lý câu bị ngắt giữa các dòng. Đọc các dòng liền kề như một đoạn văn hoàn chỉnh. Chủ động viết lại và phân bổ nội dung để tránh câu cụt, ngắt vô lý hoặc kết thúc bằng từ nối như “và”, “hoặc”, “bởi vì”. Ưu tiên câu hoàn chỉnh, dễ nghe; không cần giữ cấu trúc câu gốc.
5. Giữ nguyên số dòng và thứ tự. Không thêm, xóa, gộp hoặc tách dòng. Khi cần, được phép sửa nhiều dòng liên tiếp để lời thoại liền mạch. Giữ nhất quán tên riêng, thuật ngữ và cách xưng hô.
6. Kiểm tra lại toàn bộ lời thoại như đang nghe TTS để bảo đảm tự nhiên, đúng mạch truyện, không lặp ý và phù hợp thời lượng.
7. Tránh từ dễ bị TikTok hạn chế: thay từ nhạy cảm bằng cách nói nhẹ hơn, từ đồng nghĩa hoặc tiếng lóng quen thuộc mà người Việt vẫn hiểu, ví dụ: xác / xác chết → thi thể, người đã khuất; chết → qua đời, ra đi, không qua khỏi; giết → ra tay, kết liễu, trừ khử; máu → vết đỏ; hiếp dâm / cưỡng hiếp → xâm hại; tự tử → làm chuyện dại dột; ma túy → hàng cấm; súng → vũ khí. Giữ đúng nghĩa câu chuyện. Không né bằng ký tự lạ, số hay viết tắt (như g!ết, ch3t) vì giọng đọc TTS không đọc được.
Định dạng đầu ra:
- Chỉ trả về một khối code duy nhất, mở bằng ```text và đóng bằng ```.
- Mỗi dòng theo định dạng: [số] Câu tiếng Việt đã sửa
- Chỉ liệt kê những dòng thực sự cần sửa, giữ nguyên số thứ tự gốc.
- Không chép lại tiếng Trung, thời lượng, dấu mũi tên hoặc ghi chú.
- Không giải thích bên ngoài khối code.
- Nếu không có dòng nào cần sửa, trả về [0] KHÔNG SỬA."""

_HAN = re.compile(r"[㐀-鿿豈-﫿]")
_REPLY_LINE = re.compile(r"^\s*(?:[-*•]\s*)?\[?(\d+)\s*[\].):\-]\s*(.*)$")
_TIMING = re.compile(r"^\[\s*\d+(?:[.,]\d+)?\s*s\s*\]\s*")
_WARN_TAIL = re.compile(r"\s*⚠.*$")


def _sec(x: float) -> str:
    return f"{x:.1f}".replace(".", ",")


def row_flags(row: dict) -> List[str]:
    """Why this sentence needs a look (empty list = looks fine)."""
    vi = (row.get("vi_text") or "").strip()
    if not vi:
        return ["trống"]
    flags = []
    if _HAN.search(vi):
        flags.append("còn chữ Hán")
    slot = max(0.1, float(row["end"]) - float(row["start"]))
    need = len(vi.split()) / SYLLABLES_PER_SEC
    if need > slot * MAX_SPEEDUP and need - slot > 0.5:
        flags.append(f"dài ~{_sec(need)}s/{_sec(slot)}s")
    return flags


def flagged_numbers(rows: List[dict]) -> List[int]:
    """1-based numbers of the sentences that need a look."""
    return [n for n, row in enumerate(rows, 1) if row_flags(row)]


def chatbot_text(rows: List[dict], numbers: Optional[Iterable[int]] = None,
                 instructions: str = DEFAULT_INSTRUCTIONS) -> str:
    """Instructions + one line per sentence: "12. [1,5s] 中文 → tiếng Việt  ⚠ …"."""
    nums = list(numbers) if numbers is not None else range(1, len(rows) + 1)
    lines = []
    for n in nums:
        row = rows[n - 1]
        slot = max(0.0, float(row["end"]) - float(row["start"]))
        zh = " ".join((row.get("src_text") or "").split())
        vi = " ".join((row.get("vi_text") or "").split())
        line = f"{n}. [{_sec(slot)}s] {zh} → {vi}"
        flags = row_flags(row)
        if flags:
            line += "  ⚠ " + ", ".join(flags)
        lines.append(line)
    head = instructions.strip()
    return (head + "\n\n" if head else "") + "\n".join(lines) + "\n"


def parse_chatbot_reply(text: str, count: int) -> Tuple[Dict[int, str], List[int]]:
    """Numbered lines of a chatbot reply → ({number: new Vietnamese}, out-of-range numbers).

    Lines without a number (greetings, code fences, explanations) are skipped. A line
    that echoes the whole input ("12. [1,5s] 中文 → tiếng Việt  ⚠ …") keeps only the
    Vietnamese part. Raises ValueError when no numbered line is found.
    """
    changes: Dict[int, str] = {}
    bad: List[int] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip().replace("**", "").replace("__", "")
        m = _REPLY_LINE.match(line)
        if not m:
            continue
        n, body = int(m.group(1)), m.group(2).strip()
        body = _TIMING.sub("", body)
        if "→" in body:
            body = body.rsplit("→", 1)[1]
        body = _WARN_TAIL.sub("", body).strip().strip('"“”').strip()
        if not body:
            continue
        if 1 <= n <= count:
            changes[n] = body
        elif n not in bad:
            bad.append(n)
    if not changes and not bad:
        raise ValueError('Không thấy dòng nào dạng "số. câu tiếng Việt"')
    return changes, bad
