"""API keys saved from the GUI, kept in the OS credential store — never in a file.

macOS Keychain / Windows Credential Manager via ``keyring``. At start-up the saved
key is put into this process's environment (``os.environ``), which is where the
translators read it from; a key saved in the app wins over one set in the shell.
Keys are never logged; the UI only shows a masked form.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

SERVICE = "Dubber"
GROQ = "GROQ_API_KEY"

# What the shell gave us before a saved key replaced it — restored when it is deleted
_shell_values = {GROQ: os.environ.get(GROQ)}


def get_saved(name: str = GROQ) -> "str | None":
    """Key saved in the OS credential store, or None (also when the store is unavailable)."""
    try:
        import keyring
        return keyring.get_password(SERVICE, name) or None
    except Exception as exc:
        log.warning("Không đọc được kho mật khẩu hệ điều hành (%s)", type(exc).__name__)
        return None


def save(value: str, name: str = GROQ) -> bool:
    """Save + use now. Returns False if the credential store failed (then the key is
    only used for this session)."""
    value = value.strip()
    os.environ[name] = value
    try:
        import keyring
        keyring.set_password(SERVICE, name, value)
        return True
    except Exception as exc:
        log.warning("Không lưu được khóa vào kho mật khẩu (%s) — chỉ dùng cho phiên này",
                    type(exc).__name__)
        return False


def delete(name: str = GROQ) -> None:
    """Forget the saved key; fall back to the shell's variable, if there was one."""
    shell = _shell_values.get(name)
    if shell:
        os.environ[name] = shell
    else:
        os.environ.pop(name, None)
    try:
        import keyring
        keyring.delete_password(SERVICE, name)
    except Exception:
        pass


def apply_saved_keys() -> None:
    """Put saved keys into os.environ (call once at start-up)."""
    value = get_saved(GROQ)
    if value:
        os.environ[GROQ] = value


def current(name: str = GROQ) -> "str | None":
    return os.environ.get(name) or None


def source(name: str = GROQ) -> str:
    """"app" (saved in Dubber), "env" (shell variable only) or "" (none)."""
    if get_saved(name):
        return "app"
    return "env" if current(name) else ""


def mask(value: "str | None") -> str:
    """gsk_abcdef…wxyz → "gsk_…wxyz" (never show the whole key)."""
    if not value:
        return ""
    return f"{value[:4]}…{value[-4:]}" if len(value) > 12 else "…"


def check_groq(value: str) -> None:
    """Raise with a readable message if Groq rejects the key (one small API call)."""
    from groq import Groq
    try:
        Groq(api_key=value.strip(), max_retries=0, timeout=15).models.list()
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        if status in (401, 403):
            raise ValueError("Groq từ chối khóa này (sai hoặc đã bị thu hồi)") from None
        raise ValueError(f"Không kiểm tra được: {type(exc).__name__} — kiểm tra kết nối mạng") from None
