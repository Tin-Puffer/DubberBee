"""User-editable chatbot prompts: saved across launches, default + reset."""
from __future__ import annotations

import os

import pytest

from dubber.core.translate import prompts
from dubber.core.translate.tiktok import TIKTOK_PROMPT, tiktok_prompt

ROWS = [{"id": 0, "start": 0, "end": 1, "src_text": "你好", "vi_text": "Xin chào."}]


def test_defaults_until_saved_then_persist_and_reset():
    from dubber.core.utils.settings import load_settings
    for kind in ("review", "tiktok"):
        assert prompts.get_prompt(kind) == prompts.default_prompt(kind)
        assert not prompts.is_custom(kind)
        prompts.save_prompt(kind, "Phong cách của tôi")
        assert prompts.get_prompt(kind) == "Phong cách của tôi" and prompts.is_custom(kind)
        assert load_settings()[prompts.PROMPTS[kind][0]] == "Phong cách của tôi"   # on disk
        prompts.reset_prompt(kind)
        assert prompts.get_prompt(kind) == prompts.default_prompt(kind)
        assert load_settings()[prompts.PROMPTS[kind][0]] is None


def test_saving_the_default_or_blank_stores_nothing():
    from dubber.core.utils.settings import load_settings
    prompts.save_prompt("tiktok", TIKTOK_PROMPT + "\n\n")
    prompts.save_prompt("review", "   ")
    st = load_settings()
    assert st.get("tiktok_prompt") is None and st.get("review_prompt") is None


def test_tiktok_uses_the_saved_prompt_with_or_without_placeholder():
    prompts.save_prompt("tiktok", "Viết caption vui.\nLỜI THOẠI:\n<<< DÁN LỜI THOẠI VÀO ĐÂY >>>\nHết.")
    assert tiktok_prompt(ROWS) == "Viết caption vui.\nLỜI THOẠI:\n[1] Xin chào.\nHết."
    prompts.save_prompt("tiktok", "Chỉ 3 hashtag thôi.")          # placeholder removed
    assert tiktok_prompt(ROWS) == "Chỉ 3 hashtag thôi.\n\nLỜI THOẠI:\n[1] Xin chào.\n"


# ── GUI ───────────────────────────────────────────────────────────────────────

@pytest.fixture
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_prompt_dialog_save_and_default(qapp):
    from dubber.app.prompt_dialog import PromptDialog
    dlg = PromptDialog("tiktok")
    assert dlg.edit.toPlainText() == TIKTOK_PROMPT and not dlg.btn_default.isEnabled()
    dlg.edit.setPlainText("Prompt riêng")
    assert dlg.btn_default.isEnabled() and "tùy chỉnh" in dlg._state.text()
    dlg._save()
    assert prompts.get_prompt("tiktok") == "Prompt riêng"
    dlg2 = PromptDialog("tiktok")                                   # reopened: still there
    assert dlg2.edit.toPlainText() == "Prompt riêng"


def test_settings_rows_show_state_and_reset(qapp, monkeypatch):
    from dubber.app import settings_dialog as sd
    dlg = sd.SettingsDialog()
    row = dlg.prompt_rows["review"]
    assert row._state.text() == "mặc định" and not row.btn_reset.isEnabled()
    prompts.save_prompt("review", "Kiểu của tôi")
    row.refresh()
    assert row._state.text() == "đã tùy chỉnh" and row.btn_reset.isEnabled()
    monkeypatch.setattr(sd.QMessageBox, "question", lambda *a, **k: sd.QMessageBox.Yes)
    row._reset()
    assert prompts.get_prompt("review") == prompts.default_prompt("review")
    assert row._state.text() == "mặc định"


def test_review_dialog_copies_with_the_saved_prompt(qapp):
    from PySide6.QtGui import QGuiApplication
    from dubber.app.review_dialog import ReviewDialog
    prompts.save_prompt("review", "Dịch kiểu kiếm hiệp.")
    dlg = ReviewDialog("v.mp4", [dict(ROWS[0])])
    dlg._copy([1])
    assert QGuiApplication.clipboard().text().startswith("Dịch kiểu kiếm hiệp.\n\n1. [1,0s] 你好")


def test_translation_prompts_soften_words_tiktok_restricts():
    from dubber.core.translate.prompts import default_prompt
    for kind in ("review", "review_timed", "manual"):
        p = default_prompt(kind)
        assert "TikTok hạn chế" in p and "giết → ra tay" in p and "TTS không đọc được" in p, kind
