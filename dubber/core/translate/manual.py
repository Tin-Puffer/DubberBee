"""Manual translation with a chatbot ("Thủ công — nhờ AI").

After ASR the user gets only the Chinese lines with their exact times, asks a chatbot
(ChatGPT…) for the Vietnamese narration, and pastes the answer back. The chatbot may
merge, split and rewrite lines and sets its own times / pauses, so the reply replaces
the ASR segmentation: TTS and subtitles follow the chatbot's lines and times.

Reply lines: "[00:07.48 → 00:11.20] Câu tiếng Việt" (other common spellings accepted).
"""
from __future__ import annotations

import re
from typing import List, Tuple

MANUAL_PROMPT = """\
Bạn là biên kịch lời thuyết minh phim Trung Quốc sang tiếng Việt, viết lời cho giọng đọc TTS và phụ đề video.

Bên dưới là lời thoại tiếng Trung do máy nghe chép lại. Mỗi dòng có thời gian xuất hiện chính xác trong video:
[số] bắt đầu → kết thúc | câu tiếng Trung
(thời gian dạng phút:giây.phần trăm giây, ví dụ 01:07.48)

NHIỆM VỤ: viết lời thuyết minh tiếng Việt cho cả video.
1. Đọc hết trước để hiểu câu chuyện, nhân vật và ai nói với ai. Chữ Trung có thể sai hoặc thiếu do máy nghe — suy luận theo ngữ cảnh.
2. Được phép GỘP nhiều dòng thành một câu, TÁCH một dòng dài thành nhiều câu, viết lại, rút gọn, lược chi tiết phụ — miễn câu tiếng Việt tự nhiên, trọn ý, như người Việt kể chuyện. Giữ đúng nội dung cốt lõi, không bịa tình tiết.
3. TỰ ĐẶT THỜI GIAN cho từng câu tiếng Việt:
   - Câu nói về đoạn nào thì đặt trong khoảng thời gian của đoạn tiếng Trung đó (gộp dòng: từ đầu dòng đầu đến cuối dòng cuối).
   - Thời lượng phải đủ để đọc, khoảng 3–4 từ mỗi giây. Câu dài hơn chỗ trống thì rút gọn.
   - Các câu không chồng giờ lên nhau, theo đúng thứ tự thời gian, không vượt quá độ dài video.
   - Khoảng nghỉ do bạn quyết định bằng khoảng trống giữa hai câu: nghỉ ngắn (0,1–0,3 giây) trong mạch kể, nghỉ dài hơn khi chuyển cảnh hoặc sau câu gây bất ngờ. Đoạn không cần lời thì để trống.
4. Giữ nhất quán tên riêng, thuật ngữ và xưng hô. Viết như lời đọc: không ký hiệu, không viết tắt.
5. Đọc lại toàn bộ như đang nghe TTS: tự nhiên, liền mạch, không lặp ý, không câu cụt.
6. Tránh từ dễ bị TikTok hạn chế: thay từ nhạy cảm bằng cách nói nhẹ hơn, từ đồng nghĩa hoặc tiếng lóng quen thuộc mà người Việt vẫn hiểu, ví dụ: xác / xác chết → thi thể, người đã khuất; chết → qua đời, ra đi, không qua khỏi; giết → ra tay, kết liễu, trừ khử; máu → vết đỏ; hiếp dâm / cưỡng hiếp → xâm hại; tự tử → làm chuyện dại dột; ma túy → hàng cấm; súng → vũ khí. Giữ đúng nghĩa câu chuyện. Không né bằng ký tự lạ, số hay viết tắt (như g!ết, ch3t) vì giọng đọc TTS không đọc được.

ĐỊNH DẠNG TRẢ LỜI — làm đúng tuyệt đối:
- Chỉ trả về MỘT khối code duy nhất, mở bằng ```text và đóng bằng ```.
- Mỗi dòng là một câu tiếng Việt theo đúng mẫu:
  [00:07.48 → 00:11.20] Câu tiếng Việt
- Thời gian viết dạng phút:giây.phần trăm giây như đầu vào. Không ghi số thứ tự, không chép tiếng Trung.
- Không viết gì bên ngoài khối code."""

_TIME = r"(?:\d+:)?\d{1,3}:\d{1,2}(?:[.,]\d+)?|\d+(?:[.,]\d+)?"
_LINE = re.compile(
    r"^\s*(?:[-*•]\s*)?(?:\[?\s*\d+\s*[\].)]\s+)?"            # optional bullet / [n] / n.
    rf"\[?\s*({_TIME})\s*(?:-->|→|->|=>|–|—|-|~|đến|to)\s*({_TIME})\s*\]?"
    r"\s*[|:\-–—]?\s*(.*?)\s*$")
MIN_LINE_S = 0.3


def clock(t: float) -> str:
    """Seconds → "mm:ss.cc" (h:mm:ss.cc past an hour), the format the chatbot sees."""
    t = max(0.0, float(t))
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}" if h >= 1 else f"{int(m):02d}:{s:05.2f}"


def parse_clock(text: str) -> float:
    parts = text.replace(",", ".").split(":")
    secs = 0.0
    for p in parts:
        secs = secs * 60 + float(p)
    return secs


def manual_script(rows: List[dict]) -> List[str]:
    """ "[n] 00:07.48 → 00:09.92 | 中文" per ASR line (n is 1-based)."""
    out = []
    for n, r in enumerate(rows, 1):
        zh = " ".join((r.get("src_text") or "").split())
        out.append(f"[{n}] {clock(r['start'])} → {clock(r['end'])} | {zh}")
    return out


def manual_text(rows: List[dict], duration: float, instructions: "str | None" = None) -> str:
    """Prompt + the video's length + every Chinese line with its times."""
    if instructions is None:
        from dubber.core.translate.prompts import get_prompt
        instructions = get_prompt("manual")
    head = instructions.strip()
    body = (f"Độ dài video: {clock(duration)}\n\nLỜI THOẠI TIẾNG TRUNG:\n"
            + "\n".join(manual_script(rows)) + "\n")
    return (head + "\n\n" if head else "") + body


def looks_timed(text: str) -> bool:
    """True if most content lines of *text* are "[start → end] câu" (a merge / split reply)
    rather than "[số] câu" (a text-only edit)."""
    lines = [l.strip() for l in (text or "").splitlines()
             if l.strip() and not l.strip().startswith("```")]
    timed = sum(1 for l in lines if _LINE.match(l.replace("**", "")))
    return timed > 0 and timed * 2 >= len(lines)


def parse_manual_reply(text: str, duration: float) -> Tuple[List[dict], List[str]]:
    """Chatbot reply → ([{start, end, vi_text}] in time order, warnings).

    Fixes what it safely can (times past the end, overlaps, a line ending before it
    starts) and says so in the warnings. Raises ValueError when no timed line is found.
    """
    segs, warns, skipped = [], [], 0
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip().replace("**", "").replace("__", "")
        if not line or line.startswith("```"):
            continue
        m = _LINE.match(line)
        if not m:
            skipped += 1
            continue
        start, end = parse_clock(m.group(1)), parse_clock(m.group(2))
        vi = m.group(3).strip().strip('"“”').strip()
        if not vi:
            continue
        segs.append({"start": start, "end": end, "vi_text": vi})
    if not segs:
        raise ValueError('Không thấy dòng nào dạng "[00:07.48 → 00:11.20] câu tiếng Việt"')
    if skipped:
        warns.append(f"Bỏ qua {skipped} dòng không có thời gian")

    segs.sort(key=lambda s: s["start"])
    fixed_order = fixed_overlap = fixed_end = 0
    for i, s in enumerate(segs):
        if duration and s["start"] >= duration:
            s["start"] = max(0.0, duration - MIN_LINE_S)
            fixed_end += 1
        if duration and s["end"] > duration:
            s["end"] = duration
            fixed_end += 1
        if s["end"] <= s["start"]:
            nxt = segs[i + 1]["start"] if i + 1 < len(segs) else (duration or s["start"] + 2)
            s["end"] = max(s["start"] + MIN_LINE_S, min(nxt, s["start"] + 3))
            fixed_order += 1
        if i and s["start"] < segs[i - 1]["end"]:
            segs[i - 1]["end"] = max(segs[i - 1]["start"] + MIN_LINE_S, s["start"])
            fixed_overlap += 1
    if fixed_overlap:
        warns.append(f"{fixed_overlap} câu chồng giờ lên câu sau — đã cắt cho vừa")
    if fixed_order:
        warns.append(f"{fixed_order} câu có giờ kết thúc trước giờ bắt đầu — đã sửa")
    if fixed_end:
        warns.append(f"{fixed_end} mốc giờ vượt quá độ dài video — đã kéo về")
    return segs, warns


def to_translate_rows(segs: List[dict], asr_rows: List[dict]) -> List[dict]:
    """translate.json rows for the chatbot's lines; src_text = the Chinese they cover
    (ASR lines whose middle falls inside), for review / listening to the original."""
    out = []
    for i, s in enumerate(segs):
        zh = "".join(r.get("src_text", "") for r in asr_rows
                     if s["start"] <= (r["start"] + r["end"]) / 2 <= s["end"])
        out.append({"id": i, "start": round(s["start"], 3), "end": round(s["end"], 3),
                    "src_text": zh, "vi_text": s["vi_text"]})
    return out
