"""Persistent app settings (JSON in the user config dir) + output path resolution."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from platformdirs import user_config_dir

log = logging.getLogger(__name__)

DEFAULTS: dict = {
    "output_dir": None,  # None → save next to the source video
    "tts_engine": "piper",   # last reader + voice picked in the GUI
    "voice": None,
    "workspace_dir": None,   # None → user data dir (job caches)
    "logs_dir": None,        # None → user log dir (dubber.log, errors/)
}


def settings_path() -> Path:
    return Path(user_config_dir("dubber", "dubber")) / "settings.json"


def load_settings(path: Path | None = None) -> dict:
    path = path or settings_path()
    data = dict(DEFAULTS)
    try:
        data.update(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        pass
    except Exception as exc:
        log.warning("Không đọc được settings (%s) — dùng mặc định", exc)
    return data


def save_settings(data: dict, path: Path | None = None) -> None:
    path = path or settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_output_path(input_path: Path, output_dir: "Path | str | None") -> Path:
    """Return a non-existing `<output_dir>/<stem>_vi.mp4` (next to input if output_dir is None).

    Appends " (2)", " (3)"… instead of overwriting an existing file.
    """
    folder = Path(output_dir) if output_dir else input_path.parent
    candidate = folder / f"{input_path.stem}_vi.mp4"
    n = 2
    while candidate.exists():
        candidate = folder / f"{input_path.stem}_vi ({n}).mp4"
        n += 1
    return candidate
