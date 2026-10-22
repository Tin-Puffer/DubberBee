"""ASR segment post-processing and asr.json serialisation."""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Literal, Optional, Tuple

from dubber.core.asr.base import ASRSegment

MIN_SEGMENT_S = 1.0   # merge segments shorter than this into the previous one
MAX_SEGMENT_S = 6.0   # legacy: split segments longer than this at punctuation
MAX_SENTENCE_S = 8.0  # timestamps: longest subtitle line (2 lines still readable)
VAD_MAX_SPEECH_S = 20.0  # timestamps: VAD only cuts coarsely, sentences are cut later

_SPLIT_PUNCT = frozenset("。！？；，、")

# timestamps mode
_HARD_END = frozenset("。！？…?!")
_SOFT_END = frozenset("，；、：,;:")
SILENCE_GAP_S = 0.8     # start-to-start gap between two words that ends a sentence
                        # (includes the first word itself, ~0.2–0.3 s → ~0.5 s of silence)
_END_PAD_S = 0.2        # a sentence ends this long after its last token's timestamp …
_WORD_TAIL_S = 0.5      # … but at most this long after its last word: SenseVoice can emit
                        # the final punctuation seconds later, at the end of the VAD chunk
STRICT_MERGE_GAP_S = 0.3


def drop_empty(segments: List[ASRSegment]) -> List[ASRSegment]:
    """Remove segments with blank text."""
    return [s for s in segments if s.text.strip()]


def split_long(
    segments: List[ASRSegment],
    max_s: float = MAX_SEGMENT_S,
) -> List[ASRSegment]:
    """Split any segment longer than max_s at sentence-end punctuation.

    Time is distributed proportionally to character count.
    Segments with no split punctuation are left as-is.
    """
    result: List[ASRSegment] = []
    for seg in segments:
        result.extend(_split_one(seg, max_s))
    return result


MERGE_MAX_GAP_S = 0.5   # don't glue a short segment onto one that ended longer ago
_LEAD_S = 0.10          # start a split segment this long before its first word
_TAIL_S = 0.25          # keep this much after the punctuation that ends a segment


def _split_one(seg: ASRSegment, max_s: float) -> List[ASRSegment]:
    dur = seg.end - seg.start
    if dur <= max_s:
        return [seg]
    if seg.token_times:
        return _split_by_token_times(seg, max_s)

    text = seg.text
    total_chars = max(len(text), 1)
    chars_per_s = total_chars / dur

    # Split text into sentences at punctuation marks
    sentences: List[str] = []
    start = 0
    for i, ch in enumerate(text):
        if ch in _SPLIT_PUNCT:
            sentences.append(text[start:i + 1])
            start = i + 1
    if start < len(text):
        sentences.append(text[start:])

    if len(sentences) <= 1:
        return [seg]  # no split points — keep as-is

    result: List[ASRSegment] = []
    group: List[str] = []
    group_start_t = seg.start

    for sentence in sentences:
        prospective_dur = (sum(len(s) for s in group) + len(sentence)) / chars_per_s
        if group and prospective_dur > max_s:
            # Commit accumulated group before adding this sentence
            committed_chars = sum(len(s) for s in group)
            group_end_t = round(group_start_t + committed_chars / chars_per_s, 3)
            result.append(ASRSegment(
                start=round(group_start_t, 3),
                end=group_end_t,
                text="".join(group).strip(),
            ))
            group_start_t = group_end_t
            group = [sentence]
        else:
            group.append(sentence)

    if group:
        tail = "".join(group).strip()
        if tail:
            result.append(ASRSegment(
                start=round(group_start_t, 3),
                end=round(seg.end, 3),
                text=tail,
            ))

    return result if result else [seg]


def _split_by_token_times(seg: ASRSegment, max_s: float) -> List[ASRSegment]:
    """Split at punctuation using the recognizer's real per-token times.

    Same greedy grouping as the char-proportional split, but each cut lands where the
    speaker actually paused (proportional estimates were off by ~0.4 s, up to 1 s).
    """
    # sentences: (text, first_word_time, punctuation_time)
    sentences: List[Tuple[str, float, float]] = []
    buf, t_first = "", None
    for tok, t in seg.token_times:
        buf += tok
        if t_first is None and tok not in _SPLIT_PUNCT:
            t_first = t
        if tok in _SPLIT_PUNCT:
            if buf.strip(tok).strip():
                sentences.append((buf, t_first if t_first is not None else t, t))
            buf, t_first = "", None
    if buf.strip():
        sentences.append((buf, t_first if t_first is not None else seg.end, seg.end))
    if len(sentences) <= 1:
        return [seg]

    result: List[ASRSegment] = []
    group: List[Tuple[str, float, float]] = []
    group_start = seg.start
    for sent in sentences:
        if group and sent[2] - group_start > max_s:
            next_first = sent[1]
            end = min(group[-1][2] + _TAIL_S, next_first)
            result.append(ASRSegment(start=round(group_start, 3), end=round(end, 3),
                                     text="".join(x[0] for x in group).strip()))
            group_start = max(end, next_first - _LEAD_S)
            group = [sent]
        else:
            group.append(sent)
    result.append(ASRSegment(start=round(group_start, 3), end=round(seg.end, 3),
                             text="".join(x[0] for x in group).strip()))
    return result


def merge_short(
    segments: List[ASRSegment],
    min_s: float = MIN_SEGMENT_S,
    max_gap_s: float = MERGE_MAX_GAP_S,
    max_s: Optional[float] = None,
) -> List[ASRSegment]:
    """Merge segments shorter than min_s into a neighbour.

    A short segment joins the preceding one when the pause between them is ≤ max_gap_s
    — gluing across a long pause would stretch the line over the silence. With *max_s*
    set, a merge must also keep the result ≤ max_s, and a segment that can't join the
    previous one tries the next one under the same rules; otherwise it stays alone.
    """
    def fits(a: ASRSegment, b: ASRSegment) -> bool:
        return b.start - a.end <= max_gap_s and (max_s is None or b.end - a.start <= max_s)

    def join(a: ASRSegment, b: ASRSegment) -> ASRSegment:
        return ASRSegment(start=a.start, end=b.end, text=a.text + b.text)

    result: List[ASRSegment] = []
    pending: Optional[ASRSegment] = None   # short segment waiting to join the next one
    for seg in segments:
        seg = ASRSegment(start=seg.start, end=seg.end, text=seg.text)
        if pending is not None:
            if fits(pending, seg):
                seg = join(pending, seg)
            else:
                result.append(pending)
            pending = None
        if seg.end - seg.start < min_s:
            if result and fits(result[-1], seg):
                result[-1] = join(result[-1], seg)
                continue
            if max_s is not None:
                pending = seg
                continue
        result.append(seg)
    if pending is not None:
        result.append(pending)
    return result


def split_by_timestamps(
    segments: List[ASRSegment],
    max_s: float = MAX_SENTENCE_S,
) -> List[ASRSegment]:
    """Cut each VAD segment into sentences using per-token timestamps.

    Hard boundaries: 。！？…?! (and "." before a space / at the end), or a pause of more
    than SILENCE_GAP_S between two words. Soft boundaries (，；、：) are used only when the
    sentence would otherwise exceed *max_s* — the last one before the limit wins.
    Segments without token times are passed through unchanged.
    """
    result: List[ASRSegment] = []
    for seg in segments:
        if not seg.token_times:
            result.append(ASRSegment(start=seg.start, end=seg.end, text=seg.text))
            continue
        result.extend(_sentences_from_tokens(seg, max_s))
    return result


def _is_hard_end(toks: List[Tuple[str, float]], i: int) -> bool:
    tok = toks[i][0].strip()
    if tok and tok[-1] in _HARD_END:
        return True
    if tok.endswith("."):   # "." ends a sentence, not "3.5"
        return i + 1 == len(toks) or toks[i + 1][0][:1].isspace()
    return False


def _is_punct(tok: str) -> bool:
    t = tok.strip()
    return bool(t) and all(c in _HARD_END or c in _SOFT_END or c == "." for c in t)


def _sentences_from_tokens(seg: ASRSegment, max_s: float) -> List[ASRSegment]:
    toks = [(tok, t) for tok, t in seg.token_times if tok and not tok.startswith("<|")]
    groups: List[List[Tuple[str, float]]] = []
    cur: List[Tuple[str, float]] = []
    last_soft = -1          # index in cur just after the last soft punctuation
    last_word_t: Optional[float] = None

    def close(upto: int) -> None:
        nonlocal cur, last_soft
        if any(not _is_punct(tok) for tok, _ in cur[:upto]):
            groups.append(cur[:upto])
        elif groups:   # stray punctuation → belongs to the sentence before
            groups[-1].extend(cur[:upto])
        cur = cur[upto:]
        last_soft = -1

    for i, (tok, t) in enumerate(toks):
        word = not _is_punct(tok)
        if word and cur and last_word_t is not None and t - last_word_t > SILENCE_GAP_S:
            close(len(cur))
        # the line also gets _LEAD_S before its first word and up to _WORD_TAIL_S after
        if word and cur and t - _first_word_t(cur, t) > max_s - _LEAD_S - _WORD_TAIL_S \
                and last_soft > 0:
            close(last_soft)
        cur.append((tok, t))
        if word:
            last_word_t = t
        if _is_hard_end(toks, i):
            close(len(cur))
        elif tok.strip() and tok.strip()[-1] in _SOFT_END:
            last_soft = len(cur)
    if cur:
        close(len(cur))

    out: List[ASRSegment] = []
    for k, g in enumerate(groups):
        start = max(seg.start, _first_word_t(g, g[0][1]) - _LEAD_S)
        if out:
            start = max(start, out[-1].end)
        end = min(seg.end, g[-1][1] + _END_PAD_S, _last_word_t(g) + _WORD_TAIL_S)
        if k + 1 < len(groups):
            end = min(end, _first_word_t(groups[k + 1], groups[k + 1][0][1]) - _LEAD_S)
        end = max(end, start + 0.05)
        text = "".join(tok for tok, _ in g).strip()
        out.append(ASRSegment(start=round(start, 3), end=round(end, 3), text=text))
    return out or [ASRSegment(start=seg.start, end=seg.end, text=seg.text)]


def _last_word_t(group: List[Tuple[str, float]]) -> float:
    for tok, t in reversed(group):
        if not _is_punct(tok) and tok.strip():
            return t
    return group[-1][1]


def _first_word_t(group: List[Tuple[str, float]], default: float) -> float:
    for tok, t in group:
        if not _is_punct(tok) and tok.strip():
            return t
    return default


SplitMode = Literal["timestamps", "legacy"]


def postprocess(
    segments: List[ASRSegment],
    min_s: float = MIN_SEGMENT_S,
    max_s: Optional[float] = None,
    mode: SplitMode = "timestamps",
) -> List[ASRSegment]:
    """Full post-processing pipeline.

    timestamps: drop_empty → split_by_timestamps → merge_short (≤0.3 s gap, ≤ max_s).
    legacy:     drop_empty → split_long → merge_short — also the fallback when the
                recognizer gave no token timestamps.
    """
    segs = drop_empty(segments)
    if mode == "timestamps" and segs and all(s.token_times for s in segs):
        max_s = max_s or MAX_SENTENCE_S
        segs = split_by_timestamps(segs, max_s)
        return merge_short(segs, min_s, max_gap_s=STRICT_MERGE_GAP_S, max_s=max_s)
    segs = split_long(segs, max_s or MAX_SEGMENT_S)
    segs = merge_short(segs, min_s)
    return segs


def write_asr_json(segments: List[ASRSegment], path: Path) -> None:
    """Write segments to path as UTF-8 JSON array of {start, end, text}."""
    data = [
        {"start": s.start, "end": s.end, "text": s.text}
        for s in segments
    ]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
