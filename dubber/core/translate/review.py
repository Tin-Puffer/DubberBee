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
Bạn là biên tập viên lồng tiếng phim Trung → Việt. Bên dưới là bản dịch máy.
Mỗi dòng có dạng:  số. [thời lượng] câu tiếng Trung → bản dịch tiếng Việt
(một số dòng có thêm "⚠ ..." là ghi chú cảnh báo, không phải nội dung).

NHIỆM VỤ — sửa bản dịch tiếng Việt cho:
1. Đúng nghĩa câu tiếng Trung, tự nhiên, đúng văn nói của phim.
2. Xưng hô nhất quán giữa các nhân vật suốt đoạn (anh/em, ta/ngươi, sư phụ/con…).
3. Đủ ngắn để đọc kịp trong thời lượng ghi trong [ ], khoảng 4–5 chữ mỗi giây.
   Câu có "⚠ dài" BẮT BUỘC phải rút gọn.
4. Tên riêng gọi giống nhau từ đầu đến cuối.
5. Không thêm, không bỏ, không gộp, không tách câu. Mỗi số giữ đúng một câu.

ĐỊNH DẠNG TRẢ LỜI — làm đúng tuyệt đối:
- Chỉ trả về MỘT khối code duy nhất, mở bằng ```text và đóng bằng ```.
- Trong khối code, mỗi dòng là:  [số] câu tiếng Việt đã sửa
  Ví dụ:
  [3] Im hết cho ta!
  [57] Ngươi dám động vào nàng thử xem.
- Số đặt trong ngoặc vuông, đúng số của câu gốc. KHÔNG dùng dạng "3." và không tạo danh sách đánh số.
- Chỉ liệt kê những câu bạn thực sự sửa. Câu đã ổn thì bỏ qua.
- Không chép lại tiếng Trung, thời lượng, dấu → hay ghi chú ⚠.
- Không viết gì bên ngoài khối code: không chào, không giải thích, không tóm tắt.
- Nếu không có câu nào cần sửa, trả về khối code chỉ có một dòng: [0] KHÔNG SỬA"""

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
