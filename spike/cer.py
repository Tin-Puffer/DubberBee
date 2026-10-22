"""Character Error Rate (CER) for Chinese text."""
from __future__ import annotations
import unicodedata


def _normalize(text: str) -> list[str]:
    """Strip punctuation/spaces, return list of CJK + Latin chars."""
    keep = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat.startswith("P") or cat == "Zs" or ch in " \t\n\r":
            continue
        keep.append(ch.lower())
    return keep


def _edit_distance(a: list, b: list) -> int:
    n, m = len(a), len(b)
    dp = list(range(m + 1))
    for i in range(1, n + 1):
        prev = dp[:]
        dp[0] = i
        for j in range(1, m + 1):
            if a[i - 1] == b[j - 1]:
                dp[j] = prev[j - 1]
            else:
                dp[j] = 1 + min(prev[j], dp[j - 1], prev[j - 1])
    return dp[m]


def cer(reference: str, hypothesis: str) -> float:
    """Return CER as a fraction 0–1 (lower is better)."""
    ref = _normalize(reference)
    hyp = _normalize(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    dist = _edit_distance(ref, hyp)
    return dist / len(ref)
