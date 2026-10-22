"""Manual translation: export the Chinese script, read back the user's Vietnamese file.

Exported as plain text, one Chinese sentence per line. Accepted back: plain text with
one Vietnamese line per sentence, in order (optionally prefixed "12." / "12)").
An ``.srt`` with the same numbers is still understood.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List

_NUMBERED = re.compile(r"^\s*\[?(\d+)\s*[\].):\-]\s*(.*)$")
_TIMING = re.compile(r"-->")


def write_script_txt(segments, path: Path) -> Path:
    """One Chinese sentence per line (segments: objects with .text)."""
    lines = [" ".join(s.text.split()) for s in segments]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("cp1258", errors="replace")


def _missing_msg(missing: List[int]) -> str:
    shown = ", ".join(map(str, missing[:10])) + (" …" if len(missing) > 10 else "")
    return f"thiếu câu số {shown}"


def parse_vietnamese(path: Path, count: int) -> List[str]:
    """Return exactly *count* Vietnamese lines, or raise ValueError (Vietnamese message)."""
    text = _read_text(Path(path)).replace("\r\n", "\n").replace("\r", "\n")
    blocks = [b.strip("\n") for b in re.split(r"\n\s*\n", text) if b.strip()]

    if blocks and all(_TIMING.search(b) for b in blocks):          # SRT
        by_num = {}
        for b in blocks:
            lines = b.split("\n")
            if not lines[0].strip().isdigit():
                raise ValueError(f"Khối phụ đề không có số thứ tự: {lines[0][:40]!r}")
            body = [ln.strip() for ln in lines[1:] if not _TIMING.search(ln)]
            by_num[int(lines[0])] = " ".join(x for x in body if x)
        missing = [i for i in range(1, count + 1) if not by_num.get(i)]
        extra = sorted(i for i in by_num if i > count or i < 1)
        if missing or extra:
            parts = ([_missing_msg(missing)] if missing else []) + (
                [f"thừa câu số {', '.join(map(str, extra[:10]))}"] if extra else [])
            raise ValueError(f"File SRT phải có đủ {count} câu, đánh số 1–{count}: "
                             + "; ".join(parts))
        return [by_num[i] for i in range(1, count + 1)]

    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    numbered = [_NUMBERED.match(ln) for ln in lines]
    if lines and all(numbered):                                    # "1. …" lines
        by_num = {int(m.group(1)): m.group(2).strip() for m in numbered}
        missing = [i for i in range(1, count + 1) if not by_num.get(i)]
        if missing or len(by_num) != count:
            raise ValueError(f"File phải có đủ {count} câu đánh số 1–{count}: "
                             + (_missing_msg(missing) if missing else "có số thứ tự thừa"))
        return [by_num[i] for i in range(1, count + 1)]
    if len(lines) != count:
        raise ValueError(f"File có {len(lines)} dòng, cần đúng {count} dòng "
                         "(mỗi dòng một câu, theo thứ tự)")
    return lines
