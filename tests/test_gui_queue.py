"""Tests for the GUI queue: per-video config and sequential processing (headless)."""
from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import dubber.app.gui as gui  # noqa: E402
import dubber.core.utils.settings as settings  # noqa: E402


class _FakeWorker(QObject):
    """Stands in for PipelineWorker: records its kwargs, 'fails' on names containing 'bad'."""
    step_started = Signal(str)
    step_progress = Signal(str, int)
    step_done = Signal(str)
    finished = Signal(Path)
    error = Signal(str)
    cancelled = Signal()
    error_report = Signal(str, str)
    ask = Signal(str, object)
    runs: list = []
    answers: list = []
    answered_ahead: set = set()
    release = None   # threading.Event a "render" video waits on

    def __init__(self, input_path, **kw):
        super().__init__()
        self.input_path, self.kw = input_path, kw
        self._cancel = False
        self._answered = False

    def cancel(self):
        self._cancel = True

    def answer(self, value):
        _FakeWorker.answers.append(value)
        self._answered = True

    def run(self):
        _FakeWorker.runs.append((self.input_path.name, self.kw))
        prepare = self.kw.get("prepare", False)
        if self.input_path.stem in ("ratelimit", "review", "manual") and not (
                not prepare and self.input_path.name in _FakeWorker.answered_ahead):
            kind = {"ratelimit": "rate_limit", "review": "review",
                    "manual": "manual"}[self.input_path.stem]
            self.ask.emit(kind, {"video": self.input_path.name, "script": Path("x_zh.txt"),
                                 "count": 3, "can_reuse": False, "error": None,
                                 "wait": 12, "done": 1, "total": 3, "again": False,
                                 "can_local": True, "rows": [], "audio": None,
                                 "work_dir": None, "tts_engine": "piper", "voice": None,
                                 "duration": 10.0})
            end = time.time() + 10
            while not (self._answered or self._cancel) and time.time() < end:
                time.sleep(0.01)
            if self._cancel:
                self.cancelled.emit()
                return
            if prepare:
                _FakeWorker.answered_ahead.add(self.input_path.name)
        if prepare:                          # asked ahead; its own run comes later
            self.finished.emit(self.input_path)
            return
        if "render" in self.input_path.name:  # translated, then renders until released
            self.step_started.emit("TTS")
            _FakeWorker.release.wait(10)
        if "slow" in self.input_path.name:   # runs until cancelled
            end = time.time() + 10
            while not self._cancel and time.time() < end:
                time.sleep(0.01)
            self.cancelled.emit()
            return
        if "bad" in self.input_path.name:
            self.error_report.emit("REPORT: boom\nTraceback …", "/tmp/report.txt")
            self.error.emit("boom")
            return
        dest = settings.resolve_output_path(self.input_path, self.kw["output_dir"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"x")
        self.finished.emit(dest)


@pytest.fixture
def win(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, "settings_path", lambda: tmp_path / "settings.json")
    monkeypatch.setattr(gui, "PipelineWorker", _FakeWorker)
    monkeypatch.setattr(gui, "_prefetch_stems", lambda path, overlap: None)   # no real MDX-Net
    import threading
    _FakeWorker.runs, _FakeWorker.answered_ahead = [], set()
    _FakeWorker.release = threading.Event()
    w = gui.MainWindow()
    yield w
    w.close()


def _videos(tmp_path, *names):
    out = []
    for n in names:
        p = tmp_path / n
        p.touch()
        out.append(p)
    return out


def _wait_idle(w, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        QApplication.processEvents()
        if w._running < 0 and not any(j.status in ("pending", "running") for j in w._jobs):
            return
        time.sleep(0.01)
    raise AssertionError("queue did not finish")


def test_add_multiple_and_skip_duplicates(win, tmp_path):
    a, b = _videos(tmp_path, "a.mp4", "b.mp4")
    win._add_files([a, b])
    win._add_files([a])
    assert [j.path.name for j in win._jobs] == ["a.mp4", "b.mp4"]
    assert win._list.count() == 2


def test_video_info_is_read_in_the_background(win, tmp_path, monkeypatch):
    import threading
    import dubber.core.video.info as info_mod
    gate, seen_threads = threading.Event(), []

    def slow_probe(path):
        seen_threads.append(threading.current_thread() is threading.main_thread())
        gate.wait(5)
        return SimpleNamespace(width=640, height=360, duration=12.0, fps=25.0, rotation=0,
                               aspect="16:9", size_bytes=1, bitrate_kbps=0, video_codec="h264",
                               bit_depth=8, video_kbps=0, audio_codec="aac", sample_rate=48000,
                               channels=2, audio_kbps=0)
    monkeypatch.setattr(info_mod, "probe_info", slow_probe)
    a, b = _videos(tmp_path, "a.mp4", "b.mp4")
    win._add_files([a, b])                      # returns at once, nothing read yet
    assert "đang đọc" in win._list.itemWidget(win._list.item(0)).text()
    assert not win._queue_busy.isHidden()
    assert "Đang đọc" in win._info._val["res"].text()
    gate.set()
    end = time.time() + 10
    while any(not j.probed() for j in win._jobs) and time.time() < end:
        QApplication.processEvents(); time.sleep(0.01)
    assert seen_threads == [False, False]       # ffprobe never ran on the GUI thread
    assert "đang đọc" not in win._list.itemWidget(win._list.item(0)).text()
    assert win._queue_busy.isHidden()
    assert "640 × 360" in win._info._val["res"].text()


def test_running_video_is_read_only_other_videos_editable(win, tmp_path):
    a, b = _videos(tmp_path, "a.mp4", "b.mp4")
    win._add_files([a, b])
    win._jobs[0].status = gui._Job.RUNNING
    win._running = 0
    win._list.setCurrentRow(1)
    win._list.setCurrentRow(0)
    assert win._opts._read_only and win._opts._tabs.tabBar().isEnabled()
    assert not win._opts._banner.isHidden()
    win._list.setCurrentRow(1)
    assert not win._opts._read_only and win._opts._combo_font.isEnabled()
    win._jobs[0].status, win._running = gui._Job.PENDING, -1


def test_each_video_keeps_its_own_config(win, tmp_path):
    a, b = _videos(tmp_path, "a.mp4", "b.mp4")
    win._add_files([a, b])
    win._list.setCurrentRow(0)
    win._opts._sld_vi.setValue(150)
    gui._select_data(win._export._combo_res, "-2:720")
    win._list.setCurrentRow(1)
    win._opts._sld_vi.setValue(80)
    win._list.setCurrentRow(0)  # switching back reloads a's config into the panels
    assert win._opts._sld_vi.value() == 150
    assert win._export.scale() == "-2:720"
    assert win._jobs[1].opts["vi"] == 80
    assert win._jobs[1].export["scale"] is None


def test_runs_sequentially_with_per_video_settings(win, tmp_path):
    out = tmp_path / "out"
    win._out._edit_dir.setText(str(out))  # set first: unchecking with no dir opens a dialog
    win._out._chk_beside.setChecked(False)
    a, bad, c = _videos(tmp_path, "a.mp4", "bad.mp4", "c.mp4")
    win._add_files([a, bad, c])
    win._list.setCurrentRow(2)
    win._opts._sld_vi.setValue(120)

    win._start()
    _wait_idle(win)

    assert [r[0] for r in _FakeWorker.runs] == ["a.mp4", "bad.mp4", "c.mp4"]
    assert _FakeWorker.runs[0][1]["vi_volume"] == 1.0
    assert _FakeWorker.runs[2][1]["vi_volume"] == 1.2
    assert [j.status for j in win._jobs] == ["done", "error", "done"]  # error doesn't stop the queue
    assert win._jobs[0].output == out / "a_vi.mp4"
    assert (out / "c_vi.mp4").exists()


def test_stop_after_current(win, tmp_path):
    a, b = _videos(tmp_path, "a.mp4", "b.mp4")
    win._add_files([a, b])
    win._start()
    win._request_stop()
    end = time.time() + 5
    while time.time() < end and win._running >= 0:
        QApplication.processEvents(); time.sleep(0.01)
    assert [j.status for j in win._jobs] == ["done", "pending"]


def test_output_dir_setting_persists(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    f = tmp_path / "settings.json"
    monkeypatch.setattr(settings, "settings_path", lambda: f)
    settings.save_settings({"output_dir": str(tmp_path / "phim")}, f)
    panel = gui.OutputPanel()
    assert panel.output_dir() == tmp_path / "phim"
    panel._chk_beside.setChecked(True)
    assert settings.load_settings(f)["output_dir"] is None


# ── Real PipelineWorker: job folder lifecycle ─────────────────────────────────

def _run_worker(tmp_path, monkeypatch, src, fail=False, **kw):
    """Run a real PipelineWorker with a fake Pipeline; returns (files seen at start, result)."""
    import dubber.core.pipeline as pl
    import dubber.core.utils.paths as paths
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(paths, "_app_data_dir", lambda: tmp_path / "appdata")
    seen = {}

    class FakePipeline:
        def __init__(self, ffmpeg, ffprobe, job_dir):
            seen["files_at_start"] = sorted(p.name for p in job_dir.iterdir())
            seen["job_dir"] = job_dir
            self.job_dir = job_dir
            self.run_m1 = self.run_m2 = self.run_m3 = self.run_m4 = lambda *a, **k: None

        def run_m5(self, input_path, **k):
            (self.job_dir / "asr.json").write_text("cached")      # a cacheable artifact
            if fail:
                raise RuntimeError("render failed")
            out = self.job_dir / "output.mp4"
            out.write_bytes(b"video")
            tj = self.job_dir / "translate.json"
            tj.write_text('[{"id": 0, "start": 0, "end": 1, "src_text": "你好", '
                          '"vi_text": "Xin chào."}]', encoding="utf-8")
            return None, tj, None, None, out

    monkeypatch.setattr(pl, "Pipeline", FakePipeline)
    w = gui.PipelineWorker(src, output_dir=tmp_path / "out", **kw)
    res = []
    w.finished.connect(res.append)
    w.error.connect(lambda m: res.append(("error", m)))
    w.run()
    return seen, res


def test_worker_keeps_job_for_same_input(tmp_path, monkeypatch):
    src = tmp_path / "clip.mp4"; src.write_bytes(b"src")
    seen, res = _run_worker(tmp_path, monkeypatch, src)
    assert res == [tmp_path / "out" / "clip_vi.mp4"]
    assert (tmp_path / "out" / "clip_vi.mp4").read_bytes() == b"video"
    txt = (tmp_path / "out" / "clip_vi.txt").read_text(encoding="utf-8")
    assert "Hashtag: #... #... #... #... #..." in txt and txt.rstrip().endswith("[1] Xin chào.")
    job = seen["job_dir"]
    assert (job / "asr.json").exists()            # caches are never auto-deleted
    assert not (job / "output.mp4").exists()      # the video is moved out, not kept twice

    seen, _ = _run_worker(tmp_path, monkeypatch, src)            # same file → cache kept
    assert "asr.json" in seen["files_at_start"]


def test_worker_resets_job_when_input_or_trim_changes(tmp_path, monkeypatch):
    src = tmp_path / "clip.mp4"; src.write_bytes(b"src")
    import subprocess
    from unittest.mock import MagicMock

    def fake_ffmpeg(cmd, **kw):              # the trim: just write the output file
        Path(cmd[-1]).write_bytes(b"trimmed")
        return MagicMock(returncode=0)
    monkeypatch.setattr(subprocess, "run", fake_ffmpeg)
    _run_worker(tmp_path, monkeypatch, src)
    seen, _ = _run_worker(tmp_path, monkeypatch, src, trim_start=0.0, trim_end=20.0)
    assert seen["files_at_start"] == ["input.json", "trimmed.mp4"]   # 20 s trim ≠ full video
    import os, time
    src.write_bytes(b"replaced source")                          # same path, new content
    os.utime(src, (time.time() + 5, time.time() + 5))
    seen, _ = _run_worker(tmp_path, monkeypatch, src)
    assert seen["files_at_start"] == ["input.json"]


def test_worker_keeps_job_on_error(tmp_path, monkeypatch):
    src = tmp_path / "clip.mp4"; src.write_bytes(b"src")
    seen, res = _run_worker(tmp_path, monkeypatch, src, fail=True)
    assert res == [("error", "render failed")]
    assert (seen["job_dir"] / "asr.json").exists()               # retry can reuse ASR


def _jobs(tmp_path, monkeypatch, ages_days, size=10):
    """Job dirs j0, j1, … last used *ages_days* ago, each holding *size* bytes."""
    import os
    import dubber.core.utils.paths as paths
    monkeypatch.setattr(paths, "_app_data_dir", lambda: tmp_path)
    now = time.time()
    for i, age in enumerate(ages_days):
        d = tmp_path / "jobs" / f"j{i}"; d.mkdir(parents=True)
        (d / "data.bin").write_bytes(b"x" * size)
        (d / "input.json").write_text("{}")
        t = now - age * 86400
        os.utime(d / "input.json", (t, t))
    return paths


def test_error_reports_are_never_deleted(tmp_path, monkeypatch):
    import dubber.core.utils.error_report as er
    monkeypatch.setattr(er, "errors_dir", lambda: tmp_path)
    for i in range(40):
        (tmp_path / f"old{i:02d}.txt").write_text("x")
    er.write_error_report(tmp_path / "v.mp4", "boom", "tb", {})
    assert len(list(tmp_path.glob("*.txt"))) == 41


def test_clean_button(win, tmp_path, monkeypatch):
    import dubber.core.tts.demo as demo
    import dubber.core.utils.paths as paths
    _jobs(tmp_path / "app", monkeypatch, [0, 1], size=2048)
    monkeypatch.setattr(demo, "demo_dir", lambda: tmp_path / "demos")
    (tmp_path / "demos").mkdir(); (tmp_path / "demos" / "x.wav").write_bytes(b"x" * 1024)
    out = win._settings_dlg.storage                            # in ⚙ Cài đặt now
    out.refresh_storage()
    assert "5 KB" in out._lbl_storage.text() and out._btn_clean.isEnabled()
    out.set_busy(True)
    assert not out._btn_clean.isEnabled()                     # not while a video runs
    out.set_busy(False)
    import dubber.app.settings_dialog as sd
    monkeypatch.setattr(sd.QMessageBox, "question", lambda *a, **k: sd.QMessageBox.Yes)
    out._clean_storage()
    assert list(paths.jobs_root().iterdir()) == [] and not (tmp_path / "demos").exists()
    assert "0 B" in out._lbl_storage.text() and not out._btn_clean.isEnabled()


def test_asr_vocals_option_per_video_and_old_configs(win, tmp_path):
    if not win._opts._mdxnet:
        pytest.skip("audio-separator not installed — option is disabled")
    a, b = _videos(tmp_path, "a.mp4", "b.mp4")
    win._add_files([a, b])
    win._list.setCurrentRow(1)
    win._opts._chk_asr_vocals.setChecked(False)
    win._list.setCurrentRow(0)
    assert win._opts.asr_on_vocals()           # default on
    old = dict(win._jobs[0].opts); old.pop("asr_vocals")
    win._opts.set_config(old)                  # config saved before the option existed
    assert win._opts.asr_on_vocals()
    win._start()
    _wait_idle(win)
    assert [r[1]["asr_on_vocals"] for r in _FakeWorker.runs] == [True, False]


def test_worker_reports_separate_then_asr(tmp_path, monkeypatch):
    import dubber.core.pipeline as pl
    import dubber.core.utils.paths as paths
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(paths, "job_dir_for", lambda _p: tmp_path / "job")
    events = []

    class FakePipeline:
        def __init__(self, ffmpeg, ffprobe, job_dir):
            self.job_dir = job_dir
            self.run_m2 = self.run_m3 = self.run_m4 = lambda *a, **k: None

        def run_m1(self, p, on_progress=None, **kw):
            on_progress["separate"](0.0); on_progress["separate"](1.0)
            on_progress["asr"](0.0)

        def run_m5(self, input_path, on_progress=None, **kw):
            self.run_m1(input_path, on_progress=on_progress)
            out = self.job_dir / "output.mp4"; out.write_bytes(b"v")
            return None, None, None, None, out

    monkeypatch.setattr(pl, "Pipeline", FakePipeline)
    src = tmp_path / "clip.mp4"; src.touch()
    w = gui.PipelineWorker(src, output_dir=tmp_path / "out")
    w.step_started.connect(lambda n: events.append(("start", n)))
    w.step_done.connect(lambda n: events.append(("done", n)))
    w.run()
    assert events[:3] == [("start", "Separate"), ("done", "Separate"), ("start", "ASR")]


# ── TTS engine / VieNeu voices ────────────────────────────────────────────────

@pytest.fixture
def voice_lib(tmp_path, monkeypatch):
    """Empty clone library in tmp; VieNeu treated as installed."""
    from dubber.core.tts import voice_library as vl
    root = tmp_path / "voices"
    monkeypatch.setattr(vl, "voices_dir", lambda: root)
    monkeypatch.setattr(vl, "preset_voices", lambda: [
        vl.VoiceEntry("preset:Hải Đăng", "Hải Đăng", "preset"),
        vl.VoiceEntry("preset:Mai Anh", "Mai Anh", "preset"),
    ])
    return root


def _fake_clone(root, vid, name):
    import json
    d = root / vid
    d.mkdir(parents=True)
    (d / "voice.json").write_text(json.dumps({"speaker_emb": [0.0] * 192, "codes": None}))
    (d / "meta.json").write_text(json.dumps({"id": vid, "name": name}))


def test_engine_switch_reloads_voices(win, voice_lib):
    opts = win._opts
    if not opts._vieneu_ok:
        pytest.skip("vieneu not installed")
    _fake_clone(voice_lib, "be-na-1a2b3c", "Bé Na")
    gui._select_data(opts._combo_engine, "vieneu")
    items = [opts._combo_voice.itemText(i) for i in range(opts._combo_voice.count())]
    assert items == ["— Giọng có sẵn —", "Hải Đăng", "Mai Anh", "— Giọng đã clone —", "Bé Na",
                     "➕ Clone giọng mới…"]
    assert opts._combo_voice.currentData() == "preset:Hải Đăng"     # headers never selected
    assert opts._btn_delete_voice.isHidden()                         # presets can't be deleted
    gui._select_data(opts._combo_voice, "clone:be-na-1a2b3c")
    assert not opts._btn_delete_voice.isHidden() and not opts._btn_rename_voice.isHidden()
    gui._select_data(opts._combo_engine, "piper")
    assert opts._combo_voice.currentData() == opts._default_piper
    assert opts._btn_delete_voice.isHidden()


def test_engine_and_voice_per_video_and_old_configs(win, voice_lib, tmp_path):
    opts = win._opts
    if not opts._vieneu_ok:
        pytest.skip("vieneu not installed")
    a, b = _videos(tmp_path, "a.mp4", "b.mp4")
    win._add_files([a, b])
    win._list.setCurrentRow(1)
    gui._select_data(opts._combo_engine, "vieneu")
    gui._select_data(opts._combo_voice, "preset:Mai Anh")
    win._list.setCurrentRow(0)
    assert opts.tts_engine() == "piper"
    old = dict(win._jobs[0].opts); old.pop("tts_engine")         # saved before VieNeu existed
    opts.set_config(old)
    assert opts.tts_engine() == "piper"
    win._start()
    _wait_idle(win)
    assert [(r[1]["tts_engine"], r[1]["voice"]) for r in _FakeWorker.runs] == [
        ("piper", opts._default_piper), ("vieneu", "preset:Mai Anh")]


def test_last_engine_and_voice_remembered(win, voice_lib, tmp_path, monkeypatch):
    opts = win._opts
    if not opts._vieneu_ok:
        pytest.skip("vieneu not installed")
    gui._select_data(opts._combo_engine, "vieneu")
    gui._select_data(opts._combo_voice, "preset:Mai Anh")
    assert settings.load_settings()["voice"] == "preset:Mai Anh"
    win2 = gui.MainWindow()                                      # next app start
    assert (win2._opts.tts_engine(), win2._opts.voice()) == ("vieneu", "preset:Mai Anh")
    win2.close()


def test_deleted_clone_in_queue_is_kept(win, voice_lib):
    opts = win._opts
    if not opts._vieneu_ok:
        pytest.skip("vieneu not installed")
    cfg = dict(opts.get_config(), tts_engine="vieneu", voice="clone:gone-000000")
    opts.set_config(cfg)
    assert opts.voice() == "clone:gone-000000"                   # not silently swapped
    assert "đã bị xóa" in opts._combo_voice.currentText()


def test_worker_stops_on_deleted_clone(tmp_path, voice_lib, monkeypatch):
    import dubber.core.pipeline as pl
    import dubber.core.utils.paths as paths
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(paths, "job_dir_for", lambda _p: tmp_path / "job")
    monkeypatch.setattr(pl, "Pipeline", lambda *a, **k: pytest.fail("should stop before running"))
    src = tmp_path / "clip.mp4"; src.touch()
    w = gui.PipelineWorker(src, tts_engine="vieneu", voice="clone:gone-000000")
    errors = []
    w.error.connect(errors.append)
    w.run()
    assert errors and "đã bị xóa" in errors[0]


def test_clone_dialog_flow(tmp_path, voice_lib, monkeypatch):
    import numpy as np
    import wave as _wave
    from dubber.app.voice_dialog import CloneVoiceDialog
    import dubber.core.tts.vieneu_tts as vt
    from dubber.core.utils.ffmpeg_locator import find_ffmpeg
    try:
        find_ffmpeg()
    except FileNotFoundError:
        pytest.skip("FFmpeg not available")
    QApplication.instance() or QApplication([])

    class SDK:
        sample_rate = 48_000
        def encode_reference(self, p):
            return np.zeros(192, np.float32), np.ones((10, 16), np.int64)
    monkeypatch.setattr(vt, "shared_vieneu", lambda: SDK())

    def wav(path, secs):
        with _wave.open(str(path), "wb") as wf:
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000)
            wf.writeframes(b"\1\0" * int(secs * 16000))
        return path

    def settle():   # reading / cutting the file runs on a worker thread
        end = time.time() + 10
        while dlg._busy and time.time() < end:
            QApplication.processEvents(); time.sleep(0.01)

    dlg = CloneVoiceDialog()
    dlg.set_source(wav(tmp_path / "short.wav", 2.0))
    assert dlg._busy and not dlg._busy_bar.isHidden()   # loading bar while ffprobe reads it
    settle()
    assert dlg._busy_bar.isHidden()
    assert "quá ngắn" in dlg._lbl_status.text()
    assert not dlg._spin_start.isEnabled()
    dlg.set_source(wav(tmp_path / "long.wav", 12.0))
    settle()
    assert dlg._spin_start.isEnabled() and dlg._spin_start.maximum() == pytest.approx(11.0, abs=0.1)
    dlg._edit_name.setText("Giọng thử")
    dlg._spin_start.setValue(3.0)
    dlg._save()
    end = time.time() + 10
    while dlg.result() != gui.QDialog.Accepted and time.time() < end:
        QApplication.processEvents(); time.sleep(0.01)
    assert dlg.entry is not None and dlg.entry.name == "Giọng thử"
    from dubber.core.tts import voice_library as vl
    assert [v.name for v in vl.clone_voices()] == ["Giọng thử"]
    assert not dlg._runner.busy()


# ── Option hints (one line under each choice, also the tooltip) ───────────────

def _hint_under(widget):
    """The hint QLabel added right below *widget*'s row in its form."""
    from PySide6.QtWidgets import QFormLayout
    form = widget.parentWidget().layout()
    assert isinstance(form, QFormLayout)
    row = next(r for r in range(form.rowCount())
               if (it := form.itemAt(r, QFormLayout.FieldRole)) and
               (it.widget() is widget or (it.widget() and widget in it.widget().findChildren(type(widget)))))
    from PySide6.QtWidgets import QLabel
    for r in range(row + 1, form.rowCount()):   # first label below (trim has its spinbox row first)
        w = form.itemAt(r, QFormLayout.FieldRole).widget()
        if isinstance(w, QLabel):
            return w
    raise AssertionError("no hint below widget")


def test_translator_hint_follows_choice(win):
    combo = win._opts._combo_translator
    hint = _hint_under(combo)
    for key, text in gui._HINTS_TRANSLATOR.items():
        gui._select_data(combo, key)
        assert hint.text() == text and combo.toolTip() == text


def test_engine_and_export_hints(win):
    opts, exp = win._opts, win._export
    assert _hint_under(opts._combo_engine).text() == gui._HINTS_ENGINE["piper"]
    if opts._vieneu_ok:
        gui._select_data(opts._combo_engine, "vieneu")
        assert _hint_under(opts._combo_engine).text() == gui._HINTS_ENGINE["vieneu"]
    res_hint = _hint_under(exp._combo_res)
    gui._select_data(exp._combo_res, None)
    assert res_hint.text() == "Giữ nguyên như video gốc"
    gui._select_data(exp._combo_res, "-2:720")
    assert res_hint.text() == gui._HINT_RES_OTHER
    for w, text in ((exp._combo_fps, gui._HINT_FPS), (exp._combo_quality, gui._HINT_CRF),
                    (exp._combo_preset, gui._HINT_PRESET), (exp._combo_audio_br, gui._HINT_AUDIO_BR),
                    (opts._chk_subs, gui._HINT_SUBS), (opts._chk_trim, gui._HINT_TRIM)):
        assert _hint_under(w).text() == text and w.toolTip() == text


def test_voice_hint_for_vieneu(win, voice_lib):
    opts = win._opts
    if not opts._vieneu_ok:
        pytest.skip("vieneu not installed")
    assert not opts._hint_voice.isVisibleTo(opts)                # Piper: no voice note
    _fake_clone(voice_lib, "an-000001", "An")
    gui._select_data(opts._combo_engine, "vieneu")
    assert "Clone giọng mới" in opts._hint_voice.text()
    i = opts._combo_voice.findData(opts._CLONE_NEW)
    assert opts._combo_voice.itemData(i, gui.Qt.ToolTipRole) == gui._HINT_CLONE_ITEM
    gui._select_data(opts._combo_voice, "clone:an-000001")
    assert "🗑" in opts._hint_voice.text()


# ── Usable again after a run (no restart needed) ──────────────────────────────

def test_queue_usable_again_after_run(win, tmp_path):
    a, bad = _videos(tmp_path, "a.mp4", "bad.mp4")
    win._add_files([a, bad])
    win._list.setCurrentRow(0)
    win._start()
    _wait_idle(win)
    assert [j.status for j in win._jobs] == ["done", "error"]
    assert win._opts.isEnabled() and win._export.isEnabled()      # unlocked after the run
    assert not win._btn_start.isEnabled()                          # nothing pending yet

    win._opts._sld_vi.setValue(140)                                # edit the finished video …
    assert win._btn_rerun.isEnabled()
    win._rerun_selected()                                          # … and queue it again
    assert win._jobs[0].status == "pending" and win._jobs[0].opts["vi"] == 140
    win._add_files([bad])                                          # re-adding requeues too
    assert win._jobs[1].status == "pending" and len(win._jobs) == 2
    assert win._btn_start.isEnabled()

    _FakeWorker.runs = []
    win._start()
    _wait_idle(win)
    assert [r[0] for r in _FakeWorker.runs] == ["a.mp4", "bad.mp4"]
    assert _FakeWorker.runs[0][1]["vi_volume"] == 1.4


# ── Cancel ────────────────────────────────────────────────────────────────────

def test_cancel_stops_queue_and_job_can_rerun(win, tmp_path):
    slow, b = _videos(tmp_path, "slow.mp4", "b.mp4")
    win._add_files([slow, b])
    win._start()
    end = time.time() + 5
    while not win._btn_cancel.isEnabled() and time.time() < end:
        QApplication.processEvents(); time.sleep(0.01)
    win._cancel_current()
    end = time.time() + 10
    while (win._running >= 0 or win._cancelling) and time.time() < end:   # b stays pending
        QApplication.processEvents(); time.sleep(0.01)
    assert [j.status for j in win._jobs] == ["cancelled", "pending"]   # queue stopped too
    assert "Đã hủy" in win._status.text()
    assert not win._btn_cancel.isEnabled()
    win._list.setCurrentRow(0)
    assert win._btn_rerun.isEnabled() and win._opts.isEnabled()


def test_worker_cancel_deletes_job_files(tmp_path, monkeypatch):
    import dubber.core.pipeline as pl
    import dubber.core.utils.paths as paths
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(paths, "_app_data_dir", lambda: tmp_path / "appdata")
    seen = {}

    class FakePipeline:
        def __init__(self, ffmpeg, ffprobe, job_dir):
            self.job_dir = job_dir
            seen["job_dir"] = job_dir
            self.run_m1 = self.run_m2 = self.run_m3 = self.run_m4 = lambda *a, **k: None

        def run_m5(self, input_path, on_progress=None, **k):
            (self.job_dir / "asr.json").write_text("partial")
            w.cancel()                                   # user clicks ✖ mid-render
            on_progress["render"](0.5)
            raise AssertionError("should have been cancelled")

    monkeypatch.setattr(pl, "Pipeline", FakePipeline)
    src = tmp_path / "clip.mp4"; src.write_bytes(b"src")
    w = gui.PipelineWorker(src, output_dir=tmp_path / "out")
    events = []
    w.cancelled.connect(lambda: events.append("cancelled"))
    w.error.connect(lambda m: events.append(("error", m)))
    w.finished.connect(lambda p: events.append("finished"))
    w.run()
    assert events == ["cancelled"]
    assert not seen["job_dir"].exists()
    assert not (tmp_path / "out").exists()


def test_render_kills_ffmpeg_when_progress_raises(tmp_path):
    import sys
    from dubber.core.utils.cancel import JobCancelled
    from dubber.core.video import render
    script = tmp_path / "fake_ffmpeg.py"
    script.write_text(
        "import sys, time\n"
        "sys.stderr.write('  Duration: 00:00:10.00, start: 0\\n'); sys.stderr.flush()\n"
        "for i in range(100):\n"
        "    sys.stderr.write(f'frame=1 time=00:00:0{i % 10}.00 bitrate=1\\n'); sys.stderr.flush()\n"
        "    time.sleep(0.05)\n")
    procs = []
    real_popen = render.subprocess.Popen

    def popen(*a, **k):
        procs.append(real_popen(*a, **k)); return procs[-1]

    def cancel(_pct):
        raise JobCancelled()

    import unittest.mock as um
    with um.patch.object(render.subprocess, "Popen", popen):
        with pytest.raises(JobCancelled):
            render._run_ffmpeg_with_progress([sys.executable, str(script)], cancel)
    assert procs[0].poll() is not None       # child process is gone


# ── Waiting on the user: Groq rate limit ──────────────────────────────────────

def test_rate_limit_dialog_cancel_cancels_video(win, tmp_path, monkeypatch):
    import dubber.app.wait_dialogs as md
    monkeypatch.setattr(md, "ask_rate_limit", lambda info, parent=None: None)
    (r,) = _videos(tmp_path, "ratelimit.mp4")
    win._add_files([r])
    win._start()
    end = time.time() + 10
    while (win._running >= 0 or win._cancelling) and time.time() < end:
        QApplication.processEvents(); time.sleep(0.01)
    assert win._jobs[0].status == "cancelled"


def test_rate_limit_dialog_continue(win, tmp_path, monkeypatch):
    import dubber.app.wait_dialogs as md
    monkeypatch.setattr(md, "ask_rate_limit", lambda info, parent=None: "local")
    _FakeWorker.answers = []
    (r,) = _videos(tmp_path, "ratelimit.mp4")
    win._add_files([r])
    win._start()
    _wait_idle(win)
    assert _FakeWorker.answers == ["local"] and win._jobs[0].status == "done"


# ── Trim range checked before running ─────────────────────────────────────────

@pytest.mark.parametrize("start,end,msg", [
    (4545.0, 150.0, "nằm ngoài video"),
    (50.0, 20.0, "phải nhỏ hơn kết thúc 20s"),
    (30.0, None, None),
])
def test_check_trim(monkeypatch, tmp_path, start, end, msg):
    from types import SimpleNamespace
    import dubber.core.video.info as info
    monkeypatch.setattr(info, "probe_info", lambda p: SimpleNamespace(duration=119.5))
    if msg is None:
        gui.check_trim(tmp_path / "v.mp4", start, end)
    else:
        with pytest.raises(RuntimeError, match=msg):
            gui.check_trim(tmp_path / "v.mp4", start, end)


def test_default_volumes(win):
    assert win._opts._sld_cn_voice.value() == 20
    assert win._opts._sld_music.value() == 40


@pytest.fixture
def trim(win):
    win._opts._chk_trim.setChecked(True)
    t = win._opts._trim
    t.set_duration(119.5)
    return t


def _type(spin, value):
    """Type a value and press Enter (the spinbox applies on commit, then corrects)."""
    spin.lineEdit().setText(f"{value}")
    spin.interpretText()


def test_trim_full_length_by_default(trim):
    assert trim.trim() == (0.0, None)                      # None = until the end
    assert trim.slider.values() == (0.0, 119.5)


@pytest.mark.parametrize("start,end,expected", [
    (10, 50, (10.0, 50.0)),
    (4545, 150, (119.0, None)),          # start past the video, end past the video
    (80, 20, (80.0, 80.5)),              # start after end → end follows start
    (-5, 30, (0.0, 30.0)),               # negative start
    (119.4, None, (119.0, None)),        # start too close to the end
    (30, 30, (30.0, 30.5)),              # empty range
])
def test_trim_invalid_values_are_corrected(trim, start, end, expected):
    trim.set_trim(start, end)
    assert trim.trim() == expected
    s, e = trim.trim()
    assert trim.slider.values() == (s, e if e is not None else 119.5)


def test_typed_values_are_clamped_and_move_the_slider(trim):
    trim.set_trim(10, 50)
    _type(trim.spin_start, 70)                             # later than end → stops before end
    assert trim.trim() == (49.5, 50.0)
    _type(trim.spin_end, 500)                              # past the video → end of video
    assert trim.trim() == (49.5, None)
    _type(trim.spin_start, 12.5)
    assert trim.slider.values() == (12.5, 119.5)
    _type(trim.spin_end, 5)                                # before start → just after start
    assert trim.trim() == (12.5, 13.0)
    _type(trim.spin_start, 4545)                           # way past the video
    assert trim.trim() == (12.5, 13.0)                     # start may only go up to end − 0.5
    assert trim.spin_start.text() == "12.5 s"              # shown corrected, not 4545


def test_dragging_updates_the_numbers(trim):
    trim.set_trim(10, 50)
    trim.slider.rangeChanged.emit(20.04, 60.0)             # as during a drag
    assert (trim.spin_start.value(), trim.spin_end.value()) == (20.0, 60.0)
    assert "dài 40.0s" in trim.lbl_info.text()


def test_slider_drag_respects_bounds(trim):
    sl = trim.slider
    sl.resize(400, 24)
    trim.set_trim(10, 50)
    sl._drag = "lo"; sl._move_to(10_000)                   # drag start past the end handle
    assert sl.values()[0] == pytest.approx(49.5)
    sl._drag = "hi"; sl._move_to(-50)                      # drag end before the start handle
    assert sl.values()[1] == pytest.approx(50.0)


def test_trim_follows_each_videos_length(trim):
    trim.set_trim(100, None)
    trim.set_duration(30.0)                                # shorter video selected
    assert trim.trim() == (29.5, None)
    assert trim.slider.values() == (29.5, 30.0)


def test_trim_config_roundtrip(win):
    opts = win._opts
    opts.set_trim_limit(119.5)
    cfg = dict(opts.get_config(), trim_on=True, trim_start=12.0, trim_end=0.0)
    opts.set_config(cfg)
    assert (opts.trim_start(), opts.trim_end()) == (12.0, None)
    cfg["trim_end"] = 40.0
    opts.set_config(cfg)
    assert opts.get_config()["trim_end"] == 40.0 and opts.trim_end() == 40.0


@pytest.mark.parametrize("typed", ["12.5", "12,5", "12.5 s", "12,5 s"])
def test_seconds_input_accepts_dot_and_comma(trim, typed):
    trim.set_trim(0, None)
    trim.spin_start.lineEdit().setText(typed)
    trim.spin_start.interpretText()
    assert trim.trim()[0] == 12.5
    assert trim.spin_start.text() == "12.5 s"


# ── Error report + pop-up ─────────────────────────────────────────────────────

def test_worker_writes_error_report(tmp_path, monkeypatch):
    import dubber.core.pipeline as pl
    import dubber.core.utils.error_report as er
    import dubber.core.utils.paths as paths
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(paths, "_app_data_dir", lambda: tmp_path / "appdata")
    monkeypatch.setattr(er, "errors_dir", lambda: tmp_path / "errors")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_secret_value")

    class Boom:
        def __init__(self, *a, **k):
            self.run_m1 = self.run_m2 = self.run_m3 = self.run_m4 = lambda *a, **k: None

        def run_m5(self, *a, **k):
            raise RuntimeError("render failed: codec missing")

    monkeypatch.setattr(pl, "Pipeline", Boom)
    src = tmp_path / "clip.mp4"; src.write_bytes(b"v")
    w = gui.PipelineWorker(src, translator="groq", tts_engine="piper")
    got = []
    w.error_report.connect(lambda text, path: got.append((text, path)))
    w.run()
    text, path = got[0]
    assert Path(path).parent == tmp_path / "errors" and Path(path).read_text(encoding="utf-8") == text
    assert "render failed: codec missing" in text and "Traceback" in text
    assert '"translator": "groq"' in text and "clip.mp4" in text
    assert "gsk_secret_value" not in text


def test_error_popup_copy_and_reopen(win, tmp_path):
    bad, = _videos(tmp_path, "bad.mp4")
    win._add_files([bad])
    win._start()
    _wait_idle(win)
    dlg = win._error_dialog
    assert dlg.isVisible() and "REPORT: boom" in dlg.details.toPlainText()
    dlg.copy()
    assert QApplication.clipboard().text().startswith("REPORT: boom")
    dlg.close()
    win._on_item_double_clicked(win._list.item(0))               # look at it again later
    assert win._error_dialog.isVisible() and win._error_dialog is not dlg


# ── Voice preview + delete ────────────────────────────────────────────────────

def test_make_demo_cached(tmp_path, monkeypatch):
    import asyncio
    import dubber.core.tts.demo as demo
    import dubber.core.tts.factory as factory
    from dubber.core.tts.base import TTSResult
    monkeypatch.setattr(demo, "demo_dir", lambda: tmp_path / "demos")
    made = []

    class Eng:
        async def synthesize_one(self, text, sid, rate=None):
            p = tmp_path / "demos" / "tmp" / "x.wav"; p.write_bytes(b"RIFF")
            return TTSResult(sid, p, 1.0)

    def fake_make(engine, voice, tts_dir):
        made.append((engine, voice)); tts_dir.mkdir(parents=True, exist_ok=True); return Eng()
    monkeypatch.setattr(factory, "make_tts_engine", fake_make)
    a = demo.make_demo("vieneu", "preset:Mai Anh")
    b = demo.make_demo("vieneu", "preset:Mai Anh")               # second time: cache
    assert a == b and a.exists() and made == [("vieneu", "preset:Mai Anh")]
    assert demo.demo_path("piper", "x") != demo.demo_path("vieneu", "x")


def test_demo_button_generates_and_plays(win, monkeypatch, tmp_path):
    import wave as _wave
    import dubber.core.tts.demo as demo
    wav = tmp_path / "piper.wav"
    with _wave.open(str(wav), "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000)
        wf.writeframes(b"\0\0" * 1600)
    asked = []
    monkeypatch.setattr(demo, "make_demo", lambda e, v: asked.append((e, v)) or wav)
    opts = win._opts
    opts._play_demo()
    assert opts._btn_demo.text() == "… Đang tạo" and not opts._btn_demo.isEnabled()
    end = time.time() + 5
    while (opts._demo_task is not None or opts._demo_player is None) and time.time() < end:
        QApplication.processEvents(); time.sleep(0.01)
    assert asked == [("piper", opts._default_piper)]
    assert Path(opts._demo_player.source().toLocalFile()) == wav
    assert opts._btn_demo.text() == "▶ Nghe thử" and opts._btn_demo.isEnabled()
    opts._demo_player.stop()


def test_delete_clone_voice_button(win, voice_lib, monkeypatch):
    opts = win._opts
    if not opts._vieneu_ok:
        pytest.skip("vieneu not installed")
    _fake_clone(voice_lib, "an-000001", "An")
    gui._select_data(opts._combo_engine, "vieneu")
    gui._select_data(opts._combo_voice, "clone:an-000001")
    monkeypatch.setattr(gui.QMessageBox, "question", lambda *a, **k: gui.QMessageBox.Yes)
    opts._delete_voice()
    assert not (voice_lib / "an-000001").exists()
    assert opts._combo_voice.findData("clone:an-000001") < 0
    assert opts._combo_voice.currentData() == "preset:Hải Đăng"


# ── Translation review ────────────────────────────────────────────────────────

def test_skip_review_defaults_on_and_reaches_worker(win, tmp_path):
    assert win._opts.get_config()["skip_review"] is True
    cfg = win._opts.get_config()
    del cfg["skip_review"]                       # config saved before this option
    win._opts.set_config(cfg)
    assert win._opts._chk_skip_review.isChecked()
    a, b = _videos(tmp_path, "a.mp4", "b.mp4")
    win._add_files([a, b])
    win._list.setCurrentRow(1)
    win._opts._chk_skip_review.setChecked(False)  # only video b stops for review
    win._start()
    _wait_idle(win)
    kw = {name: k for name, k in _FakeWorker.runs}
    assert kw["a.mp4"]["skip_review"] is True and kw["b.mp4"]["skip_review"] is False


def test_review_dialog_answer_reaches_worker(win, tmp_path, monkeypatch):
    import dubber.app.review_dialog as rd

    class AutoDialog:
        def __init__(self, *a, **k): self.result_vi = self.result = ["Một.", "Hai."]
        def exec(self): return gui.QDialog.Accepted
        def deleteLater(self): pass
    monkeypatch.setattr(rd, "ReviewDialog", AutoDialog)
    _FakeWorker.answers = []
    (r,) = _videos(tmp_path, "review.mp4")
    win._add_files([r])
    win._start()
    _wait_idle(win)
    assert _FakeWorker.answers == [["Một.", "Hai."]]
    assert win._jobs[0].status == "done"


def test_review_dialog_cancel_cancels_video(win, tmp_path, monkeypatch):
    import dubber.app.review_dialog as rd

    class CancelDialog:
        def __init__(self, *a, **k): self.result_vi = self.result = None
        def exec(self): return gui.QDialog.Rejected
        def deleteLater(self): pass
    monkeypatch.setattr(rd, "ReviewDialog", CancelDialog)
    (r,) = _videos(tmp_path, "review.mp4")
    win._add_files([r])
    win._start()
    end = time.time() + 10
    while (win._running >= 0 or win._cancelling) and time.time() < end:
        QApplication.processEvents(); time.sleep(0.01)
    assert win._jobs[0].status == "cancelled"


def test_old_manual_translator_config_runs_as_auto(win, tmp_path):
    cfg = dict(win._opts.get_config(), translator="manual")      # saved before removal
    win._opts.set_config(cfg)
    assert win._opts.translator() == "auto"
    (a,) = _videos(tmp_path, "a.mp4")
    win._add_files([a])
    win._jobs[0].opts["translator"] = "manual"
    win._start()
    _wait_idle(win)
    assert _FakeWorker.runs[0][1]["translator"] == "auto"


# ── Separating the next queued video ahead of its turn ────────────────────────

def test_next_video_is_prefetched_once_the_running_one_is_translated(win, tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(gui, "_prefetch_stems", lambda path, overlap: seen.append((path, overlap)))
    a, b, c = _videos(tmp_path, "a.mp4", "b.mp4", "c.mp4")
    win._add_files([a, b, c])
    win._running = 0
    win._jobs[0].status = gui._Job.RUNNING
    for step in ("Separate", "ASR", "Translate"):
        win._on_started(step)
    assert seen == []                               # A may still use the GPU
    win._on_started("TTS")
    win._prefetch.join(2)
    assert seen == [(b, 0.5)]                       # only the next one, at its quality
    win._on_started("TTS")                          # e.g. a second callback: not again
    win._prefetch.join(2)
    assert seen == [(b, 0.5)]
    win._running, win._jobs[0].status = -1, gui._Job.PENDING


def test_no_prefetch_for_trimmed_or_same_file_or_no_separation(win, tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(gui, "_prefetch_stems", lambda path, overlap: seen.append((path, overlap)))
    a, b = _videos(tmp_path, "a.mp4", "b.mp4")
    win._add_files([a, b])
    win._running = 0
    win._jobs[0].status = gui._Job.RUNNING
    nxt = win._jobs[1]
    for opts in ({"trim_on": True},
                 {"asr_vocals": False, "music_on": True, "cn_on": True, "music": 30, "cn": 30}):
        saved = dict(nxt.opts)
        nxt.opts.update(opts)
        win._prefetch_next()
        nxt.opts.clear(); nxt.opts.update(saved)
    assert seen == []
    nxt.path = win._jobs[0].path                   # a duplicate of the running video
    win._prefetch_next()
    assert seen == []
    win._running, win._jobs[0].status = -1, gui._Job.PENDING


def test_needs_stems_follows_pipeline_rules():
    base = {"asr_vocals": False, "music_on": True, "cn_on": True, "music": 40, "cn": 20}
    assert gui._needs_stems(dict(base, asr_vocals=True))
    assert gui._needs_stems(base)                              # different volumes
    assert not gui._needs_stems(dict(base, cn=40))             # same volume: original audio
    assert not gui._needs_stems(dict(base, music_on=False, cn_on=False))
    assert gui._needs_stems(dict(base, cn_on=False))           # music only


def test_separation_quality_is_per_video_and_reaches_the_worker(win, tmp_path):
    from dubber.core.audio.separate import DEFAULT_OVERLAP
    a, b = _videos(tmp_path, "a.mp4", "b.mp4")
    win._add_files([a, b])
    assert win._jobs[0].opts["sep_overlap"] == DEFAULT_OVERLAP
    win._list.setCurrentRow(1)
    gui._select_data(win._opts._combo_sep, 0.75)
    win._list.setCurrentRow(0)                      # leaving the row saves its config
    assert win._jobs[1].opts["sep_overlap"] == 0.75
    assert win._jobs[0].opts["sep_overlap"] == DEFAULT_OVERLAP
    win._start()
    _wait_idle(win)
    assert [kw["sep_overlap"] for _, kw in _FakeWorker.runs] == [DEFAULT_OVERLAP, 0.75]


def test_old_configs_get_the_default_separation_quality():
    from dubber.core.audio.separate import DEFAULT_OVERLAP
    assert gui._sep_overlap_of({}) == DEFAULT_OVERLAP
    assert gui._sep_overlap_of({"sep_overlap": 0.33}) == DEFAULT_OVERLAP   # not a choice
    assert gui._sep_overlap_of({"sep_overlap": 0.1}) == 0.1


def test_logo_of_a_queued_video_becomes_the_remembered_one(tmp_path, monkeypatch):
    """Logos set before 'last_logo' existed are picked up from the saved queue."""
    from PIL import Image
    monkeypatch.setattr(settings, "settings_path", lambda: tmp_path / "settings.json")
    monkeypatch.setattr(gui, "PipelineWorker", _FakeWorker)
    QApplication.instance() or QApplication([])
    img = tmp_path / "l.png"
    Image.new("RGBA", (20, 10), (255, 0, 0, 255)).save(img)
    (a,) = _videos(tmp_path, "a.mp4")
    w = gui.MainWindow()
    w._add_files([a])
    w.close()                                               # saves the queue
    st = settings.load_settings()                           # … as an older version left it
    st["queue"][0]["opts"].update(logo_on=True, logo_path=str(img), logo_size=15)
    st.pop("last_logo", None)
    settings.save_settings(st)
    w2 = gui.MainWindow()                                   # restart
    assert settings.load_settings()["last_logo"]["logo_path"] == str(img)
    assert gui.OptionsPanel().get_config()["logo_size"] == 15   # new videos get it
    w2.close()


def test_manual_translation_dialog_answer_reaches_worker(win, tmp_path, monkeypatch):
    import dubber.app.manual_dialog as md
    segs = [{"start": 0.0, "end": 2.0, "vi_text": "Một câu gộp."}]

    class AutoDialog:
        def __init__(self, video, rows, duration, parent=None):
            assert duration == 10.0
            self.segments = segs
        def exec(self): return gui.QDialog.Accepted
        def deleteLater(self): pass
    monkeypatch.setattr(md, "ManualTranslateDialog", AutoDialog)
    _FakeWorker.answers = []
    (m,) = _videos(tmp_path, "manual.mp4")
    win._add_files([m])
    win._start()
    _wait_idle(win)
    assert _FakeWorker.answers == [segs] and win._jobs[0].status == "done"


def test_ai_manual_translator_is_offered_and_kept(win, tmp_path):
    (a,) = _videos(tmp_path, "a.mp4")
    win._add_files([a])
    combo = win._opts._combo_translator
    assert combo.findData("ai_manual") >= 0
    gui._select_data(combo, "ai_manual")
    win._start()
    _wait_idle(win)
    assert _FakeWorker.runs[-1][1]["translator"] == "ai_manual"


# ── Each queued video asks its own popup ahead of its turn ────────────────────

def _wait_for(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        QApplication.processEvents()
        if cond():
            return
        time.sleep(0.01)
    raise AssertionError("condition not reached")


def _manual_dialog(monkeypatch, log, segs, accept=True, during=None):
    import dubber.app.manual_dialog as md

    class Dialog:
        def __init__(self, video, rows, duration, parent=None):
            self.video, self.segments = video, segs
        def exec(self):
            log.append(("open", self.video))
            if during:
                during()
            log.append(("close", self.video))
            return gui.QDialog.Accepted if accept else gui.QDialog.Rejected
        def deleteLater(self): pass
    monkeypatch.setattr(md, "ManualTranslateDialog", Dialog)


def test_needs_user_follows_translator_and_review():
    assert gui._needs_user({"translator": "ai_manual", "skip_review": True})
    assert gui._needs_user({"translator": "groq", "skip_review": False})
    assert not gui._needs_user({"translator": "groq", "skip_review": True})
    assert not gui._needs_user({"translator": "manual"})        # old config → auto


def test_queued_video_asks_its_popup_while_the_running_one_renders(win, tmp_path, monkeypatch):
    log, segs = [], [{"start": 0.0, "end": 2.0, "vi_text": "Một câu."}]
    _manual_dialog(monkeypatch, log, segs,
                   during=lambda: log.append(("running", win._jobs[0].status)))
    _FakeWorker.answers = []
    a, m = _videos(tmp_path, "render.mp4", "manual.mp4")
    win._add_files([a, m])
    win._jobs[1].opts["translator"] = "ai_manual"
    win._start()
    _wait_for(lambda: _FakeWorker.answers == [segs] and win._prep is None)
    assert log == [("open", "manual.mp4"), ("running", "running"), ("close", "manual.mp4")]
    assert win._jobs[1].status == "pending" and win._jobs[0].status == "running"
    _FakeWorker.release.set()                     # A finishes → M runs with the answer
    _wait_idle(win)
    names = [(n, k.get("prepare", False)) for n, k in _FakeWorker.runs]
    assert names == [("render.mp4", False), ("manual.mp4", True), ("manual.mp4", False)]
    assert [j.status for j in win._jobs] == ["done", "done"]
    assert len([e for e in log if e[0] == "open"]) == 1        # asked once, not again


def test_popups_never_overlap(win, tmp_path, monkeypatch):
    """The running video's question arrives while a queued video's popup is open: it
    waits until that popup is closed."""
    import dubber.app.wait_dialogs as wd
    log, segs = [], [{"start": 0.0, "end": 2.0, "vi_text": "Một câu."}]

    def rate_limit(info, parent=None):
        log.append(("rate_limit", info["video"]))
        return "continue"
    monkeypatch.setattr(wd, "ask_rate_limit", rate_limit)
    _manual_dialog(monkeypatch, log, segs, during=lambda: (
        win._on_ask("rate_limit", {"video": "render.mp4"}), QApplication.processEvents()))
    _FakeWorker.answers = []
    a, m = _videos(tmp_path, "render.mp4", "manual.mp4")
    win._add_files([a, m])
    win._jobs[1].opts["translator"] = "ai_manual"
    win._start()
    _wait_for(lambda: len(_FakeWorker.answers) == 2 and win._prep is None)
    assert log == [("open", "manual.mp4"), ("close", "manual.mp4"),
                   ("rate_limit", "render.mp4")]
    assert _FakeWorker.answers == [segs, "continue"]
    _FakeWorker.release.set()
    _wait_idle(win)


def test_cancelling_a_queued_videos_popup_cancels_only_that_video(win, tmp_path, monkeypatch):
    log = []
    _manual_dialog(monkeypatch, log, None, accept=False)
    a, m, b = _videos(tmp_path, "render.mp4", "manual.mp4", "b.mp4")
    win._add_files([a, m, b])
    win._jobs[1].opts["translator"] = "ai_manual"
    win._start()
    _wait_for(lambda: win._jobs[1].status == "cancelled" and win._prep is None)
    assert win._jobs[0].status == "running"            # the running video goes on
    _FakeWorker.release.set()
    _wait_idle(win)
    assert [j.status for j in win._jobs] == ["done", "cancelled", "done"]
    assert ("manual.mp4", False) not in [(n, k.get("prepare", False))
                                        for n, k in _FakeWorker.runs]


def test_review_videos_are_prepared_one_after_another(win, tmp_path, monkeypatch):
    import dubber.app.review_dialog as rd
    opened = []

    class Dialog:
        def __init__(self, video, rows, **k): opened.append(video); self.result = ["ok"]
        def exec(self): return gui.QDialog.Accepted
        def deleteLater(self): pass
    monkeypatch.setattr(rd, "ReviewDialog", Dialog)
    a, r1 = _videos(tmp_path, "render.mp4", "review.mp4")
    (tmp_path / "other").mkdir()
    r2 = tmp_path / "other" / "review.mp4"; r2.touch()
    win._add_files([a, r1, r2])
    for j in win._jobs[1:]:
        j.opts["skip_review"] = False
    win._start()
    _wait_for(lambda: len(opened) == 2 and win._prep is None)   # both before A finishes
    assert win._jobs[0].status == "running"
    _FakeWorker.release.set()
    _wait_idle(win)
    assert len(opened) == 2 and all(j.status == "done" for j in win._jobs)


def _fake_prep(win, job):
    import copy
    worker = _FakeWorker(job.path)
    win._prep = {"job": job, "worker": worker, "thread": None, "cancelled": False,
                 "opts": copy.deepcopy((job.opts, job.export))}
    return worker


_MANUAL_ASK = {"video": "manual.mp4", "rows": [], "duration": 10.0}


def test_stop_cancels_the_prepare_and_its_waiting_popup(win, tmp_path, monkeypatch):
    log = []
    _manual_dialog(monkeypatch, log, [{"start": 0.0, "end": 1.0, "vi_text": "x"}])
    (m,) = _videos(tmp_path, "manual.mp4")
    win._add_files([m])
    worker = _fake_prep(win, win._jobs[0])
    win._request_stop()                  # "stop after the current video"
    assert worker._cancel
    win._on_prep_ask("manual", _MANUAL_ASK)
    assert log == [] and win._jobs[0].status == "pending"
    win._prep = None


def test_popup_of_a_video_whose_settings_changed_is_dropped(win, tmp_path, monkeypatch):
    log = []
    _manual_dialog(monkeypatch, log, [{"start": 0.0, "end": 1.0, "vi_text": "x"}])
    (m,) = _videos(tmp_path, "manual.mp4")
    win._add_files([m])
    worker = _fake_prep(win, win._jobs[0])
    win._jobs[0].opts["translator"] = "groq"          # edited while it was preparing
    win._on_prep_ask("manual", _MANUAL_ASK)
    assert log == [] and worker._cancel and win._jobs[0].status == "pending"
    win._prep = None


def test_worker_prepare_stops_before_tts_and_holds_the_job_lock(tmp_path, monkeypatch):
    import threading
    import dubber.core.pipeline as pl
    import dubber.core.utils.paths as paths
    from dubber.core.audio.separate import separation_lock
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(paths, "_app_data_dir", lambda: tmp_path / "appdata")
    src = tmp_path / "clip.mp4"; src.write_bytes(b"src")
    seen = {}

    class FakePipeline:
        def __init__(self, ffmpeg, ffprobe, job_dir):
            self.job_dir, self.mark_reviewed = job_dir, False
            self.run_m1 = self.run_m3 = lambda *a, **k: None

        def run_m2(self, input_path, **k):
            seen["m2"], seen["mark"] = k, self.mark_reviewed
            got = []
            t = threading.Thread(target=lambda: got.append(
                separation_lock(self.job_dir).acquire(blocking=False)))
            t.start(); t.join()
            seen["lock_free"] = got[0]

        def run_m5(self, *a, **k):
            raise AssertionError("a prepare run never renders")

    monkeypatch.setattr(pl, "Pipeline", FakePipeline)
    w = gui.PipelineWorker(src, output_dir=tmp_path / "out", translator="ai_manual",
                           prepare=True)
    res = []
    w.finished.connect(res.append)
    w.error.connect(lambda m: res.append(("error", m)))
    w.run()
    assert res == [paths.job_dir_for(src)] and res[0].is_dir()
    assert seen["m2"]["translator"] == "ai_manual" and seen["mark"] is True
    assert seen["lock_free"] is False                 # its own run waits for it
    lock = separation_lock(res[0])
    assert lock.acquire(blocking=False); lock.release()   # released at the end
    assert not (tmp_path / "out").exists()


def test_worker_prepare_cancel_keeps_the_cache(tmp_path, monkeypatch):
    import dubber.core.pipeline as pl
    import dubber.core.utils.paths as paths
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(paths, "_app_data_dir", lambda: tmp_path / "appdata")
    src = tmp_path / "clip.mp4"; src.write_bytes(b"src")

    class FakePipeline:
        def __init__(self, ffmpeg, ffprobe, job_dir):
            self.job_dir = job_dir
            self.run_m1 = self.run_m3 = lambda *a, **k: None

        def run_m2(self, input_path, **k):
            (self.job_dir / "asr.json").write_text("[]")
            from dubber.core.utils.cancel import JobCancelled
            raise JobCancelled("Đã hủy")

    monkeypatch.setattr(pl, "Pipeline", FakePipeline)
    w = gui.PipelineWorker(src, translator="ai_manual", prepare=True)
    res = []
    w.cancelled.connect(lambda: res.append("cancelled"))
    w.run()
    assert res == ["cancelled"]
    assert (paths.job_dir_for(src) / "asr.json").exists()
