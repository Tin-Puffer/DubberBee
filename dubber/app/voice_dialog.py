"""Clone a VieNeu voice from an audio/video clip: pick, cut, listen, try, name, save."""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QObject, QThread, QUrl, Signal
from PySide6.QtWidgets import (
    QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QVBoxLayout,
)

from dubber.core.tts import voice_library as vl
from dubber.core.utils.ffmpeg_locator import find_ffprobe, no_window

SAMPLE_SENTENCE = "Xin chào, đây là giọng đọc thử nghiệm."
_FILE_FILTER = ("Âm thanh / video (*.wav *.mp3 *.m4a *.flac *.ogg *.mp4 *.mkv *.mov *.avi *.webm)"
                ";;Tất cả (*)")
_AMBER = "#ffb74d"
_RED = "#f44336"
_DIM = "#555"


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        [str(find_ffprobe()), "-v", "error", "-show_entries", "format=duration", "-of", "json",
         str(path)],
        capture_output=True, text=True, **no_window(),
    )
    try:
        return float(json.loads(out.stdout or "{}").get("format", {}).get("duration", 0) or 0)
    except ValueError:
        return 0.0


class _Task(QObject):
    """Runs fn() in a QThread; done/failed are delivered on the GUI thread."""
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[[], object]):
        super().__init__()
        self._fn = fn

    def run(self):
        try:
            self.done.emit(self._fn())
        except Exception as exc:   # shown in the dialog
            self.failed.emit(str(exc))


class CloneVoiceDialog(QDialog):
    """On accept, ``self.entry`` is the new VoiceEntry."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Clone giọng mới (VieNeu-TTS)")
        self.setMinimumWidth(460)
        self.entry: Optional[vl.VoiceEntry] = None
        self._src: Optional[Path] = None
        self._duration = 0.0
        self._tmp = tempfile.TemporaryDirectory(prefix="dubber-clone-")
        self._threads: list = []
        self._player = None

        lay = QVBoxLayout(self)
        form = QFormLayout()
        lay.addLayout(form)

        self._edit_file = QLineEdit(self)
        self._edit_file.setReadOnly(True)
        self._edit_file.setPlaceholderText("Chọn file ghi âm hoặc video…")
        btn_browse = QPushButton("Chọn…", self)
        btn_browse.clicked.connect(self._browse)
        row = QHBoxLayout(); row.addWidget(self._edit_file, 1); row.addWidget(btn_browse)
        form.addRow("File", row)

        self._lbl_dur = QLabel("—", self)
        form.addRow("Thời lượng", self._lbl_dur)

        self._spin_start = QDoubleSpinBox(self)
        self._spin_start.setSuffix(" s")
        self._spin_start.setSingleStep(0.5)
        self._spin_start.setEnabled(False)
        self._spin_start.setToolTip(f"Chỉ dùng {vl.MAX_REF_S:.0f} giây kể từ mốc này")
        form.addRow("Bắt đầu từ", self._spin_start)

        self._edit_name = QLineEdit(self)
        self._edit_name.setPlaceholderText("Ví dụ: Giọng anh Tuấn")
        self._edit_name.textChanged.connect(self._refresh)
        form.addRow("Tên giọng", self._edit_name)

        btns = QHBoxLayout()
        self._btn_listen = QPushButton("▶ Nghe thử", self)
        self._btn_listen.setToolTip("Phát đoạn mẫu đã cắt")
        self._btn_listen.clicked.connect(self._listen)
        self._btn_try = QPushButton("🔊 Đọc thử", self)
        self._btn_try.setToolTip(f"Đọc “{SAMPLE_SENTENCE}” bằng giọng mới")
        self._btn_try.clicked.connect(self._try_voice)
        btns.addWidget(self._btn_listen); btns.addWidget(self._btn_try); btns.addStretch(1)
        lay.addLayout(btns)

        self._lbl_status = QLabel("", self)
        self._lbl_status.setWordWrap(True)
        lay.addWidget(self._lbl_status)

        note = QLabel("Dùng 5–8 giây rõ tiếng, một người nói, không nhạc nền", self)
        note.setStyleSheet(f"color: {_DIM}; font-size: 11px;")
        lay.addWidget(note)

        row = QHBoxLayout(); row.addStretch(1)
        btn_cancel = QPushButton("Hủy", self)
        btn_cancel.clicked.connect(self.reject)
        self._btn_save = QPushButton("Lưu", self)
        self._btn_save.setDefault(True)
        self._btn_save.clicked.connect(self._save)
        row.addWidget(btn_cancel); row.addWidget(self._btn_save)
        lay.addLayout(row)
        self._refresh()

    # ── File ──────────────────────────────────────────────────────────────────

    def _browse(self):
        f, _ = QFileDialog.getOpenFileName(self, "Chọn đoạn ghi âm giọng nói", str(Path.home()),
                                           _FILE_FILTER)
        if f:
            self.set_source(Path(f))

    def set_source(self, path: Path) -> None:
        self._src = path
        self._edit_file.setText(path.name)
        self._duration = probe_duration(path)
        self._lbl_dur.setText(f"{self._duration:.1f} s" if self._duration else "không đọc được")
        long_clip = self._duration > vl.MAX_REF_S
        self._spin_start.setEnabled(long_clip)
        self._spin_start.setRange(0.0, max(0.0, self._duration - 1.0))
        self._spin_start.setValue(0.0)
        if 0 < self._duration < vl.MIN_REF_S:
            self._status("Đoạn quá ngắn, giọng sẽ kém giống", _AMBER)
        else:
            self._status("")
        if not self._edit_name.text().strip():
            self._edit_name.setText(path.stem[:40])
        self._refresh()

    def _cut(self) -> Path:
        ref = Path(self._tmp.name) / f"ref_{self._spin_start.value():.2f}.wav"
        if not ref.exists():
            vl.prepare_reference(self._src, ref, self._spin_start.value())
        return ref

    # ── Listen / try ──────────────────────────────────────────────────────────

    def _listen(self):
        try:
            self._play(self._cut())
        except Exception as exc:
            self._status(str(exc), _RED)

    def _try_voice(self):
        try:
            ref = self._cut()
        except Exception as exc:
            self._status(str(exc), _RED)
            return
        out = Path(self._tmp.name) / f"try_{ref.stem}.wav"

        def work():
            from dubber.core.tts.vieneu_tts import sdk_lock, shared_vieneu, write_wav
            sdk = shared_vieneu()
            with sdk_lock:
                emb, codes = sdk.encode_reference(str(ref))
                audio = sdk.infer(SAMPLE_SENTENCE, voice={"speaker_emb": emb, "codes": codes})
            write_wav(out, audio, sdk.sample_rate)
            return out

        self._status("Đang tạo giọng đọc thử… (lần đầu có thể mất một lúc để tải model)")
        self._run(work, self._play)

    def _play(self, path: Path):
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        except ImportError:
            self._status(f"Không phát được âm thanh — file ở: {path}", _AMBER)
            return
        if self._player is None:
            self._player = QMediaPlayer(self)
            self._audio_out = QAudioOutput(self)
            self._player.setAudioOutput(self._audio_out)
        self._player.stop()
        self._player.setSource(QUrl.fromLocalFile(str(path)))
        self._player.play()
        self._status("")

    # ── Save ──────────────────────────────────────────────────────────────────

    def _save(self):
        name = self._edit_name.text().strip()
        src, start = self._src, self._spin_start.value()

        def work():
            from dubber.core.tts.vieneu_tts import sdk_lock, shared_vieneu
            sdk = shared_vieneu()
            with sdk_lock:
                return vl.add_clone(src, name, sdk, start_s=start)

        def done(entry):
            self.entry = entry
            self.accept()

        self._status("Đang lưu giọng…")
        self._run(work, done)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _run(self, fn, on_done):
        self._set_busy(True)
        self._on_task_done = on_done
        thread, task = QThread(self), _Task(fn)
        task.moveToThread(thread)
        thread.started.connect(task.run)
        # Bound methods of this dialog → Qt queues the calls onto the GUI thread
        # (a plain function or lambda would run on the worker thread).
        task.done.connect(self._task_done)
        task.failed.connect(self._task_failed)
        task.done.connect(thread.quit)
        task.failed.connect(thread.quit)
        self._threads.append((thread, task))
        thread.start()

    def _task_done(self, result):
        self._set_busy(False)
        self._on_task_done(result)

    def _task_failed(self, msg: str):
        self._set_busy(False)
        self._status(msg, _RED)

    def _set_busy(self, busy: bool):
        self._busy = busy
        self._refresh()

    def _refresh(self):
        busy = getattr(self, "_busy", False)
        has_src = self._src is not None and self._duration > 0
        self._btn_listen.setEnabled(has_src and not busy)
        self._btn_try.setEnabled(has_src and not busy)
        self._btn_save.setEnabled(has_src and bool(self._edit_name.text().strip()) and not busy)

    def _status(self, text: str, color: str = _DIM):
        self._lbl_status.setText(text)
        self._lbl_status.setStyleSheet(f"color: {color}; font-size: 12px;")

    def reject(self):
        if getattr(self, "_busy", False):   # a running thread must finish first
            self._status("Đang xử lý, vui lòng đợi…", _AMBER)
            return
        super().reject()

    def closeEvent(self, event):
        if getattr(self, "_busy", False):
            event.ignore()
            return
        super().closeEvent(event)

    def done(self, result):   # QDialog.done (accept / reject)
        if self._player is not None:
            self._player.stop()
        # Only reached when idle (reject is blocked while busy; accept comes from a
        # finished task), so these threads are just winding down.
        for thread, _ in list(self._threads):
            thread.quit()
            thread.wait(5000)
        super().done(result)
