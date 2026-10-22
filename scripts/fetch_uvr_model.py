"""Make sure the UVR MDX-Net model + audio-separator's model lists are cached; print the dir.

Used by the build scripts to bundle separation for offline use:
    python scripts/fetch_uvr_model.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dubber.core.audio.separate import UVR_FILES, download_uvr_model  # noqa: E402


def main() -> int:
    d = download_uvr_model()
    missing = [f for f in UVR_FILES if not (d / f).is_file()]
    if missing:
        print(f"missing in {d}: {missing}", file=sys.stderr)
        return 1
    print(d)
    return 0


if __name__ == "__main__":
    sys.exit(main())
