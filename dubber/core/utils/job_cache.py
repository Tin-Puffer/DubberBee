"""Signatures for cached job artifacts (asr.json, translate.json, …).

Each artifact gets a sidecar ``<name>.sig.json`` holding the parameters that produced it.
An artifact is reused only when its sidecar matches the current parameters exactly —
otherwise a change of VAD / translator / … would silently reuse stale results.
Artifacts from older jobs have no sidecar and are therefore never reused.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def sig_path(artifact: Path) -> Path:
    """asr.json → asr.sig.json"""
    return artifact.with_name(artifact.stem + ".sig.json")


def file_digest(path: Path) -> str:
    """16-char sha1 of a file's content."""
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()[:16]


def sig_matches(artifact: Path, sig: dict) -> bool:
    """True if *artifact* exists and was written with exactly *sig*."""
    sp = sig_path(artifact)
    if not artifact.exists() or not sp.exists():
        return False
    try:
        return json.loads(sp.read_text(encoding="utf-8")) == sig
    except (OSError, ValueError):
        return False


def write_sig(artifact: Path, sig: dict) -> None:
    sig_path(artifact).write_text(json.dumps(sig, ensure_ascii=False, sort_keys=True),
                                  encoding="utf-8")
