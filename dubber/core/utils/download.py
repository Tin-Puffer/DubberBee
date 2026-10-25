"""HTTP download with resume (Range on a .part file), progress, cancel and sha256 check.

Used for the first-run model download and for app updates — both are large files on
GitHub releases, so an interrupted download continues where it stopped.
"""
from __future__ import annotations

import hashlib
import json
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from dubber import __version__

USER_AGENT = f"DubberBee/{__version__}"
_CHUNK = 1 << 20

Progress = Callable[[int, int], None]   # (bytes done, bytes total — 0 if unknown)


class DownloadCancelled(Exception):
    """The caller's cancel() returned True."""


def _ssl_context() -> ssl.SSLContext:
    # Frozen builds have no system CA path wired into their OpenSSL: use certifi's bundle
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def open_url(url: str, headers: Optional[dict] = None, timeout: float = 30):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    return urllib.request.urlopen(req, timeout=timeout, context=_ssl_context())


def get_json(url: str, timeout: float = 15):
    with open_url(url, {"Accept": "application/vnd.github+json"}, timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def get_text(url: str, timeout: float = 15) -> str:
    with open_url(url, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(_CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def fetch(url: str, dest: Path, *, progress: Optional[Progress] = None,
          cancel: Optional[Callable[[], bool]] = None, sha256: Optional[str] = None) -> Path:
    """Download *url* to *dest*. Bytes go to "<dest>.part" first; a .part left by an
    interrupted run is continued with a Range request. Raises DownloadCancelled, or
    ValueError when the finished file does not match *sha256*."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    try:
        resp = open_url(url, headers)
    except urllib.error.HTTPError as exc:
        if exc.code != 416 or not have:   # 416: the .part already holds the whole file
            raise
        resp = None
    if resp is not None:
        with resp:
            if have and resp.status != 206:   # server ignored Range → start over
                have = 0
            length = int(resp.headers.get("Content-Length") or 0)
            total = have + length if length else 0
            with open(part, "ab" if have else "wb") as f:
                done = have
                while True:
                    if cancel and cancel():
                        raise DownloadCancelled()
                    block = resp.read(_CHUNK)
                    if not block:
                        break
                    f.write(block)
                    done += len(block)
                    if progress:
                        progress(done, total)
            if total and done < total:
                raise ConnectionError(f"Tải chưa xong ({done}/{total} bytes) — thử lại để tải tiếp")
    if sha256 and sha256_of(part) != sha256.lower():
        part.unlink(missing_ok=True)
        raise ValueError(f"File tải về bị lỗi (sai checksum): {dest.name}")
    part.replace(dest)
    return dest
