"""In-app update from GitHub Releases.

check() finds the newest "vX.Y.Z" release carrying this platform's zip; download() fetches
it (resumable) and unpacks it next to the installed app; apply() starts a small script
that waits for the app to quit, swaps the folders and starts the new version.

The installed app is replaced as a whole folder (Dubber.app / the Dubber folder). Settings,
models, voices and caches live in the user-data dirs, so they are kept.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from platformdirs import user_cache_dir

from dubber import RELEASES_REPO, __version__

log = logging.getLogger(__name__)

REPO = RELEASES_REPO
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases?per_page=30"
_TAG = re.compile(r"^v(\d+(?:\.\d+)*)$")


def asset_name() -> str:
    return "Dubber-windows.zip" if sys.platform == "win32" else "Dubber-macos.zip"


def parse_version(text: str) -> tuple[int, ...]:
    m = _TAG.match(text if text.startswith("v") else "v" + text)
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


@dataclass(frozen=True)
class Release:
    version: str        # "0.4.0"
    notes: str          # release body (markdown)
    url: str            # asset download URL
    size: int
    sha256: Optional[str]
    page: str           # release page (for "update by hand")


def check(current: str = __version__) -> Optional[Release]:
    """Newest published release newer than *current* that has this platform's zip."""
    from dubber.core.utils.download import get_json
    best: Optional[Release] = None
    for r in get_json(RELEASES_API):
        if r.get("draft") or r.get("prerelease"):
            continue
        ver = parse_version(r.get("tag_name", ""))
        if not ver or ver <= parse_version(current):
            continue
        if best and ver <= parse_version(best.version):
            continue
        asset = next((a for a in r.get("assets", []) if a.get("name") == asset_name()), None)
        if not asset:
            continue
        digest = asset.get("digest") or ""
        best = Release(
            version=".".join(map(str, ver)), notes=r.get("body") or "",
            url=asset["browser_download_url"], size=int(asset.get("size") or 0),
            sha256=digest.split(":", 1)[1] if digest.startswith("sha256:") else None,
            page=r.get("html_url") or f"https://github.com/{REPO}/releases",
        )
    return best


# ── where the running app is ──────────────────────────────────────────────────

def install_dir() -> Optional[Path]:
    """The folder replaced on update: …/Dubber.app (macOS) or the folder holding
    Dubber.exe (Windows). None when running from source."""
    if not getattr(sys, "frozen", False):
        return None
    exe = Path(sys.executable).resolve()
    if sys.platform == "darwin":
        for p in exe.parents:
            if p.suffix == ".app":
                return p
        return None
    return exe.parent


def cannot_update_reason() -> Optional[str]:
    """Why this copy cannot replace itself (shown with a link to update by hand)."""
    d = install_dir()
    if d is None:
        return "Đang chạy từ mã nguồn — cập nhật bằng git pull."
    if "AppTranslocation" in str(d):
        return ("macOS đang chạy app từ vị trí tạm. Hãy kéo Dubber.app vào thư mục "
                "Applications rồi mở lại, sau đó cập nhật.")
    if not _writable(d.parent):
        return f"Không có quyền ghi vào thư mục chứa app:\n{d.parent}"
    return None


def _writable(folder: Path) -> bool:
    # os.access is unreliable for folders on Windows (Program Files reads as writable)
    try:
        with tempfile.TemporaryFile(dir=folder):
            return True
    except OSError:
        return False


# ── download + unpack ─────────────────────────────────────────────────────────

def _cache_dir() -> Path:
    return Path(user_cache_dir("dubber", "dubber")) / "update"


def download(rel: Release, progress: Optional[Callable[[int, int], None]] = None,
             cancel: Optional[Callable[[], bool]] = None) -> Path:
    """Fetch the zip (continues an interrupted download) and unpack it beside the installed
    app (same volume → the swap is a rename). Returns the unpacked app folder."""
    from dubber.core.utils.download import fetch
    target = install_dir()
    if target is None:
        raise RuntimeError("Không xác định được thư mục app")
    cache = _cache_dir()
    for old in cache.glob("*.zip"):   # zips of other versions
        if old.name != f"{rel.version}-{asset_name()}":
            old.unlink(missing_ok=True)
    zpath = cache / f"{rel.version}-{asset_name()}"
    if not zpath.exists():
        fetch(rel.url, zpath, progress=progress, cancel=cancel, sha256=rel.sha256)
    if rel.size and zpath.stat().st_size != rel.size:
        zpath.unlink(missing_ok=True)
        raise ValueError("File cập nhật tải về không đủ dung lượng — thử lại")

    staging = target.parent / f".{target.name}.update"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    try:
        _unzip(zpath, staging)
        new = staging / ("Dubber.app" if sys.platform == "darwin" else "Dubber")
        exe = new / ("Contents/MacOS/Dubber" if sys.platform == "darwin" else "Dubber.exe")
        if not exe.is_file():
            raise ValueError("File cập nhật không chứa app Dubber")
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        zpath.unlink(missing_ok=True)
        raise
    return new


def _unzip(zpath: Path, dest: Path) -> None:
    if sys.platform == "darwin":
        # ditto keeps the bundle's symlinks + permissions (zipfile drops both)
        subprocess.run(["ditto", "-x", "-k", str(zpath), str(dest)], check=True,
                       capture_output=True)
    else:
        with zipfile.ZipFile(zpath) as zf:
            zf.extractall(dest)


# ── swap + restart ────────────────────────────────────────────────────────────

_MAC_SCRIPT = r"""#!/bin/bash
# Dubber Bee update: wait for the app to quit, swap Dubber.app, start the new one
APP="$1"; NEW="$2"; PID="$3"; ZIP="$4"
for _ in $(seq 1 600); do kill -0 "$PID" 2>/dev/null || break; sleep 0.2; done
OLD="$APP.old-update"
rm -rf "$OLD"
if mv "$APP" "$OLD"; then
    if mv "$NEW" "$APP"; then
        rm -rf "$OLD" "$ZIP"
    else
        mv "$OLD" "$APP"
    fi
fi
xattr -dr com.apple.quarantine "$APP" 2>/dev/null
rm -rf "$(dirname "$NEW")"
open "$APP"
rm -f "$0"
"""

_WIN_SCRIPT = r"""# Dubber Bee update: wait for the app to quit, swap the folder, start the new one
param([string]$App, [string]$New, [int]$AppPid, [string]$Zip)
Wait-Process -Id $AppPid -Timeout 120 -ErrorAction SilentlyContinue
$old = "$App.old-update"
Remove-Item $old -Recurse -Force -ErrorAction SilentlyContinue
$moved = $false
for ($i = 0; $i -lt 30 -and -not $moved; $i++) {
    try { Rename-Item $App (Split-Path $old -Leaf) -ErrorAction Stop; $moved = $true }
    catch { Start-Sleep -Milliseconds 500 }   # a file still in use (antivirus, Explorer)
}
if ($moved) {
    try {
        Move-Item $New $App -ErrorAction Stop
        Remove-Item $old -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item $Zip -Force -ErrorAction SilentlyContinue
    } catch {
        Rename-Item $old (Split-Path $App -Leaf)
    }
}
Remove-Item (Split-Path $New) -Recurse -Force -ErrorAction SilentlyContinue
Start-Process (Join-Path $App "Dubber.exe")
Remove-Item $PSCommandPath -Force -ErrorAction SilentlyContinue
"""


def _clean_env() -> dict:
    """Environment for the helper / the relaunched app without PyInstaller's runtime vars
    (a new Dubber.exe that inherits them treats itself as a child of this one)."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("_PYI_", "_MEIPASS"))}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def apply(new_app: Path, version: str) -> None:
    """Start the swap script; the caller must quit the app right after."""
    target = install_dir()
    if target is None:
        raise RuntimeError("Không xác định được thư mục app")
    zpath = _cache_dir() / f"{version}-{asset_name()}"
    fd, script = tempfile.mkstemp(prefix="dubber-update-",
                                  suffix=".ps1" if sys.platform == "win32" else ".sh")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(_WIN_SCRIPT if sys.platform == "win32" else _MAC_SCRIPT)
    args = [str(target), str(new_app), str(os.getpid()), str(zpath)]
    log.info("Update to %s: %s → %s", version, new_app, target)
    if sys.platform == "win32":
        flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                 | subprocess.CREATE_NO_WINDOW)
        subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                          "-WindowStyle", "Hidden", "-File", script, *args],
                         env=_clean_env(), creationflags=flags, close_fds=True,
                         cwd=tempfile.gettempdir())   # not inside the folder being swapped
    else:
        subprocess.Popen(["/bin/bash", script, *args], env=_clean_env(),
                         start_new_session=True, close_fds=True, cwd=tempfile.gettempdir(),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
