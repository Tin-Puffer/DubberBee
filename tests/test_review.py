"""Translation review: warnings, chatbot copy / paste-back, pipeline hook, dialog."""
from __future__ import annotations

import json
import os

import pytest

from dubber.core.translate.review import (
    DEFAULT_INSTRUCTIONS, chatbot_text, flagged_numbers, parse_chatbot_reply, row_flags,
)

ROWS = [
    {"id": 0, "start": 12.0, "end": 14.0, "src_text": "你怎么来了？", "vi_text": "Sao anh lại đến đây?"},
    {"id": 1, "start": 14.0, "end": 15.0, "src_text": "我来找你。", "vi_text": ""},
    {"id": 2, "start": 17.0, "end": 18.0, "src_text": "都给我闭嘴！",
     "vi_text": "Tất cả các ngươi im miệng hết cho ta ngay bây giờ!"},
    {"id": 3, "start": 20.0, "end": 21.5, "src_text": "师父", "vi_text": "Sư phụ 师父"},
]


def test_row_flags():
    assert row_flags(ROWS[0]) == []
    assert row_flags(ROWS[1]) == ["trống"]
    assert row_flags(ROWS[2]) == ["dài ~2,4s/1,0s"]
    assert row_flags(ROWS[3]) == ["còn chữ Hán"]
    assert flagged_numbers(ROWS) == [2, 3, 4]


def test_chatbot_text_numbers_timing_and_warnings():
    text = chatbot_text(ROWS, [1, 3])
    assert text.startswith(DEFAULT_INSTRUCTIONS)
    lines = text.strip().split("\n")[-2:]
    assert lines[0] == "1. [2,0s] 你怎么来了？ → Sao anh lại đến đây?"
    assert lines[1].startswith("3. [1,0s] 都给我闭嘴！ → Tất cả") and "⚠ dài" in lines[1]
    assert chatbot_text(ROWS, instructions="").startswith("1. [2,0s]")


def test_parse_reply_tolerates_chatbot_noise():
    reply = """Đây là bản sửa:
```
1. Sao anh lại tới đây?
**3)** Im hết cho ta!
- [4] "Sư phụ."
2. [1,0s] 我来找你。 → Anh đến tìm em.  ⚠ trống
9. Câu thừa
```
Chúc bạn dịch tốt!"""
    changes, bad = parse_chatbot_reply(reply, 4)
    assert changes == {1: "Sao anh lại tới đây?", 3: "Im hết cho ta!", 4: "Sư phụ.",
                       2: "Anh đến tìm em."}
    assert bad == [9]


def test_parse_reply_without_numbers_fails():
    with pytest.raises(ValueError, match="số. câu tiếng Việt"):
        parse_chatbot_reply("Xin chào\nkhông có số", 4)


def test_copy_paste_round_trip():
    changes, bad = parse_chatbot_reply(chatbot_text(ROWS, instructions=""), len(ROWS))
    assert not bad
    assert changes == {1: "Sao anh lại đến đây?", 3: ROWS[2]["vi_text"], 4: "Sư phụ 师父"}


# ── pipeline hook ─────────────────────────────────────────────────────────────

@pytest.fixture
def env(tmp_path):
    from tests.test_job_cache import _Env
    job = tmp_path / "job"
    job.mkdir()
    (job / "audio.wav").write_bytes(b"RIFF fake audio")
    return _Env(job, tmp_path / "models")


def test_review_edits_are_saved_and_reused(env):
    seen = []

    def review(rows):
        seen.append([r["vi_text"] for r in rows])
        return ["xin chào", rows[1]["vi_text"]]
    _, tj = env.run("groq", review=review)
    assert seen == [["Groq-0.", "Groq-1."]]
    data = json.loads(tj.read_text(encoding="utf-8"))
    assert data[0]["vi_text"] == "Xin chào."         # normalized like any translation
    stored = env.job_dir / "translations" / "groq.json"
    assert json.loads(stored.read_text(encoding="utf-8"))[0]["vi_text"] == "Xin chào."

    seen.clear()
    _, tj = env.run("groq", review=lambda rows: seen.append(rows[0]["vi_text"]) or
                    [r["vi_text"] for r in rows])
    assert env.translate_calls == ["groq"]            # not translated again
    assert seen == ["Xin chào."]                      # review starts from the edited text


def test_review_done_ahead_is_skipped_once(env):
    """A queue prepare run reviews before the video's turn: its own run skips the review
    (same text), a later rerun asks again."""
    seen = []

    def review(tag):
        def hook(rows):
            seen.append(tag)
            return ["đã duyệt trước", rows[1]["vi_text"]]
        return hook
    env.run("groq", review=review("ahead"), prepare=True)
    mark = env.job_dir / "translations" / "groq.reviewed"
    assert seen == ["ahead"] and mark.exists()
    _, tj = env.run("groq", review=review("own run"))
    assert seen == ["ahead"] and not mark.exists()     # not asked twice
    assert json.loads(tj.read_text(encoding="utf-8"))[0]["vi_text"] == "Đã duyệt trước."
    env.run("groq", review=review("rerun"))
    assert seen == ["ahead", "rerun"]


def test_review_ahead_mark_ignored_when_the_text_changed(env):
    env.run("groq", review=lambda rows: [r["vi_text"] for r in rows], prepare=True)
    stored = env.job_dir / "translations" / "groq.json"
    stored.write_text(stored.read_text(encoding="utf-8").replace("Groq-0.", "Khác."),
                      encoding="utf-8")
    seen = []
    env.run("groq", review=lambda rows: seen.append(1) or [r["vi_text"] for r in rows])
    assert seen == [1]


def test_no_review_hook_runs_straight_through(env):
    _, tj = env.run("groq")
    assert json.loads(tj.read_text(encoding="utf-8"))[0]["vi_text"] == "Groq-0."


# ── dialog ────────────────────────────────────────────────────────────────────

@pytest.fixture
def qapp(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    import dubber.core.utils.settings as settings
    monkeypatch.setattr(settings, "settings_path", lambda: tmp_path / "settings.json")
    return QApplication.instance() or QApplication([])


def test_dialog_edit_paste_undo_continue(qapp, monkeypatch):
    from PySide6.QtGui import QGuiApplication
    import dubber.app.review_dialog as rd

    dlg = rd.ReviewDialog("v.mp4", [dict(r) for r in ROWS])
    assert "(3)" in dlg._chk_flagged.text()
    dlg._table.item(1, rd.COL_VI).setText("Anh  đến tìm em.")      # user edit
    assert dlg._vi[1] == "Anh đến tìm em." and "(2)" in dlg._chk_flagged.text()

    dlg._copy([1, 2])
    copied = QGuiApplication.clipboard().text()
    assert "2. [1,0s] 我来找你。 → Anh đến tìm em." in copied

    QGuiApplication.clipboard().setText("3. Im hết cho ta!\n4. Sư phụ.")

    class AutoPaste:
        def __init__(self, vi, text, parent=None):
            self.changes = dict(parse_chatbot_reply(text, len(vi))[0])
            self.timed_text = ""
        def exec(self): return rd.QDialog.Accepted
    monkeypatch.setattr(rd, "PasteReplyDialog", AutoPaste)
    dlg._paste()
    assert dlg._vi[2:] == ["Im hết cho ta!", "Sư phụ."]
    assert dlg._table.item(2, rd.COL_VI).text() == "Im hết cho ta!"

    dlg._undo_last()                                   # undo the paste
    assert dlg._vi[2] == ROWS[2]["vi_text"] and dlg._vi[1] == "Anh đến tìm em."
    dlg._continue()
    assert dlg.result_vi == ["Sao anh lại đến đây?", "Anh đến tìm em.",
                             ROWS[2]["vi_text"], "Sư phụ 师父"]


def test_paste_dialog_preview_skips_unchanged_and_unticked(qapp):
    from PySide6.QtCore import Qt
    import dubber.app.review_dialog as rd
    vi = [r["vi_text"] for r in ROWS]
    dlg = rd.PasteReplyDialog(vi, "1. Sao anh lại đến đây?\n2. Mới hai\n4. Mới bốn\n7. x")
    assert dlg._table.rowCount() == 2                    # 1 is unchanged
    assert "7" in dlg._info.text()
    dlg._table.item(1, 0).setCheckState(Qt.Unchecked)    # untick sentence 4
    dlg._apply()
    assert dlg.changes == {2: "Mới hai"}


def test_filter_flagged_and_search(qapp):
    import dubber.app.review_dialog as rd
    dlg = rd.ReviewDialog("v.mp4", [dict(r) for r in ROWS])
    dlg._chk_flagged.setChecked(True)
    assert [dlg._table.isRowHidden(i) for i in range(4)] == [True, False, False, False]
    dlg._chk_flagged.setChecked(False)
    dlg._search.setText("sư phụ")
    assert [dlg._table.isRowHidden(i) for i in range(4)] == [True, True, True, False]


def test_default_prompt_reply_format_parses():
    reply = "```text\n[3] Im hết cho ta!\n[4] Sư phụ.\n```"
    assert parse_chatbot_reply(reply, 4) == ({3: "Im hết cho ta!", 4: "Sư phụ."}, [])
    assert parse_chatbot_reply("```text\n[0] KHÔNG SỬA\n```", 4) == ({}, [0])


def test_paste_ignores_our_own_copy_on_the_clipboard(qapp, monkeypatch):
    import dubber.app.review_dialog as rd
    seen = []

    class Spy:
        def __init__(self, vi, text, parent=None): seen.append(text); self.changes = {}
        def exec(self): return rd.QDialog.Rejected
    monkeypatch.setattr(rd, "PasteReplyDialog", Spy)
    dlg = rd.ReviewDialog("v.mp4", [dict(r) for r in ROWS])
    dlg._copy([1, 2, 3, 4])
    dlg._paste()
    assert seen == [""]



# ── merge / split replies (Tự động / Groq / MarianMT / NLLB) ──────────────────

def _review_dialog(qapp, monkeypatch):
    import dubber.app.review_dialog as rd
    rows = [{"id": i, "start": s, "end": e, "src_text": zh, "vi_text": vi}
            for i, (s, e, zh, vi) in enumerate([
                (0.0, 2.0, "我来了", "Tôi đến rồi."),
                (2.0, 4.0, "你是谁", "Anh là ai?"),
                (5.0, 8.0, "门开了，一个人走进来", "Cửa mở, một người bước vào.")])]
    return rd, rd.ReviewDialog("v.mp4", rows, duration=10.0)


def test_copy_chinese_only_has_times_and_its_own_prompt(qapp, monkeypatch):
    from PySide6.QtGui import QGuiApplication
    from dubber.core.translate.prompts import default_prompt
    rd, dlg = _review_dialog(qapp, monkeypatch)
    dlg._copy_timed()
    text = QGuiApplication.clipboard().text()
    assert text.startswith(default_prompt("review_timed").strip()[:40])
    assert "[3] 00:05.00 → 00:08.00 | 门开了，一个人走进来" in text
    assert "Tôi đến rồi" not in text                     # no Vietnamese in this copy
    dlg.reject()


def test_pasting_a_timed_reply_rebuilds_the_table_and_undo_restores_it(qapp, monkeypatch):
    rd, dlg = _review_dialog(qapp, monkeypatch)
    monkeypatch.setattr(rd.QGuiApplication.clipboard(), "text", lambda: (
        "[00:00.00 → 00:04.00] Tôi đến rồi, còn anh là ai?\n"
        "[00:05.00 → 00:06.40] Cửa mở.\n"
        "[00:06.50 → 00:08.00] Một người bước vào."))
    import dubber.app.manual_dialog as md

    class AutoApply:
        def __init__(self, duration, n, text, parent=None):
            assert duration == 10.0 and n == 3
            from dubber.core.translate.manual import parse_manual_reply
            self.segments = parse_manual_reply(text, duration)[0]
        def exec(self): return rd.QDialog.Accepted
    monkeypatch.setattr(md, "TimedReplyDialog", AutoApply)
    dlg._paste()
    assert dlg._table.rowCount() == 3 and dlg._vi[0] == "Tôi đến rồi, còn anh là ai?"
    assert [r["start"] for r in dlg._rows] == [0.0, 5.0, 6.5]
    assert dlg._rows[0]["src_text"] == "我来了你是谁"            # merged: both Chinese lines
    dlg._undo_last()
    assert dlg._vi == ["Tôi đến rồi.", "Anh là ai?", "Cửa mở, một người bước vào."]
    dlg._redo = None
    dlg.apply_segments([{"start": 0.0, "end": 4.0, "vi_text": "Gộp."},
                        {"start": 5.0, "end": 8.0, "vi_text": "Cửa mở."}])
    dlg._continue()
    assert dlg.result == {"rows": [
        {"id": 0, "start": 0.0, "end": 4.0, "src_text": "我来了你是谁", "vi_text": "Gộp."},
        {"id": 1, "start": 5.0, "end": 8.0, "src_text": "门开了，一个人走进来", "vi_text": "Cửa mở."}]}


def test_text_only_edits_still_return_a_list(qapp, monkeypatch):
    rd, dlg = _review_dialog(qapp, monkeypatch)
    dlg._continue()
    assert dlg.result == ["Tôi đến rồi.", "Anh là ai?", "Cửa mở, một người bước vào."]


def test_pipeline_review_can_restructure_the_lines(tmp_path):
    from pathlib import Path
    from dubber.core.pipeline import Pipeline
    from dubber.core.utils.job_cache import sig_matches, write_sig
    job = tmp_path / "job"
    (job / "translations").mkdir(parents=True)
    rows = [{"id": 0, "start": 0.0, "end": 2.0, "src_text": "我来了", "vi_text": "Tôi đến."},
            {"id": 1, "start": 2.0, "end": 4.0, "src_text": "你是谁", "vi_text": "Ai?"}]
    tj, stored = job / "translate.json", job / "translations" / "groq.json"
    tj.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    sig = {"asr": "x", "translator": "groq"}
    write_sig(tj, sig)
    p = Pipeline(Path("ffmpeg"), Path("ffprobe"), job)
    p.review_translation = lambda r: {"rows": [
        {"start": 0.0, "end": 4.0, "src_text": "我来了你是谁", "vi_text": "tôi đến , anh là ai"}]}
    p._review(tj, stored, sig)
    data = json.loads(tj.read_text(encoding="utf-8"))
    assert len(data) == 1 and data[0]["id"] == 0 and data[0]["end"] == 4.0
    assert data[0]["vi_text"].startswith("Tôi đến, anh là ai")
    assert sig_matches(stored, sig) and json.loads(stored.read_text(encoding="utf-8")) == data
