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
Bạn viết lời kể chuyện tiếng Việt cho video TikTok tóm tắt phim Trung Quốc. Giọng đọc TTS sẽ đọc lời này trên đúng video gốc và hiện thành phụ đề.

Đầu vào là lời thoại tiếng Trung máy nghe chép lại (có thể sai chữ — suy theo ngữ cảnh), mỗi dòng: [số] bắt đầu → kết thúc | câu tiếng Trung.

VIẾT LẠI thành một mạch kể liền, không dịch từng câu:
<noi_dung>
- Chỉ cần giữ ý chính và tình tiết, đúng thứ tự diễn biến (không kể trước sự việc chưa xảy ra). Tiểu tiết được thay đổi, phóng tác tự do cho kịch tính và hợp với người xem Việt: lời thoại, cách diễn đạt, chi tiết nhỏ, cảm xúc, không khí — thêm, bớt hay đổi đều được.
- Giữ ngôi kể gốc (truyện xưng "tôi" thì giữ "tôi"). Tên riêng, xưng hô cố định cả bài; tên có thể Việt hóa cho dễ nghe.
</noi_dung>
<van_phong>
- Kể như người Việt kể chuyện cuốn hút: câu trôi, nối ý bằng dấu phẩy và từ nối ("rồi", "thế là", "vậy mà", "đúng lúc đó", "hóa ra"), nhấn vào chỗ bất ngờ.
- Mỗi dòng là một câu trọn vẹn, kết thúc bằng dấu chấm, chấm hỏi hoặc chấm than — KHÔNG BAO GIỜ cắt một câu ra hai dòng hay kết thúc dòng bằng dấu phẩy: mỗi dòng được đọc riêng, câu bị cắt sẽ nghe đứt đoạn. Ví dụ sai: "…tôi lại gần thì thấy đất tơi xốp," / "dưới lớp đất còn có gì đó ngọ nguậy…" → đúng: một dòng "Tôi lại gần thì thấy đất tơi xốp đến lạ, dưới lớp đất còn có gì đó ngọ nguậy."
- Độ dài dòng theo ý của câu, không theo dòng tiếng Trung: thường 5–12 giây, gộp nhiều dòng tiếng Trung. Tránh chuỗi câu cụt và lặp chủ ngữ đầu câu. Ví dụ: "Tôi chạy đi lấy ngay. Ông Bảy ấn đầu gà lên quan tài." → "Tôi vội chạy đi lấy, ông Bảy liền túm con gà ấn chặt đầu nó lên nắp quan tài."
- Viết như lời đọc: không ký hiệu, không viết tắt.
</van_phong>
<dat_thoi_gian>
- NEO GIỜ THEO SỰ VIỆC: câu kể sự việc nào thì bắt đầu lệch tối đa 1 giây so với dòng tiếng Trung nhắc tới sự việc đó (không cần khớp ranh giới từng dòng tiếng Trung). Lời kể KHÔNG ĐƯỢC chậm dần so với hình: chi tiết phóng tác làm sự việc sau bị đẩy muộn thì cắt bớt. Khớp hình quan trọng hơn kín lời. Câu đầu bắt đầu từ đầu video, câu cuối kết thúc gần cuối video.
- Trong giới hạn neo giờ, lời phủ kín thời lượng: câu sau bắt đầu 0,1–0,2 giây sau khi câu trước kết thúc. Đoạn không có thoại vẫn kể tiếp không khí, cảm xúc của cùng cảnh. Chỉ để trống trên 1 giây ở vài khoảnh khắc gây sốc.
- Giọng đọc khoảng 3,8 tiếng (âm tiết) mỗi giây: số tiếng mỗi câu ≈ thời lượng × 3,8 (5 giây ≈ 19 tiếng, 10 giây ≈ 38 tiếng). Giữ trong khoảng 3,4–4 tiếng/giây: thiếu thì có khoảng lặng, thừa thì máy phải đọc nhanh.
- Không chồng giờ, đúng thứ tự, không vượt quá độ dài video.
</dat_thoi_gian>

Trước khi trả lời, tự soát: giờ bắt đầu từng câu so với dòng tiếng Trung của sự việc đó (lệch ≤ 1 giây, nhất là cuối video), số tiếng từng câu so với thời lượng, khoảng trống giữa các câu, và đọc lại cả bài cho liền mạch.

Chỉ trả về MỘT khối ```text, mỗi dòng một câu đúng mẫu (thời gian phút:giây.phần trăm giây, không số thứ tự, không tiếng Trung):
[00:07.48 → 00:11.20] Câu tiếng Việt
Không viết gì bên ngoài khối code."""

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
