"""Vietnamese text clean-up after translation (spacing, capitals, dangling punctuation).

Every function is idempotent: running it on its own output changes nothing, so cached
translations can be normalized again on every run without re-translating.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Dict, Optional

_HARD_END_ZH = tuple("。！？…?!")
_HARD_END_VI = (".", "!", "?", "…")
_DANGLING = ",;:-–— "                 # never end a line / sentence with these

_SPACE_BEFORE = re.compile(r"\s+([,.!?;:…])")
# one space after , ; : ! ? — except inside numbers (1,5 · 10:30) and before closing marks
_SPACE_AFTER = re.compile(r"([,;:!?])(?=[^\s\d,.;:!?…\"'”’)\]}])|(?<=\D)([,;:])(?=\d)")
_SENT_START = re.compile(r"([.!?…]\s+)(\W*)(\w)")


def _cap_first(text: str) -> str:
    """Upper-case the first letter, leaving the rest alone (str.capitalize() would
    lower-case proper names further on)."""
    for i, ch in enumerate(text):
        if ch.isalpha():
            return text[:i] + ch.upper() + text[i + 1:]
        if ch.isdigit():
            return text
    return text


def _space_after(m: re.Match) -> str:
    return (m.group(1) or m.group(2)) + " "


def clean_spacing(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "")
    text = " ".join(text.split())
    text = _SPACE_BEFORE.sub(r"\1", text)
    return _SPACE_AFTER.sub(_space_after, text)


def apply_names(text: str, names: Optional[Dict[str, str]]) -> str:
    """Write proper names the way the translator spelled them (case-insensitive match)."""
    for key in sorted(names or {}, key=len, reverse=True):
        canon = names[key]
        text = re.sub(rf"(?<!\w){re.escape(key)}(?!\w)", canon, text, flags=re.IGNORECASE)
    return text


def normalize_text(vi: str, zh: str = "", names: Optional[Dict[str, str]] = None) -> str:
    """One translated segment (what TTS reads and the subtitles show)."""
    text = clean_spacing(vi)
    if not text:
        return ""
    text = apply_names(text, names)
    text = text.rstrip(_DANGLING)
    if zh.strip().endswith(_HARD_END_ZH) and not text.endswith(_HARD_END_VI):
        text += "."
    text = _SENT_START.sub(lambda m: m.group(1) + m.group(2) + m.group(3).upper(), text)
    return _cap_first(text)


def normalize_cue(text: str) -> str:
    """One on-screen subtitle line: capital first letter, no dangling , ; :"""
    return _cap_first(" ".join(text.split()).rstrip(_DANGLING))
