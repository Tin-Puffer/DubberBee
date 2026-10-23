"""Shared pytest fixtures."""
from __future__ import annotations

import sys
from pathlib import Path
import pytest


@pytest.fixture
def fake_ffmpeg(tmp_path) -> Path:
    """A non-functional but existing ffmpeg binary path."""
    name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    p = tmp_path / name
    p.touch()
    return p


@pytest.fixture
def fake_ffprobe(tmp_path) -> Path:
    name = "ffprobe.exe" if sys.platform == "win32" else "ffprobe"
    p = tmp_path / name
    p.touch()
    return p


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    """Never read or write the real settings.json (custom workspace / logs folders…)."""
    import dubber.core.utils.settings as settings
    monkeypatch.setattr(settings, "settings_path", lambda: tmp_path / "settings.json")


@pytest.fixture(autouse=True)
def _memory_keyring():
    """Never touch the real Keychain / Credential Manager: an in-memory store instead."""
    try:
        import keyring
        from keyring.backend import KeyringBackend
    except ImportError:
        yield None
        return

    class MemoryKeyring(KeyringBackend):
        priority = 1

        def __init__(self):
            super().__init__()
            self.store = {}

        def get_password(self, service, username):
            return self.store.get((service, username))

        def set_password(self, service, username, password):
            self.store[(service, username)] = password

        def delete_password(self, service, username):
            self.store.pop((service, username), None)

    previous = keyring.get_keyring()
    mem = MemoryKeyring()
    keyring.set_keyring(mem)
    yield mem
    keyring.set_keyring(previous)


@pytest.fixture(autouse=True)
def _collect_qt_garbage():
    """Free the windows / dialogs a test left behind right after it, while no worker
    thread runs: a cyclic-GC pass that lands mid-test (in the next test, during a
    background task) can crash PySide6 (Shiboken cppPointer on a deleted object)."""
    yield
    import gc
    import sys
    if "PySide6.QtWidgets" in sys.modules:
        from PySide6.QtWidgets import QApplication
        if QApplication.instance() is not None:
            QApplication.processEvents()
        gc.collect()
