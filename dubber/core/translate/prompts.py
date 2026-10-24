"""The chatbot prompts the user can restyle (⚙ Cài đặt → Chatbot), saved in settings.json.

Only a prompt that differs from its default is stored, so users who never edited one get
improved defaults automatically.
"""
from __future__ import annotations

from typing import Dict, Tuple

from dubber.core.translate.manual import MANUAL_PROMPT
from dubber.core.translate.review import DEFAULT_INSTRUCTIONS
from dubber.core.translate.tiktok import SCRIPT_PLACEHOLDER, TIKTOK_PROMPT

# kind → (settings key, default text)
PROMPTS: Dict[str, Tuple[str, str]] = {
    "review": ("review_prompt", DEFAULT_INSTRUCTIONS),
    "manual": ("manual_prompt", MANUAL_PROMPT),
    # Review dialog, "Copy chỉ tiếng Trung": the same job as manual mode, its own prompt
    "review_timed": ("review_timed_prompt", MANUAL_PROMPT),
    "tiktok": ("tiktok_prompt", TIKTOK_PROMPT),
}


def default_prompt(kind: str) -> str:
    return PROMPTS[kind][1]


def get_prompt(kind: str) -> str:
    """The user's version if they saved one, else the default."""
    from dubber.core.utils.settings import load_settings
    key, default = PROMPTS[kind]
    saved = load_settings().get(key)
    return saved if isinstance(saved, str) and saved.strip() else default


def is_custom(kind: str) -> bool:
    return get_prompt(kind).strip() != default_prompt(kind).strip()


def save_prompt(kind: str, text: "str | None") -> None:
    """Store *text*; blank or equal to the default → back to the default."""
    from dubber.core.utils.settings import load_settings, save_settings
    key, default = PROMPTS[kind]
    st = load_settings()
    st[key] = None if not text or not text.strip() or text.strip() == default.strip() else text
    save_settings(st)


def reset_prompt(kind: str) -> None:
    save_prompt(kind, None)


__all__ = ["PROMPTS", "SCRIPT_PLACEHOLDER", "default_prompt", "get_prompt", "is_custom",
           "save_prompt", "reset_prompt"]
