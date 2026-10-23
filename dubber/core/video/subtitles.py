"""SubtitleBuilder: generate ASS subtitle file from fitted segments.

Every subtitle is ONE line: each translated segment is cut into cues at natural breaks
(punctuation, before a connective, never after a weak word), measured with the real font.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from dubber.core.translate.normalize_vi import normalize_cue
from dubber.core.video.fonts import DEFAULT_FONT, DEFAULT_FONT_SIZE, scaled_font_px
from dubber.core.video.text_metrics import line_height, text_width

MIN_CHUNK_DURATION = 1.2   # merge chunks shorter than this (seconds)
MAX_LINE_FRACTION = 0.9    # a line never spans more than this share of the frame width
MAX_CUE_CHARS = 50         # longer lines are hard to read in one glance, even if they fit

# Display timing (subtitle on screen ≠ only while the voice speaks)
READ_CPS = 17.0        # comfortable reading speed, characters / second
MIN_DISPLAY = 1.0      # never show a subtitle shorter than this (seconds)
MAX_HOLD = 1.5         # max extra time a subtitle may stay after its speech ends
GAP_BRIDGE = 0.3       # close gaps shorter than this between subtitles (no flicker)

# Vietnamese/Latin sentence endings
_SENT_END = re.compile(r'(?<=[.!?…])\s+')

# ── Where to cut a line (scores for _best_cut) ────────────────────────────────
# A cue may start with these (cut *before* them)
_CONNECTIVES = (
    "chính là", "cho nên", "bởi vì", "trong khi", "sau khi", "trước khi", "vì vậy",
    "tuy nhiên", "nhưng", "và", "để", "khi", "vì", "nên", "mà", "thì", "nếu", "rồi",
    "còn", "hay", "hoặc", "là", "với",
)
# A cue starting with a preposition reads fine (smaller bonus than a connective)
_PHRASE_START = frozenset("của vào trong trên dưới tại ở từ đến cho bằng như sau trước".split())
# …but must not end with these (they need the word after them)
_WEAK_END = frozenset(
    "chính là của và các những một cho với để được đã sẽ đang rất cái con nhiều "
    "mỗi từ ở trong trên dưới tại bị do vào ra lên theo như thật quá "
    "vừa đều cũng lại vẫn chưa không hơi khá luôn hay thường đầy".split()
)
# Common two-syllable words that must stay on one line (fallback without pyvi)
_COMPOUNDS_LOWER = frozenset(tuple(p.split()) for p in (
    "độc đáo", "nguyên liệu", "nguyên sinh", "thưởng thức", "đặc biệt", "chế biến",
    "phô mai", "vân sam", "địa phương", "người dân", "rậm rạp", "cao lớn", "tận dụng",
    "chìa khóa", "bình thường", "đặc trưng", "hòa quyện", "đậm đà", "thơm béo", "sẵn có",
    "trung quốc", "tây tạng", "món ăn", "buổi sáng", "sáng sớm", "mềm dai", "năng lượng",
    "nhựa cây", "lao động", "liên tục", "bổ sung", "hoàn toàn", "tự nhiên", "lên men",
))


@dataclass
class SubtitleStyle:
    font_name: str = DEFAULT_FONT
    font_size: int = DEFAULT_FONT_SIZE  # px on a 1080p frame
    margin_v: int = 20
    margin_h: int = 20
    bold: bool = True
    primary_color: str = "&H00FFFFFF"   # white text
    outline_color: str = "&H00000000"   # black outline
    back_color: str = "&H55000000"      # ~67% opaque black box (only with box=True)
    outline: float = 2.0                # px on a 1080p frame
    shadow: float = 0.0
    box: bool = False                   # dark box behind the text (the pre-M10 look)
    pos: Optional[tuple[int, int]] = None  # (cx, cy) px — center of the text; None → bottom-center


def hex_to_ass(color: str, alpha: int = 0) -> str:
    """'#RRGGBB' → ASS '&HAABBGGRR' (ASS stores blue first; alpha 0 = opaque)."""
    c = color.lstrip("#")
    if len(c) != 6:
        raise ValueError(f"bad color {color!r}")
    return f"&H{alpha:02X}{c[4:6]}{c[2:4]}{c[0:2]}".upper()


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
        _events_section(segments, s, video_width, video_height),
    ]
    output_path.write_text("\n".join(lines), encoding="utf-8-sig")
    return output_path


def cue_size_px(text: str, style: SubtitleStyle, video_h: int) -> tuple[float, float]:
    """(width, height) in video px of one subtitle line as libass draws it, outline included."""
    size = scaled_font_px(style.font_size, video_h)
    border = 2 * _outline_px(style, video_h)
    return (text_width(text, style.font_name, size) + border,
            line_height(style.font_name, size) + border)


def max_line_px(style: SubtitleStyle, video_w: int) -> float:
    """Widest a subtitle line may be: the frame minus margins, around the chosen center."""
    room = video_w - 2 * style.margin_h
    if style.pos:
        room = 2 * min(style.pos[0], video_w - style.pos[0]) - 2 * style.margin_h
    return max(video_w * 0.3, min(room, video_w * MAX_LINE_FRACTION))


# ── Internal ──────────────────────────────────────────────────────────────────

def _outline_px(s: SubtitleStyle, h: int) -> float:
    return s.outline * h / 1080


def _header(w: int, h: int) -> str:
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "WrapStyle: 2\n"          # never wrap: lines are already cut to fit
        f"PlayResX: {w}\n"
        f"PlayResY: {h}\n"
        "ScaledBorderAndShadow: yes\n"
        "YCbCr Matrix: TV.709\n"
    )


_STYLE_FORMAT = (
    "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,"
    "OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,"
    "ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
    "Alignment,MarginL,MarginR,MarginV,Encoding"
)


def _styles_section(s: SubtitleStyle, w: int, h: int) -> str:
    bold = -1 if s.bold else 0
    scaled = scaled_font_px(s.font_size, h)
    tail = f"{bold},0,0,0,100,100,0,0"
    margins = f"2,{s.margin_h},{s.margin_h},{s.margin_v},1"
    lines = ["[V4+ Styles]", _STYLE_FORMAT]
    # BorderStyle 1 = outline around the glyphs
    lines.append(
        f"Style: Default,{s.font_name},{scaled},{s.primary_color},&H000000FF,"
        f"{s.outline_color},&H00000000,{tail},"
        f"1,{_outline_px(s, h):.1f},{s.shadow:.1f},{margins}"
    )
    if s.box:
        # BorderStyle 3 = opaque box; text itself invisible — drawn under the Default layer
        # (one style can't have both an outline and a box)
        lines.append(
            f"Style: Box,{s.font_name},{scaled},&HFF000000,&HFF000000,"
            f"{s.back_color},{s.back_color},{tail},"
            f"3,{_outline_px(s, h):.1f},0.0,{margins}"
        )
    return "\n".join(lines) + "\n"


def _events_header() -> str:
    return "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text"


def _events_section(segments: list, s: SubtitleStyle, w: int, h: int) -> str:
    # \an5 = anchor at text center, so \pos is the center the user picked
    prefix = f"{{\\an5\\pos({s.pos[0]},{s.pos[1]})}}" if s.pos else ""
    limit = max_line_px(s, w)

    def fits(text: str) -> bool:
        return len(text) <= MAX_CUE_CHARS and cue_size_px(text, s, h)[0] <= limit

    lines = ["[Events]", _events_header()]
    events: list[tuple[float, float, str]] = []
    for seg in segments:
        if not seg.vi_text:
            continue
        duration = seg.final_end - seg.final_start
        chunks = _split_to_chunks(seg.vi_text, fits)

        # Distribute time proportionally to character count
        total_chars = sum(len(c) for c in chunks) or 1
        timed: list[tuple[float, float, str]] = []
        t = seg.final_start
        for chunk in chunks:
            chunk_dur = duration * len(chunk) / total_chars
            timed.append((t, t + chunk_dur, chunk))
            t += chunk_dur

        # Merge chunks that are too short to read (only while the result stays one line)
        merged = _merge_short_chunks(timed, MIN_CHUNK_DURATION, fits)
        events.extend((a, b, normalize_cue(text)) for a, b, text in merged)

    for start_t, end_t, text in _retime(events):
        start, end = _ass_time(start_t), _ass_time(end_t)
        if s.box:
            lines.append(f"Dialogue: 0,{start},{end},Box,,0,0,0,,{prefix}{text}")
        lines.append(f"Dialogue: 1,{start},{end},Default,,0,0,0,,{prefix}{text}")
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


def _split_to_chunks(text: str, fits: Callable[[str], bool]) -> list[str]:
    """Cut translated text into one-line cues: sentences first, then the best cut point."""
    text = " ".join((text or "").split())
    if not text:
        return []
    chunks: list[str] = []
    for sent in (p for p in _SENT_END.split(text) if p.strip()):
        words = sent.strip().split(" ")
        chunks.extend(_split_line(words, _glued(words), 0, len(words), fits))
    return chunks or [text]


def _split_line(words: list[str], glued: set[int], lo: int, hi: int,
                fits: Callable[[str], bool]) -> list[str]:
    """Recursively cut words[lo:hi] at the best boundary until every part fits on one line."""
    text = " ".join(words[lo:hi])
    if fits(text) or hi - lo < 2:
        return [text]
    i = _best_cut(words, glued, lo, hi)
    return _split_line(words, glued, lo, i, fits) + _split_line(words, glued, i, hi, fits)


def _bare(word: str) -> str:
    return word.lower().strip(",;:.!?…\"'“”()")


def _glued(words: list[str]) -> set[int]:
    """Cut positions i (between words[i-1] and words[i]) that fall inside a word.

    Vietnamese words are often two syllables ("năng lượng"); pyvi's segmenter finds them
    when installed, a short list of common ones is the fallback.
    """
    bare = [_bare(w) for w in words]
    glued = {i for i in range(1, len(words)) if (bare[i - 1], bare[i]) in _COMPOUNDS_LOWER}
    try:
        from pyvi import ViTokenizer
        tokens = ViTokenizer.tokenize(" ".join(words)).split()
    except Exception:         # not installed / model missing in a bundle → word list only
        return glued
    sylls: list[bool] = []   # per syllable: is it glued to the next one?
    for tok in tokens:
        parts = [p for p in tok.split("_") if any(c.isalnum() for c in p)]
        sylls.extend([True] * (len(parts) - 1) + [False] * bool(parts))
    if len(sylls) != sum(1 for b in bare if any(c.isalnum() for c in b)):
        return glued          # tokenizer split differently (numbers, symbols) — don't guess
    k = 0
    for i, b in enumerate(bare):
        if any(c.isalnum() for c in b):
            if sylls[k] and i + 1 < len(words):
                glued.add(i + 1)
            k += 1
    return glued


def _best_cut(words: list[str], glued: set[int], lo: int, hi: int) -> int:
    """Index i to cut words[lo:i] | words[i:hi], lowest cost wins.

    Balanced halves are preferred; cutting after , ; : or before a connective is cheap,
    leaving a weak word at the end of a line, splitting a word or stranding one or two
    words is expensive.
    """
    best_i, best_cost = (lo + hi) // 2, float("inf")
    total = len(" ".join(words[lo:hi]))
    left_len = -1
    for i in range(lo + 1, hi):
        left_len += len(words[i - 1]) + 1
        cost = abs(left_len - (total - left_len - 1)) * 0.5
        last = words[i - 1]
        if last[-1] in ".!?…":
            cost -= 30
        elif last[-1] in ",;:":
            cost -= 20
        else:
            if _bare(last) in _WEAK_END:
                cost += 40
            if i in glued:
                cost += 50
        nxt = " ".join(_bare(w) for w in words[i:i + 2])
        if any(nxt == c or nxt.startswith(c + " ") for c in _CONNECTIVES):
            cost -= 12
        elif _bare(words[i]) in _PHRASE_START:
            cost -= 6
        if min(i - lo, hi - i) <= 2:
            cost += 30
        if cost < best_cost:
            best_i, best_cost = i, cost
    return best_i


def _merge_short_chunks(
    chunks: list[tuple[float, float, str]],
    min_dur: float,
    fits: Callable[[str], bool],
) -> list[tuple[float, float, str]]:
    """Merge any chunk shorter than min_dur with its successor when the result still fits."""
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
                    and fits(text + " " + result[i + 1][2])):
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
