"""Groq key saved from the GUI: OS credential store (mocked), env handling, dialog."""
from __future__ import annotations

import os

import pytest

from dubber.core.utils import api_keys


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setitem(api_keys._shell_values, "GROQ_API_KEY", None)


def test_save_apply_delete(_memory_keyring):
    assert api_keys.source() == ""
    assert api_keys.save("  gsk_abcdefghijklmnopWXYZ  ")
    assert _memory_keyring.store[("Dubber", "GROQ_API_KEY")] == "gsk_abcdefghijklmnopWXYZ"
    assert os.environ["GROQ_API_KEY"] == "gsk_abcdefghijklmnopWXYZ"    # used right away
    os.environ.pop("GROQ_API_KEY")
    api_keys.apply_saved_keys()                                        # next start
    assert os.environ["GROQ_API_KEY"] == "gsk_abcdefghijklmnopWXYZ"
    assert api_keys.source() == "app"
    api_keys.delete()
    assert "GROQ_API_KEY" not in os.environ and api_keys.source() == ""


def test_saved_key_wins_over_shell_and_shell_comes_back(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_from_shell_000000")
    monkeypatch.setitem(api_keys._shell_values, "GROQ_API_KEY", "gsk_from_shell_000000")
    assert api_keys.source() == "env"
    api_keys.save("gsk_saved_in_app_1111")
    api_keys.apply_saved_keys()
    assert os.environ["GROQ_API_KEY"] == "gsk_saved_in_app_1111"
    api_keys.delete()
    assert os.environ["GROQ_API_KEY"] == "gsk_from_shell_000000"


def test_store_failure_still_works_for_this_session(monkeypatch):
    import keyring

    def boom(*a, **k):
        raise RuntimeError("keychain locked")
    monkeypatch.setattr(keyring, "set_password", boom)
    assert api_keys.save("gsk_session_only_2222") is False
    assert os.environ["GROQ_API_KEY"] == "gsk_session_only_2222"


def test_mask_never_shows_the_key():
    assert api_keys.mask("gsk_abcdefghijklmnopWXYZ") == "gsk_…WXYZ"
    assert api_keys.mask("short") == "…"
    assert api_keys.mask(None) == ""


def test_check_groq_messages(monkeypatch):
    import groq

    class Unauthorized(Exception):
        status_code = 401

    class FakeClient:
        def __init__(self, **kw):
            self.models = self
            self.key = kw["api_key"]

        def list(self):
            if self.key != "gsk_good":
                raise Unauthorized(f"bad key {self.key}")   # must not be echoed

    monkeypatch.setattr(groq, "Groq", FakeClient)
    api_keys.check_groq("gsk_good")
    with pytest.raises(ValueError, match="từ chối") as e:
        api_keys.check_groq("gsk_wrong_secret")
    assert "gsk_wrong_secret" not in str(e.value)


# ── GUI ───────────────────────────────────────────────────────────────────────

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_dialog_save_and_delete(monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from dubber.app import api_key_dialog as d
    dlg = d.GroqKeyDialog()
    assert "Chưa có Groq API key" in dlg._status.text() and not dlg.btn_save.isEnabled()
    dlg.edit.setText("gsk_abcdefghijklmnopWXYZ")
    assert dlg.btn_save.isEnabled() and dlg.edit.echoMode() == d.QLineEdit.Password
    dlg._save()
    assert "gsk_…WXYZ" in dlg._status.text() and dlg.edit.text() == ""
    assert "abcdefghijklmnop" not in dlg._status.text()
    monkeypatch.setattr(d.QMessageBox, "question", lambda *a, **k: d.QMessageBox.Yes)
    dlg._delete()
    assert "Chưa có Groq API key" in dlg._status.text()


def test_options_panel_shows_key_state(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from dubber.app import gui
    win = gui.MainWindow()
    row = win._settings_dlg.groq                                  # in ⚙ Cài đặt now
    assert "Chưa có" in row._lbl_groq.text()
    api_keys.save("gsk_abcdefghijklmnopWXYZ")
    row._refresh_groq_key()
    assert row._lbl_groq.text() == "✓ Đã lưu (gsk_…WXYZ)"
    win.close()


def test_ping_groq_real_request_and_messages(monkeypatch):
    import groq
    calls = []

    class Err(Exception):
        def __init__(self, status):
            super().__init__(f"error for key gsk_secret_value {status}")
            self.status_code = status

    class FakeClient:
        def __init__(self, **kw):
            self.chat = self.completions = self
            self.key = kw["api_key"]

        def create(self, model, messages, max_tokens):
            calls.append((model, max_tokens))
            if self.key == "gsk_good":
                return object()
            if self.key == "gsk_fallback" and model == "openai/gpt-oss-20b":
                return object()
            raise Err({"gsk_bad": 401, "gsk_limit": 429, "gsk_fallback": 404,
                       "gsk_down": 500}[self.key])

    monkeypatch.setattr(groq, "Groq", FakeClient)
    assert api_keys.ping_groq("gsk_good") == "qwen/qwen3.8-27b"
    assert calls == [("qwen/qwen3.8-27b", 1)]                     # one tiny request
    assert api_keys.ping_groq("gsk_fallback") == "openai/gpt-oss-20b"
    for key, msg in (("gsk_bad", "từ chối"), ("gsk_limit", "hạn mức"), ("gsk_down", "lỗi 500")):
        with pytest.raises(ValueError, match=msg) as e:
            api_keys.ping_groq(key)
        assert "gsk_secret_value" not in str(e.value)


def test_options_panel_check_button_lamp(monkeypatch):
    pytest.importorskip("PySide6")
    import time
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from dubber.app import gui
    win = gui.MainWindow()
    opts = win._settings_dlg.groq

    def wait():
        end = time.time() + 5
        while opts._groq_runner.busy() and time.time() < end:
            app.processEvents(); time.sleep(0.01)

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    opts._check_groq_key()                                          # no key → red at once
    assert gui._RED in opts._lamp_groq.styleSheet()

    api_keys.save("gsk_abcdefghijklmnopWXYZ")
    monkeypatch.setattr(api_keys, "ping_groq", lambda k: "qwen/qwen3.8-27b")
    opts._check_groq_key(); wait()
    assert gui._GREEN in opts._lamp_groq.styleSheet()
    assert "Key hoạt động" in opts._lamp_groq.toolTip() and opts._btn_check_groq.isEnabled()
    assert gui._GREEN in win._btn_groq_chip.styleSheet()            # header chip follows

    def fail(k):
        raise ValueError("Groq từ chối khóa này (sai hoặc đã bị thu hồi)")
    monkeypatch.setattr(api_keys, "ping_groq", fail)
    opts._check_groq_key(); wait()
    assert gui._RED in opts._lamp_groq.styleSheet()
    assert "từ chối" in opts._lbl_groq.text() and "abcdefghijklmnop" not in opts._lbl_groq.text()
    win.close()
