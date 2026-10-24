"""Manual translation with a chatbot: prompt text, reply parsing, pipeline, dialog."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dubber.core.translate.manual import (
    clock, manual_text, parse_clock, parse_manual_reply, to_translate_rows,
)

ROWS = [{"id": 0, "start": 0.02, "end": 3.62, "src_text": "我24小时都在记彩票号码，"},
        {"id": 1, "start": 3.62, "end": 7.10, "src_text": "因为我左眼突然能看到一年后。"},
        {"id": 2, "start": 9.00, "end": 12.50, "src_text": "那天晚上，门开了。"}]


def test_clock_roundtrip():
    for t in (0.0, 7.48, 69.9, 3725.5):
        assert parse_clock(clock(t)) == pytest.approx(t, abs=0.01)
    assert clock(67.48) == "01:07.48" and parse_clock("7,5") == 7.5


def test_prompt_has_instructions_length_and_timed_chinese_lines():
    text = manual_text(ROWS, 15.0, instructions="HƯỚNG DẪN")
    assert text.startswith("HƯỚNG DẪN")
    assert "Độ dài video: 00:15.00" in text
    assert "[2] 00:03.62 → 00:07.10 | 因为我左眼突然能看到一年后。" in text


def test_default_prompt_allows_merging_and_own_timing():
    from dubber.core.translate.prompts import default_prompt
    p = default_prompt("manual")
    assert "GỘP" in p and "TỰ ĐẶT THỜI GIAN" in p and "[00:07.48 → 00:11.20]" in p


def test_reply_with_merged_lines_and_new_times():
    reply = ("```text\n"
             "[00:00.02 → 00:06.90] Tôi ghi số xổ số suốt ngày đêm, vì mắt trái thấy được tương lai.\n"
             "[00:09.20 → 00:12.30] Đêm đó, cánh cửa mở ra.\n```")
    segs, warns = parse_manual_reply(reply, 15.0)
    assert [(s["start"], s["end"]) for s in segs] == [(0.02, 6.9), (9.2, 12.3)]
    assert segs[1]["vi_text"] == "Đêm đó, cánh cửa mở ra." and warns == []


@pytest.mark.parametrize("line", [
    "[00:01.00 → 00:02.50] Câu.",
    "00:01.00 --> 00:02.50 | Câu.",
    "- [00:01,00 – 00:02,50]: Câu.",
    "[3] 00:01.00 -> 00:02.50 Câu.",
    "**[00:01.00 → 00:02.50]** Câu.",
    "1.0 - 2.5 | Câu.",
])
def test_reply_line_spellings(line):
    (s,), _ = parse_manual_reply(line, 10.0)
    assert (s["start"], s["end"], s["vi_text"]) == (1.0, 2.5, "Câu.")


def test_reply_problems_are_fixed_and_reported():
    reply = ("[00:05.00 → 00:08.00] Câu hai.\n"
             "[00:01.00 → 00:06.00] Câu một, chồng giờ.\n"
             "[00:09.00 → 00:08.50] Kết thúc trước bắt đầu.\n"
             "[00:13.00 → 00:20.00] Vượt quá video.\n"
             "Lời bình của AI không có giờ")
    segs, warns = parse_manual_reply(reply, 15.0)
    assert [s["vi_text"][:4] for s in segs] == ["Câu ", "Câu ", "Kết ", "Vượt"]   # time order
    for a, b in zip(segs, segs[1:]):
        assert a["end"] <= b["start"] + 1e-9 and a["end"] > a["start"]
    assert segs[-1]["end"] == 15.0
    assert len(warns) == 4


def test_reply_without_timed_lines_is_rejected():
    with pytest.raises(ValueError):
        parse_manual_reply("[1] Câu không có giờ\n[2] Câu khác", 10.0)


def test_rows_keep_the_chinese_they_cover():
    segs = [{"start": 0.0, "end": 7.2, "vi_text": "Gộp hai dòng."},
            {"start": 9.0, "end": 12.5, "vi_text": "Dòng ba."}]
    rows = to_translate_rows(segs, ROWS)
    assert [r["id"] for r in rows] == [0, 1]
    assert rows[0]["src_text"] == ROWS[0]["src_text"] + ROWS[1]["src_text"]
    assert rows[1]["src_text"] == ROWS[2]["src_text"]


# ── pipeline ──────────────────────────────────────────────────────────────────

def _pipeline(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from dubber.core.pipeline import Pipeline
    job = tmp_path / "job"
    job.mkdir()
    asr = job / "asr.json"
    asr.write_text(json.dumps([{"start": r["start"], "end": r["end"], "src_text": r["src_text"]}
                               for r in ROWS], ensure_ascii=False), encoding="utf-8")
    p = Pipeline(Path("ffmpeg"), Path("ffprobe"), job)
    monkeypatch.setattr(p, "run_m1", lambda *a, **k: asr)
    monkeypatch.setattr(p._extractor, "probe", lambda path: SimpleNamespace(duration=15.0))
    return p


def test_pipeline_follows_the_chatbot_lines(tmp_path, monkeypatch):
    p = _pipeline(tmp_path, monkeypatch)
    calls = []

    def manual(rows, duration):
        calls.append((len(rows), duration))
        return [{"start": 0.02, "end": 6.9, "vi_text": "tôi ghi số xổ số , vì mắt trái thấy tương lai"},
                {"start": 9.2, "end": 12.3, "vi_text": "Đêm đó, cánh cửa mở ra."}]
    p.manual_translation = manual
    _, tj = p.run_m2(tmp_path / "v.mp4", translator="ai_manual")
    data = json.loads(tj.read_text(encoding="utf-8"))
    assert calls == [(3, 15.0)]
    assert [(d["start"], d["end"]) for d in data] == [(0.02, 6.9), (9.2, 12.3)]
    assert data[0]["vi_text"].startswith("Tôi ghi số xổ số, vì")      # cleaned up
    _, tj2 = p.run_m2(tmp_path / "v.mp4", translator="ai_manual")       # rerun: cached
    assert calls == [(3, 15.0)] and json.loads(tj2.read_text(encoding="utf-8")) == data


def test_pipeline_manual_needs_the_gui(tmp_path, monkeypatch):
    p = _pipeline(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError):
        p.run_m2(tmp_path / "v.mp4", translator="ai_manual")


def test_manual_mode_never_calls_the_groq_shortener(tmp_path, monkeypatch):
    p = _pipeline(tmp_path, monkeypatch)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    assert p._shortener("ai_manual") is None


# ── dialog ────────────────────────────────────────────────────────────────────

def test_dialog_copies_prompt_and_reads_the_reply(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from dubber.app.manual_dialog import ManualTranslateDialog
    dlg = ManualTranslateDialog("v.mp4", ROWS, 15.0)
    assert dlg._zh.rowCount() == 3 and not dlg._btn_ok.isEnabled()
    dlg._copy()
    assert "[3] 00:09.00 → 00:12.50 | 那天晚上，门开了。" in QGuiApplication.clipboard().text()
    pane = dlg._pane
    pane.edit.setPlainText("Không có giờ")
    pane.parse()
    assert not dlg._btn_ok.isEnabled() and "⚠" in pane.info.text()
    pane.edit.setPlainText("[00:00.02 → 00:06.90] Một câu gộp.\n"
                           "[00:09.20 → 00:10.00] Câu này có rất nhiều chữ đọc không kịp đâu nha.")
    pane.parse()
    assert dlg._btn_ok.isEnabled() and len(dlg.segments) == 2 and pane.table.rowCount() == 2
    assert "1 câu dày chữ" in pane.info.text()
    dlg.reject()


def test_pipeline_follows_a_split_into_more_lines(tmp_path, monkeypatch):
    """3 Chinese lines → 5 Vietnamese lines: the long first line is split in three."""
    p = _pipeline(tmp_path, monkeypatch)
    p.manual_translation = lambda rows, duration: parse_manual_reply(
        "[00:00.02 → 00:01.20] Suốt một ngày đêm,\n"
        "[00:01.30 → 00:02.40] tôi chỉ ghi số xổ số.\n"
        "[00:02.50 → 00:03.60] Không ai hiểu vì sao.\n"
        "[00:03.62 → 00:07.10] Vì mắt trái tôi thấy được tương lai.\n"
        "[00:09.00 → 00:12.50] Đêm đó, cánh cửa mở ra.", duration)[0]
    _, tj = p.run_m2(tmp_path / "v.mp4", translator="ai_manual")
    data = json.loads(tj.read_text(encoding="utf-8"))
    assert len(data) == 5 and [d["id"] for d in data] == list(range(5))
    assert [d["start"] for d in data] == [0.02, 1.3, 2.5, 3.62, 9.0]
    assert data[1]["src_text"] == ROWS[0]["src_text"]   # the middle part keeps its Chinese
