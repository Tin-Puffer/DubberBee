"""Queue cards, progress / time left, notifications, settings dialog, preview, saved state."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

import dubber.app.gui as gui  # noqa: E402
from dubber.app.progress import STEP_WEIGHTS, RunProgress, fmt_clock, fmt_left  # noqa: E402
from dubber.app.queue_card import config_summary, voice_name  # noqa: E402


# ── progress ──────────────────────────────────────────────────────────────────

class _Clock:
    def __init__(self): self.t = 0.0
    def __call__(self): return self.t


def test_progress_fraction_skips_and_eta():
    c = _Clock()
    p = RunProgress(clock=c)
    assert p.eta() is None
    w = STEP_WEIGHTS
    p.started("ASR")                      # separation never reported: counted as done
    assert p.fraction() == pytest.approx(w["Separate"])
    c.t = 60
    p.done("ASR")
    done = w["Separate"] + w["ASR"]
    assert p.fraction() == pytest.approx(done)
    assert p.eta() == pytest.approx(60 / done - 60)
    assert p.step_elapsed("ASR") == pytest.approx(60)
    p.started("TTS"); p.progress("TTS", 50)
    assert p.fraction() == pytest.approx(done + w["Translate"] + w["TTS"] / 2)
    assert sum(w.values()) == pytest.approx(1.0) and "Mux" not in w


def test_progress_pause_excludes_waiting_on_user():
    c = _Clock()
    p = RunProgress(clock=c)
    c.t = 30; p.pause()
    c.t = 600                              # user took 9.5 minutes to review
    assert p.elapsed() == pytest.approx(30)
    p.resume(); c.t = 630
    assert p.elapsed() == pytest.approx(60)


def test_time_formats():
    assert fmt_clock(5) == "0:05" and fmt_clock(247) == "4:07" and fmt_clock(3909) == "1:05:09"
    assert fmt_left(20) == "dưới 1 phút" and fmt_left(41 * 60) == "~41 phút"
    assert fmt_left(3 * 3600 + 600) == "~3 giờ 10 phút" and fmt_left(7200) == "~2 giờ"


def test_config_summary():
    assert voice_name("piper", "vi_VN-thanh_nien_tu_tin-medium") == "thanh nien tu tin"
    assert voice_name("vieneu", "preset:Thiện Minh") == "Thiện Minh"
    s = config_summary({"translator": "groq", "tts_engine": "vieneu",
                        "voice": "preset:Thiện Minh", "skip_review": False})
    assert s == "Groq · VieNeu Thiện Minh · duyệt bản dịch"


# ── main window ───────────────────────────────────────────────────────────────

@pytest.fixture
def win(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    import dubber.app.notify as notify
    monkeypatch.setattr(notify, "notify", lambda *a, **k: None)
    w = gui.MainWindow()
    yield w
    w.close()


def _videos(tmp_path, *names):
    out = []
    for n in names:
        p = tmp_path / n
        p.write_bytes(b"v")
        out.append(p)
    return out


def _card(win, row):
    return win._list.itemWidget(win._list.item(row))


def test_cards_show_state_and_actions(win, tmp_path):
    a, b = _videos(tmp_path, "a.mp4", "b.mp4")
    win._add_files([a, b])
    assert win._drop.maximumHeight() == 40                       # compact once videos exist
    card = _card(win, 0)
    assert card.button("remove") is not None and card.button("play") is None
    assert "Chờ chạy" in card._state.text()
    assert "2 video" in win._summary.text() and "2 chờ chạy" in win._summary.text()

    out = tmp_path / "a_vi.mp4"; out.write_bytes(b"x" * 2048)
    (tmp_path / "a_vi.txt").write_text("PROMPT + lời thoại", encoding="utf-8")
    job = win._jobs[0]
    job.status, job.output = gui._Job.DONE, out
    win._update_item(0)
    card = _card(win, 0)
    assert card.button("play") and card.button("tiktok") and card.button("rerun")
    assert "Xong" in card._state.text() and "2 KB" in card._state.text()
    card.button("tiktok").click()
    assert QApplication.clipboard().text() == "PROMPT + lời thoại"

    card.button("rerun").click()
    assert win._jobs[0].status == gui._Job.PENDING and win._jobs[0].output is None


def test_waiting_card_and_error_card(win, tmp_path):
    (a,) = _videos(tmp_path, "a.mp4")
    win._add_files([a])
    job = win._jobs[0]
    job.status, job.waiting = gui._Job.RUNNING, "review"
    win._update_item(0)
    assert "duyệt bản dịch" in _card(win, 0)._state.text() and "chờ bạn" in win._summary.text()
    job.status, job.waiting, job.error = gui._Job.ERROR, "", "FFmpeg hỏng"
    win._update_item(0)
    card = _card(win, 0)
    assert "FFmpeg hỏng" in card._state.text() and card.button("show_error") is not None


def test_multi_select_remove_and_duplicate(win, tmp_path):
    a, b, c = _videos(tmp_path, "a.mp4", "b.mp4", "c.mp4")
    win._add_files([a, b, c])
    win._card_action(win._jobs[0], "duplicate")
    assert [j.path.name for j in win._jobs] == ["a.mp4", "a.mp4", "b.mp4", "c.mp4"]
    assert win._jobs[1].opts == win._jobs[0].opts and win._jobs[1].opts is not win._jobs[0].opts
    win._list.clearSelection()
    win._list.item(1).setSelected(True)
    win._list.item(3).setSelected(True)
    win._remove_selected()
    assert [j.path.name for j in win._jobs] == ["a.mp4", "b.mp4"]
    assert win._list.count() == 2 and all(_card(win, r) for r in range(2))


def test_drag_reorders_queue(win, tmp_path):
    from PySide6.QtCore import QModelIndex
    a, b, c = _videos(tmp_path, "a.mp4", "b.mp4", "c.mp4")
    win._add_files([a, b, c])
    assert win._list.model().moveRow(QModelIndex(), 2, QModelIndex(), 0)
    QApplication.processEvents()
    assert [j.path.name for j in win._jobs] == ["c.mp4", "a.mp4", "b.mp4"]
    assert [win._list.item(r).data(gui.Qt.UserRole) for r in range(3)] == \
        [id(j) for j in win._jobs]
    assert all(_card(win, r) is not None for r in range(3))


def test_running_card_shows_progress_and_time_left(win, tmp_path):
    (a,) = _videos(tmp_path, "a.mp4")
    win._add_files([a])
    clock = _Clock()
    win._progress = RunProgress(clock=clock)
    win._running = 0
    win._jobs[0].status = gui._Job.RUNNING
    win._on_started("ASR")
    clock.t = 120
    win._on_done("ASR")
    text = _card(win, 0)._state.text()
    assert "tổng 45%" in text and "đã chạy 2:00" in text and "còn ~2 phút" in text
    assert "45%" in win._prog_box.title() and win._rows["ASR"]._time.text() == "2:00"
    win._running = -1
    win._jobs[0].status = gui._Job.PENDING


def test_queue_window_and_tab_remembered(tmp_path):
    QApplication.instance() or QApplication([])
    a, b = _videos(tmp_path, "a.mp4", "b.mp4")
    w = gui.MainWindow()
    w._add_files([a, b])
    out = tmp_path / "b_vi.mp4"; out.write_bytes(b"x")
    w._jobs[1].status, w._jobs[1].output = gui._Job.DONE, out
    w._opts._tabs.setCurrentIndex(2)
    w.close()
    b.unlink()                                                   # gone since → not restored
    w2 = gui.MainWindow()
    assert [j.path.name for j in w2._jobs] == ["a.mp4"]
    assert w2._jobs[0].status == gui._Job.PENDING and w2._opts._tabs.currentIndex() == 2
    w2.close()


def test_settings_dialog_notify_toggle(win):
    from dubber.app import notify
    dlg = win._settings_dlg
    assert notify.enabled()
    dlg._chk_notify.setChecked(False)
    assert not notify.enabled()


def test_notify_only_when_window_inactive(monkeypatch):
    import sys
    import dubber.app.notify as notify
    calls = []
    monkeypatch.setattr(notify.subprocess, "Popen", lambda cmd, **k: calls.append(cmd))
    monkeypatch.setattr(sys, "platform", "darwin")

    class Win:
        active = True
        def isActiveWindow(self): return self.active
    QApplication.instance() or QApplication([])
    w = Win()
    notify.notify(None, "T", "x")
    assert calls and calls[-1][:2] == ["osascript", "-e"] and calls[-1][-2:] == ["T", "x"]
    calls.clear()
    notify.notify(w, "T", "x")                                   # user is looking: no ping
    assert calls == []


def test_preview_paints_subtitle_on_a_frame(tmp_path):
    QApplication.instance() or QApplication([])
    from PySide6.QtGui import QColor, QImage
    from dubber.app.preview import paint_preview
    img = QImage(640, 360, QImage.Format_RGB32); img.fill(QColor("#203040"))
    frame = tmp_path / "f.jpg"; img.save(str(frame))
    layout = {"blur_mode": "off", "sub_font": "Arial", "sub_size": 48, "sub_color": "#FFFF00",
              "sub_outline_color": "#000000", "sub_outline": 2.0, "sub_box": False}
    pix = paint_preview(frame, 1920, 1080, layout, show_subs=True)
    assert (pix.width(), pix.height()) == (320, 180)
    out = pix.toImage()

    def yellow(rows):
        return sum(1 for x in range(320) for y in rows
                   if QColor(out.pixel(x, y)).red() > 200 and QColor(out.pixel(x, y)).blue() < 90)
    assert yellow(range(150, 180)) > 0 and yellow(range(0, 140)) == 0   # true-to-scale, at the bottom
    plain = paint_preview(frame, 1920, 1080, layout, show_subs=False).toImage()
    assert all(QColor(plain.pixel(x, 170)).red() < 100 for x in range(0, 320, 8))


def test_duplicate_selects_only_the_copy(win, tmp_path):
    (a,) = _videos(tmp_path, "a.mp4")
    win._add_files([a])
    win._card_action(win._jobs[0], "duplicate")
    win._card_action(win._jobs[0], "duplicate")
    assert [win._list.row(i) for i in win._list.selectedItems()] == [1]


def test_context_menu_entries(win, tmp_path):
    a, b = _videos(tmp_path, "a.mp4", "b.mp4")
    win._add_files([a, b])

    def entries():
        menu = win._build_context_menu(0)
        return {act.text(): act.isEnabled() for act in menu.actions() if act.text()}
    seen = entries()
    assert seen["▶  Xem video kết quả"] is False                 # not done yet
    assert seen["⧉  Nhân bản (chạy với cấu hình khác)"] is True
    assert seen["✕  Xóa khỏi hàng đợi"] is True
    win._list.item(0).setSelected(True); win._list.item(1).setSelected(True)
    seen = entries()
    assert "✕  Xóa 2 video đã chọn" in seen
    assert seen["⚙  Áp cấu hình video này cho 1 video đã chọn"] is True


def test_mouse_wheel_never_changes_inputs(win):
    from PySide6.QtCore import QPoint, QPointF, Qt as _Qt
    from PySide6.QtGui import QWheelEvent

    def wheel(w):
        ev = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120),
                         _Qt.NoButton, _Qt.NoModifier, _Qt.NoScrollPhase, False)
        QApplication.sendEvent(w, ev)
    o = win._opts
    combo, spin, slider = o._combo_translator, o._spin_size, o._sld_music
    before = (combo.currentIndex(), spin.value(), slider.value())
    for w in (combo, spin, slider):
        wheel(w)
    assert (combo.currentIndex(), spin.value(), slider.value()) == before
