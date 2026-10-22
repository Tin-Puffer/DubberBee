"""SubtitleBuilder: generate ASS subtitle file from fitted segments."""
from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dubber.core.video.fonts import DEFAULT_FONT, DEFAULT_FONT_SIZE, scaled_font_px

CHARS_PER_LINE = 38    # wrap when a subtitle is longer than this
MAX_LINE_CHARS = 42    # hard limit per line when balancing two lines
MAX_LINES = 2
MAX_CHUNK_CHARS = 68   # ~2 lines × 34 chars — target max per subtitle chunk
MIN_CHUNK_DURATION = 1.2   # merge chunks shorter than this (seconds)

# Display timing (subtitle on screen ≠ only while the voice speaks)
READ_CPS = 17.0        # comfortable reading speed, characters / second
MIN_DISPLAY = 1.0      # never show a subtitle shorter than this (seconds)
MAX_HOLD = 1.5         # max extra time a subtitle may stay after its speech ends
GAP_BRIDGE = 0.3       # close gaps shorter than this between subtitles (no flicker)

# Vietnamese/Latin sentence endings and clause separators
_SENT_END = re.compile(r'(?<=[.!?])\s+')
_CLAUSE_BREAK = ',;'


@dataclass
class SubtitleStyle:
    font_name: str = DEFAULT_FONT
    font_size: int = DEFAULT_FONT_SIZE  # px on a 1080p frame
    margin_v: int = 20
    margin_h: int = 20
    bold: bool = True
    primary_color: str = "&H00FFFFFF"   # white text
    outline_color: str = "&H00000000"
    back_color: str = "&H55000000"      # ~67% opaque black box (matches reference)
    outline: float = 0.0
    shadow: float = 0.0
    pos: Optional[tuple[int, int]] = None  # (cx, cy) px — center of the text; None → bottom-center


def build_ass(
    segments: list,
    video_width: int,
    video_height: int,
    output_path: Path,
    style: Optional[SubtitleStyle] = None,
) -> Path:
    s = style or SubtitleStyle()
    lines = [
        _header(video_width, video_height),
        _styles_section(s, video_width, video_height),
        _events_section(segments, s.pos),
    ]
    output_path.write_text("\n".join(lines), encoding="utf-8-sig")
    return output_path


# ── Internal ──────────────────────────────────────────────────────────────────

def _header(w: int, h: int) -> str:
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "WrapStyle: 0\n"
        f"PlayResX: {w}\n"
        f"PlayResY: {h}\n"
        "ScaledBorderAndShadow: yes\n"
        "YCbCr Matrix: TV.709\n"
    )


def _styles_section(s: SubtitleStyle, w: int, h: int) -> str:
    bold = -1 if s.bold else 0
    scaled = scaled_font_px(s.font_size, h)
    return (
        "[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,"
        "OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,"
        "ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
        "Alignment,MarginL,MarginR,MarginV,Encoding\n"
        f"Style: Default,{s.font_name},{scaled},"
        f"{s.primary_color},&H000000FF,"
        f"{s.outline_color},{s.back_color},"
        f"{bold},0,0,0,"
        f"100,100,0,0,"
        # BorderStyle 3 = opaque background box (tight around text, matches reference style)
        f"3,{s.outline:.1f},{s.shadow:.1f},"
        f"2,{s.margin_h},{s.margin_h},{s.margin_v},1\n"
    )


def _events_header() -> str:
    return "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text"


def _events_section(segments: list, pos: Optional[tuple[int, int]] = None) -> str:
    # \an5 = anchor at text center, so \pos is the center the user picked
    prefix = f"{{\\an5\\pos({pos[0]},{pos[1]})}}" if pos else ""
    lines = [
        "[Events]",
        _events_header(),
    ]
    events: list[tuple[float, float, str]] = []
    for seg in segments:
        if not seg.vi_text:
            continue
        duration = seg.final_end - seg.final_start
        chunks = _split_to_chunks(seg.vi_text)

        # Distribute time proportionally to character count
        total_chars = sum(len(c) for c in chunks) or 1
        timed: list[tuple[float, float, str]] = []
        t = seg.final_start
        for chunk in chunks:
            chunk_dur = duration * len(chunk) / total_chars
            timed.append((t, t + chunk_dur, chunk))
            t += chunk_dur

        # Merge chunks that are too short to read
        events.extend(_merge_short_chunks(timed, MIN_CHUNK_DURATION))

    for start_t, end_t, text in _retime(events):
        lines.append(
            f"Dialogue: 0,{_ass_time(start_t)},{_ass_time(end_t)},"
            f"Default,,0,0,0,,{prefix}{_wrap_text(text)}"
        )
    return "\n".join(lines) + "\n"


def _retime(events: list[tuple[float, float, str]]) -> list[tuple[float, float, str]]:
    """Keep each subtitle long enough to read, without overlapping the next one.

    Speech timing decides when a subtitle starts; it may then stay into the following
    silence (up to MAX_HOLD) until it reaches READ_CPS / MIN_DISPLAY. Tiny gaps between
    subtitles are closed so the text doesn't blink off and on.
    """
    events = sorted(events)
    out = []
    for i, (s, e, text) in enumerate(events):
        nxt = events[i + 1][0] if i + 1 < len(events) else float("inf")
        want = max(MIN_DISPLAY, len(text) / READ_CPS)
        if e - s < want:
            e = min(s + want, e + MAX_HOLD)
        if 0 < nxt - e < GAP_BRIDGE:
            e = nxt
        out.append((s, max(s + 0.01, min(e, nxt)), text))
    return out


def _split_to_chunks(text: str) -> list[str]:
    """Split translated text into subtitle-sized chunks at natural sentence/clause breaks."""
    if not text:
        return []

    # Step 1: split at sentence endings (. ! ?)
    sentences = [s.strip() for s in _SENT_END.split(text) if s.strip()]
    if not sentences:
        sentences = [text]

    # Step 2: split long sentences at clause separators (, ;)
    chunks: list[str] = []
    for sent in sentences:
        if len(sent) <= MAX_CHUNK_CHARS:
            chunks.append(sent)
        else:
            chunks.extend(_split_at_clause(sent))

    return chunks or [text]


def _split_at_clause(text: str) -> list[str]:
    """Recursively split at the comma/semicolon nearest to midpoint until each part fits."""
    if len(text) <= MAX_CHUNK_CHARS:
        return [text]

    mid = len(text) // 2
    best_idx: int | None = None
    best_dist = len(text)
    for i, ch in enumerate(text):
        if ch in _CLAUSE_BREAK and i + 1 < len(text):
            # Split after the punctuation (keeps comma with preceding clause)
            # Skip trailing punctuation — splitting there leaves right="" and recurses forever
            split_pos = i + 1
            dist = abs(split_pos - mid)
            if dist < best_dist:
                best_dist = dist
                best_idx = split_pos

    if best_idx and best_idx < len(text):
        left = text[:best_idx].strip()
        right = text[best_idx:].strip()
        if left and right:
            return _split_at_clause(left) + _split_at_clause(right)

    # No clause punctuation: split at word boundary nearest to middle
    words = text.split()
    half = len(words) // 2
    if half == 0:
        return [text]
    return [" ".join(words[:half]), " ".join(words[half:])]


def _merge_short_chunks(
    chunks: list[tuple[float, float, str]],
    min_dur: float,
) -> list[tuple[float, float, str]]:
    """Merge any chunk shorter than min_dur with its successor."""
    if not chunks:
        return []
    result = list(chunks)
    changed = True
    while changed:
        changed = False
        new_result: list[tuple[float, float, str]] = []
        i = 0
        while i < len(result):
            start, end, text = result[i]
            if (end - start < min_dur and i + 1 < len(result)
                    and len(text) + 1 + len(result[i + 1][2]) <= MAX_CHUNK_CHARS):
                ns, ne, nt = result[i + 1]
                new_result.append((start, ne, text + " " + nt))
                i += 2
                changed = True
            else:
                new_result.append((start, end, text))
                i += 1
        result = new_result
    return result


def _ass_time(seconds: float) -> str:
    cs = round(seconds * 100)
    h = cs // 360000;  cs %= 360000
    m = cs // 6000;    cs %= 6000
    s = cs // 100;     cs %= 100
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _wrap_text(text: str) -> str:
    """Break into 1–2 balanced lines (never drops text).

    Picks the word boundary that makes both lines similar in length, prefers breaking
    after punctuation and avoids a lone word on either line. Text that can't fit in two
    lines of MAX_LINE_CHARS gets a third line rather than being cut.
    """
    text = " ".join(text.split())
    if len(text) <= CHARS_PER_LINE:
        return text
    words = text.split(" ")
    best: tuple[float, int] | None = None
    for i in range(1, len(words)):
        l1, l2 = " ".join(words[:i]), " ".join(words[i:])
        if max(len(l1), len(l2)) > MAX_LINE_CHARS:
            continue
        cost = abs(len(l1) - len(l2))
        if l1[-1] in ",;:.!?":
            cost -= 8            # natural pause → good place to break
        if i == 1 or i == len(words) - 1:
            cost += 30           # lone word on a line
        if best is None or cost < best[0]:
            best = (cost, i)
    if best is None:
        lines = textwrap.wrap(text, width=MAX_LINE_CHARS, break_long_words=False)
        return r"\N".join(lines)
    i = best[1]
    return " ".join(words[:i]) + r"\N" + " ".join(words[i:])
