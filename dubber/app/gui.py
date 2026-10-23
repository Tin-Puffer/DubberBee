"""M6: PySide6 GUI for Dubber Bee — drag-and-drop video, live progress bars."""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QObject, QSize, QTimer, QItemSelectionModel
from PySide6.QtGui import (
    QColor, QPalette, QDragEnterEvent, QDropEvent, QFont, QFontDatabase, QPixmap,
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QProgressBar, QFileDialog,
    QFrame, QSizePolicy, QCheckBox, QComboBox,
    QGroupBox, QSlider, QDoubleSpinBox, QSpinBox,
    QListWidget, QListWidgetItem, QLineEdit, QAbstractItemView, QDialog,
    QInputDialog, QMenu, QMessageBox,
    QTabWidget, QFormLayout, QScrollBar,
)

from dubber.app.tasks import BusyBar, TaskRunner

_DARK_BG   = "#111"
_PANEL_BG  = "#181818"
_SURFACE   = "#1e1e1e"
_BORDER    = "#2a2a2a"
_ACCENT    = "#4db8ff"
_BEE       = "#FFB300"   # brand amber (logo)
_GREEN     = "#4caf50"
_AMBER     = "#ffb74d"
_RED       = "#f44336"
_TXT_DIM   = "#7d7d7d"   # hints / secondary text: readable on the dark bg
_TXT_MID   = "#888"
_TXT_MAIN  = "#ccc"
_TXT_BRIGHT= "#eee"

_BASE_STYLE = f"""
QWidget                   {{ background: {_DARK_BG}; color: {_TXT_MAIN}; }}
QGroupBox                 {{ background: {_PANEL_BG}; border: 1px solid {_BORDER};
                             border-radius: 8px; margin-top: 8px;
                             font-size: 10px; color: {_TXT_DIM}; }}
QGroupBox::title          {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
QCheckBox                 {{ color: {_TXT_MAIN}; font-size: 13px; spacing: 6px; }}
QCheckBox::indicator      {{ width: 15px; height: 15px; border-radius: 3px;
                             border: 1px solid #484848; background: #222; }}
QCheckBox::indicator:checked  {{ background: {_ACCENT}; border-color: {_ACCENT}; }}
QCheckBox::indicator:disabled {{ background: #1e1e1e; border-color: #2e2e2e; }}
QComboBox                 {{ background: {_SURFACE}; border: 1px solid #3a3a3a;
                             border-radius: 5px; padding: 4px 10px;
                             font-size: 12px; color: {_TXT_MAIN}; min-height: 26px; }}
QComboBox::drop-down      {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{ background: {_SURFACE}; border: 1px solid #3a3a3a;
                               selection-background-color: #2a2a2a; color: {_TXT_MAIN}; }}
QSlider::groove:horizontal {{ background: #2a2a2a; height: 4px; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {_ACCENT}; width: 14px; height: 14px;
                               margin: -5px 0; border-radius: 7px; }}
QSlider::sub-page:horizontal {{ background: {_ACCENT}; border-radius: 2px; }}
QSlider::groove:horizontal:disabled {{ background: #222; }}
QSlider::handle:horizontal:disabled {{ background: #3a3a3a; }}
QSlider::sub-page:horizontal:disabled {{ background: #2a2a2a; }}
QDoubleSpinBox            {{ background: {_SURFACE}; border: 1px solid #3a3a3a;
                             border-radius: 4px; padding: 2px 4px;
                             font-size: 12px; color: {_TXT_MAIN}; min-height: 22px; }}
QDoubleSpinBox:disabled   {{ color: {_TXT_DIM}; border-color: {_BORDER}; }}
QLabel, QCheckBox, QRadioButton, QSlider {{ background: transparent; }}
QLabel                    {{ font-size: 13px; }}
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {{ min-height: 28px; }}
QSpinBox                  {{ background: {_SURFACE}; border: 1px solid #3a3a3a; border-radius: 5px;
                             padding: 2px 6px; font-size: 12px; color: {_TXT_MAIN}; }}
QSpinBox:disabled         {{ color: {_TXT_DIM}; border-color: {_BORDER}; }}
QDoubleSpinBox            {{ border-radius: 5px; padding: 2px 6px; }}
QComboBox:disabled        {{ color: {_TXT_DIM}; border-color: {_BORDER}; }}
QTabWidget::pane          {{ border: 1px solid {_BORDER}; border-radius: 8px; background: {_PANEL_BG};
                             top: -1px; }}
QTabBar                   {{ background: transparent; }}
QTabBar::tab              {{ background: transparent; color: {_TXT_MID}; padding: 9px 16px;
                             border: none; border-bottom: 2px solid transparent;
                             font-size: 13px; margin-right: 2px; }}
QTabBar::tab:selected     {{ color: {_TXT_BRIGHT}; border-bottom: 2px solid {_ACCENT}; }}
QTabBar::tab:hover:!selected {{ color: {_TXT_MAIN}; }}
QTabBar::tab:disabled     {{ color: {_TXT_DIM}; }}
QGroupBox                 {{ font-size: 11px; color: {_TXT_MID}; font-weight: 600; padding-top: 6px; }}
"""


log = logging.getLogger(__name__)


_file_log_handler = None


def _setup_file_logging() -> Path:
    """Log to <logs dir>/dubber.log — the only log a Finder-launched .app has.

    Called again after the logs folder is changed: swaps the file handler."""
    global _file_log_handler
    from logging.handlers import RotatingFileHandler
    from dubber.core.utils.paths import logs_root
    log_dir = logs_root()
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "dubber.log"
    handler = RotatingFileHandler(path, maxBytes=5_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root = logging.getLogger()
    if _file_log_handler is not None:
        root.removeHandler(_file_log_handler)
        _file_log_handler.close()
    _file_log_handler = handler
    root.addHandler(handler)
    if root.level > logging.INFO or root.level == logging.NOTSET:
        root.setLevel(logging.INFO)
    return path


# ── Worker thread ──────────────────────────────────────────────────────────────

class PipelineWorker(QObject):
    step_started  = Signal(str)
    step_progress = Signal(str, int)
    step_done     = Signal(str)
    finished      = Signal(Path)
    error         = Signal(str)
    cancelled     = Signal()
    error_report  = Signal(str, str)        # (full report text, report file) — before error
    ask           = Signal(str, object)   # (kind, payload) — GUI answers via answer()

    def __init__(
        self,
        input_path: Path,
        vi_volume: float = 1.0,
        music_volume: float = 0.80,
        cn_voice_volume: float = 0.50,
        show_subtitles: bool = True,
        voice: str | None = None,
        keep_music: bool = True,
        keep_cn_voice: bool = True,
        trim_start: float = 0.0,
        trim_end: "float | None" = None,
        translator: str = "auto",
        scale: "str | None" = None,
        fps: "int | None" = None,
        crf: int = 23,
        encoder_preset: str = "medium",
        audio_bitrate: str = "192k",
        output_dir: "Path | None" = None,
        blur_mode: str = "auto",
        blur_rect: "tuple | None" = None,
        sub_pos: "tuple | None" = None,
        sub_font: str = "Arial",
        sub_size: int = 48,
        sub_color: str = "#FFFFFF",
        sub_outline_color: str = "#000000",
        sub_outline: float = 2.0,
        sub_box: bool = False,
        blur_pad: int = 12,
        asr_on_vocals: bool = True,
        tts_engine: str = "piper",
        skip_review: bool = True,
    ) -> None:
        super().__init__()
        import threading

        from dubber.core.utils.cancel import CancelToken
        self._cancel = CancelToken()
        self._answer_ready = threading.Event()
        self._answer = None
        self._created: list = []   # files made outside the job dir (deleted on cancel)
        self.asr_on_vocals   = asr_on_vocals
        self.tts_engine      = tts_engine
        self.skip_review     = skip_review
        self.sub_font        = sub_font
        self.sub_size        = sub_size
        self.sub_color       = sub_color
        self.sub_outline_color = sub_outline_color
        self.sub_outline     = sub_outline
        self.sub_box         = sub_box
        self.blur_pad        = blur_pad
        self.input_path      = input_path
        self.output_dir      = output_dir
        self.blur_mode       = blur_mode
        self.blur_rect       = blur_rect
        self.sub_pos         = sub_pos
        self.vi_volume       = vi_volume
        self.music_volume    = music_volume
        self.cn_voice_volume = cn_voice_volume
        self.show_subtitles  = show_subtitles
        self.voice           = voice
        self.keep_music      = keep_music
        self.keep_cn_voice   = keep_cn_voice
        self.trim_start      = trim_start
        self.trim_end        = trim_end
        self.translator      = translator
        self.scale           = scale
        self.fps             = fps
        self.crf             = crf
        self.encoder_preset  = encoder_preset
        self.audio_bitrate   = audio_bitrate

    def cancel(self) -> None:
        """Stop at the next checkpoint and delete what this job created (thread-safe)."""
        self._cancel.cancel()
        self._answer_ready.set()   # release a pending question

    def answer(self, value) -> None:
        """GUI's reply to an ask() (thread-safe)."""
        self._answer = value
        self._answer_ready.set()

    def _ask(self, kind: str, payload: dict):
        """Block this worker until the GUI answers (or the job is cancelled)."""
        self._answer, _ = None, self._answer_ready.clear()
        self.ask.emit(kind, payload)
        self._answer_ready.wait()
        self._cancel.check()
        return self._answer

    def _settings(self) -> dict:
        """What this job was run with (for error reports)."""
        keys = ("translator", "skip_review", "tts_engine", "voice", "asr_on_vocals",
                "trim_start", "trim_end",
                "vi_volume", "music_volume", "cn_voice_volume", "keep_music", "keep_cn_voice",
                "show_subtitles", "scale", "fps", "crf", "encoder_preset", "audio_bitrate",
                "blur_mode", "blur_pad", "sub_font", "sub_size", "sub_color",
                "sub_outline_color", "sub_outline", "sub_box", "output_dir")
        return {k: getattr(self, k, None) for k in keys}

    def _report_error(self, exc: BaseException) -> None:
        """Save an error report file and send its text to the GUI pop-up."""
        import traceback
        from dubber.core.utils.error_report import build_report, write_error_report
        tb = "".join(traceback.format_exception(exc))
        try:
            path = write_error_report(self.input_path, str(exc), tb, self._settings())
            text = path.read_text(encoding="utf-8")
        except Exception:   # never let reporting hide the real error
            log.exception("Could not write the error report")
            path, text = "", build_report(self.input_path, str(exc), tb, self._settings())
        self.error_report.emit(text, str(path))

    def _review(self, rows: list, job_dir: Path, audio: Path) -> list:
        """Wait for the user to check / edit the translation (list of Vietnamese texts)."""
        answer = self._ask("review", {"video": self.input_path.name, "rows": rows,
                                      "audio": audio, "work_dir": job_dir / "review",
                                      "tts_engine": self.tts_engine, "voice": self.voice})
        if answer is None:
            from dubber.core.utils.cancel import JobCancelled
            raise JobCancelled("Đã hủy")
        return answer

    @staticmethod
    def _write_tiktok_txt(translate_json, video: Path) -> None:
        """<video>.txt beside the video: the TikTok prompt + this video's script, to paste
        into ChatGPT. Never fails the job (the video is already saved)."""
        if not translate_json or not Path(translate_json).exists():
            return
        from dubber.core.translate.tiktok import write_tiktok_txt
        try:
            write_tiktok_txt(Path(translate_json), video.with_suffix(".txt"))
        except Exception:
            log.exception("Could not write the TikTok prompt next to %s", video.name)

    def run(self) -> None:
        import shutil

        from dubber.core.utils.cancel import JobCancelled
        job_dir = None
        cancelled = False
        check = self._cancel.check
        try:
            import shutil
            import subprocess as _sp

            from dubber.core.utils.ffmpeg_locator import find_ffmpeg, find_ffprobe, no_window
            from dubber.core.utils.paths import prepare_job_dir
            from dubber.core.pipeline import Pipeline

            ffmpeg  = find_ffmpeg()
            ffprobe = find_ffprobe()
            if self.tts_engine == "vieneu" and (self.voice or "").startswith("clone:"):
                from dubber.core.tts.voice_library import clone_exists
                if not clone_exists(self.voice.split(":", 1)[1]):
                    raise RuntimeError("Giọng clone của video này đã bị xóa — hãy chọn giọng khác")
            # The job dir is kept between runs of the same file + trim range, so a rerun
            # with another translator / voice reuses ASR (and translation); a different
            # source or trim starts empty (a 20 s trim's ASR must never serve the full video).
            trimming = self.trim_start > 0 or self.trim_end is not None
            if trimming:
                check_trim(self.input_path, self.trim_start, self.trim_end)
            job_dir = prepare_job_dir(
                self.input_path, trim=(self.trim_start, self.trim_end) if trimming else None)

            # ── Video trim ────────────────────────────────────────────────────
            input_path = self.input_path
            if trimming:
                trimmed = job_dir / "trimmed.mp4"
                if not trimmed.exists():
                    part = job_dir / "trimmed.part.mp4"
                    cmd = [str(ffmpeg), "-y", "-i", str(self.input_path),
                           "-ss", str(self.trim_start)]
                    if self.trim_end is not None:
                        cmd += ["-to", str(self.trim_end)]
                    cmd += ["-c", "copy", str(part)]
                    result = _sp.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **no_window())
                    if result.returncode != 0:
                        raise RuntimeError(
                            f"FFmpeg không cắt được video:\n{result.stderr[-2000:]}"
                        )
                    part.replace(trimmed)
                input_path = trimmed

            check()
            pipeline = Pipeline(ffmpeg=ffmpeg, ffprobe=ffprobe, job_dir=job_dir)
            pipeline.cancel_check = check
            pipeline.on_rate_limit = lambda info: self._ask("rate_limit", info) or "continue"
            if not self.skip_review:
                pipeline.review_translation = lambda rows: self._review(
                    rows, job_dir, job_dir / "audio.wav")

            orig_m1 = pipeline.run_m1
            orig_m2 = pipeline.run_m2
            orig_m3 = pipeline.run_m3
            orig_m4 = pipeline.run_m4

            asr_started = [False]

            def _on_asr(_pct: float) -> None:
                # emitted by run_m1 right before recognition (after vocal separation)
                check()
                asr_started[0] = True
                self.step_started.emit("ASR")

            def _m1(p, **kw):
                check()
                r = orig_m1(p, **kw)
                check()
                if not asr_started[0]:          # cached asr.json — no callback came
                    self.step_started.emit("ASR")
                self.step_done.emit("ASR"); return r

            def _m2(p, **kw):
                self.step_started.emit("Translate")
                r = orig_m2(p, **kw); check(); self.step_done.emit("Translate"); return r

            def _m3(p, **kw):
                self.step_started.emit("TTS")
                r = orig_m3(p, **kw); check(); self.step_done.emit("TTS"); return r

            def _m4(p, **kw):
                self.step_started.emit("Mux")
                r = orig_m4(p, **kw); check(); self.step_done.emit("Mux"); return r

            pipeline.run_m1 = _m1
            pipeline.run_m2 = _m2
            pipeline.run_m3 = _m3
            pipeline.run_m4 = _m4

            def _on_render(pct: float) -> None:
                check()   # raising here kills the FFmpeg render
                if pct == 0.0:
                    self.step_started.emit("Render")
                self.step_progress.emit("Render", int(pct * 100))
                if pct >= 1.0:
                    self.step_done.emit("Render")

            def _on_separate(pct: float) -> None:
                check()
                if pct == 0.0:
                    self.step_started.emit("Separate")
                self.step_progress.emit("Separate", int(pct * 100))
                if pct >= 1.0:
                    self.step_done.emit("Separate")

            _, translate_json, _, _, output_mp4 = pipeline.run_m5(
                input_path,
                vi_volume=self.vi_volume,
                music_volume=self.music_volume,
                cn_voice_volume=self.cn_voice_volume,
                show_subtitles=self.show_subtitles,
                blur_mode=self.blur_mode,
                blur_rect=self.blur_rect,
                sub_pos=self.sub_pos,
                sub_font=self.sub_font,
                sub_size=self.sub_size,
                sub_color=self.sub_color,
                sub_outline_color=self.sub_outline_color,
                sub_outline=self.sub_outline,
                sub_box=self.sub_box,
                blur_pad=self.blur_pad,
                voice=self.voice,
                keep_music=self.keep_music,
                keep_cn_voice=self.keep_cn_voice,
                translator=self.translator,
                scale=self.scale,
                fps=self.fps,
                crf=self.crf,
                encoder_preset=self.encoder_preset,
                audio_bitrate=self.audio_bitrate,
                on_progress={"render": _on_render, "separate": _on_separate, "asr": _on_asr,
                             "tts": lambda pct: self.step_progress.emit("TTS", int(pct * 100))},
                asr_on_vocals=self.asr_on_vocals,
                tts_engine=self.tts_engine,
            )

            # ── Copy result out of the job cache to the user's output folder ──
            from dubber.core.utils.settings import resolve_output_path
            dest = resolve_output_path(self.input_path, self.output_dir)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(output_mp4, dest)
            self._write_tiktok_txt(translate_json, dest)
            self.finished.emit(dest)

        except JobCancelled:
            cancelled = True
            log.info("Job cancelled: %s", self.input_path.name)
        except Exception as exc:
            if self._cancel.cancelled:   # e.g. FFmpeg killed mid-render
                cancelled = True
            else:
                log.exception("Job failed: %s", self.input_path.name)
                self._report_error(exc)
                self.error.emit(str(exc))
        finally:
            if cancelled:
                # Cancel = stop and leave nothing behind (job cache included)
                if job_dir is not None:
                    shutil.rmtree(job_dir, ignore_errors=True)
                for f in self._created:
                    Path(f).unlink(missing_ok=True)
                self.cancelled.emit()
            # Nothing is deleted automatically after a run: job caches stay until the user
            # empties them ("Dọn ngay").


def check_trim(video: Path, start: float, end: "float | None") -> None:
    """Raise a plain-Vietnamese error for a trim range FFmpeg would reject."""
    from dubber.core.video.info import probe_info
    try:
        duration = probe_info(video).duration
    except Exception:   # unreadable here → FFmpeg will report it; still check start < end
        duration = 0.0
    if end is None and not duration:
        return
    stop = end if end is not None else duration
    if duration and start >= duration:
        raise RuntimeError(f"Đoạn cắt không hợp lệ: bắt đầu {start:g}s nằm ngoài video "
                           f"(video dài {duration:.0f}s)")
    if stop <= start:
        raise RuntimeError(f"Đoạn cắt không hợp lệ: bắt đầu {start:g}s phải nhỏ hơn "
                           f"kết thúc {stop:g}s")


# ── Drop zone ──────────────────────────────────────────────────────────────────

_VIDEO_EXTS = (".mp4", ".mkv", ".mov", ".avi", ".webm")


class DropZone(QFrame):
    files_chosen = Signal(list)  # list[Path]

    _IDLE  = (f"QFrame {{ border: 2px dashed #333; border-radius: 10px;"
               f" background: {_PANEL_BG}; }}")
    _HOVER = (f"QFrame {{ border: 2px dashed {_ACCENT}; border-radius: 10px;"
               f" background: #162030; }}")
    _READY = (f"QFrame {{ border: 2px solid #333; border-radius: 10px;"
               f" background: {_PANEL_BG}; }}")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setStyleSheet(self._IDLE)
        self.setMinimumHeight(72)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(12)

        self._icon = QLabel("🎬", self)
        self._icon.setFixedWidth(28)
        self._icon.setAlignment(Qt.AlignCenter)
        self._icon.setStyleSheet("font-size: 22px; border: none; background: transparent;")

        self._label = QLabel("Kéo thả video vào đây (nhiều file)  ·  hoặc bấm để chọn", self)
        self._label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self._label.setStyleSheet(f"color: {_TXT_DIM}; font-size: 13px; border: none; background: transparent;")

        lay.addWidget(self._icon)
        lay.addWidget(self._label, 1)

    @staticmethod
    def _videos(event) -> list[Path]:
        if not event.mimeData().hasUrls():
            return []
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls()]
        return [p for p in paths if p.suffix.lower() in _VIDEO_EXTS]

    def dragEnterEvent(self, event: QDragEnterEvent):
        if self._videos(event):
            self.setStyleSheet(self._HOVER)
            event.acceptProposedAction(); return
        event.ignore()

    def dragLeaveEvent(self, event):
        self.setStyleSheet(self._IDLE)

    def dropEvent(self, event: QDropEvent):
        self.setStyleSheet(self._IDLE)
        paths = self._videos(event)
        if paths:
            self.files_chosen.emit(paths)
        event.acceptProposedAction()

    def set_compact(self, compact: bool) -> None:
        """Once videos are queued the zone shrinks to a one-line "add more" bar."""
        self.setMinimumHeight(40 if compact else 72)
        self.setMaximumHeight(40 if compact else 16777215)
        self._icon.setText("＋" if compact else "🎬")
        self._label.setText("Thêm video — kéo thả hoặc bấm để chọn" if compact
                            else "Kéo thả video vào đây (nhiều file)  ·  hoặc bấm để chọn")

    def mousePressEvent(self, event):
        ps, _ = QFileDialog.getOpenFileNames(
            self, "Chọn video", str(Path.home()),
            "Video (*.mp4 *.mkv *.mov *.avi *.webm)",
        )
        if ps: self.files_chosen.emit([Path(p) for p in ps])


APP_NAME = "Dubber Bee"
FILE_MANAGER = "Explorer" if sys.platform == "win32" else "Finder"


class _NoWheelChange(QObject):
    """App-wide: the mouse wheel never changes a combo box, number box or slider (too easy
    to hit while scrolling the page) — the wheel goes to the scrollable page instead."""

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        from PySide6.QtWidgets import QAbstractSlider, QAbstractSpinBox
        if event.type() == QEvent.Wheel and isinstance(
                obj, (QComboBox, QAbstractSpinBox, QAbstractSlider)) \
                and not isinstance(obj, QScrollBar):
            parent = obj.parentWidget()
            if parent is not None:
                QApplication.sendEvent(parent, event)   # bubbles up to the scroll area
            return True
        return False


_no_wheel = None


def disable_wheel_on_inputs(app) -> None:
    global _no_wheel
    if _no_wheel is None:
        _no_wheel = _NoWheelChange(app)
        app.installEventFilter(_no_wheel)
_LOGO_DIR = Path(__file__).resolve().parents[1] / "assets" / "logo"


def app_logo(size: int) -> QPixmap:
    """The bee logo at *size* logical px (sharp on Retina)."""
    from PySide6.QtGui import QGuiApplication
    ratio = QGuiApplication.primaryScreen().devicePixelRatio() if QGuiApplication.primaryScreen() else 1.0
    src = _LOGO_DIR / ("logo_256.png" if size * ratio > 64 else "logo_64.png")
    pix = QPixmap(str(src))
    if pix.isNull():
        return pix
    pix = pix.scaled(round(size * ratio), round(size * ratio), Qt.KeepAspectRatio,
                     Qt.SmoothTransformation)
    pix.setDevicePixelRatio(ratio)
    return pix


# ── Small helpers ──────────────────────────────────────────────────────────────

_SMALL_BTN_STYLE = (
    f"QPushButton {{ background: {_SURFACE}; color: {_TXT_MID}; border: 1px solid {_BORDER};"
    " border-radius: 5px; font-size: 12px; padding: 0 10px; }"
    f"QPushButton:hover {{ background: #2a2a2a; color: {_TXT_MAIN}; }}"
    " QPushButton:disabled { background: #161616; color: #333; border-color: #1e1e1e; }"
)


_qt_fonts_registered = False


def register_qt_fonts() -> None:
    """Make the bundled subtitle fonts usable by Qt (combo preview, frame editor)."""
    global _qt_fonts_registered
    if _qt_fonts_registered:
        return
    from dubber.core.video.fonts import fonts_dir
    for ttf in sorted(fonts_dir().glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(ttf))
    _qt_fonts_registered = True


def _form_page(parent) -> "tuple[QWidget, QFormLayout]":
    """A tab page with an aligned label/field form."""
    page = QWidget(parent)
    form = QFormLayout(page)
    form.setContentsMargins(18, 18, 18, 14)
    form.setHorizontalSpacing(18)
    form.setVerticalSpacing(12)
    form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    form.setRowWrapPolicy(QFormLayout.DontWrapRows)
    return page, form


def _scrollable(page: QWidget) -> QWidget:
    """Wrap a tab page so it scrolls instead of squeezing rows when it is taller than the tab."""
    from PySide6.QtWidgets import QScrollArea
    area = QScrollArea()
    area.setWidget(page)
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.setStyleSheet(
        "QScrollArea { background: transparent; }"
        "QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }"
        "QScrollBar::handle:vertical { background: #3a3a3a; border-radius: 3px; min-height: 30px; }"
        "QScrollBar::handle:vertical:hover { background: #4a4a4a; }"
        "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }"
    )
    area.viewport().setAutoFillBackground(False)
    page.setAutoFillBackground(False)
    return area


def _finish_form(form: "QFormLayout") -> None:
    """Make every label as tall as a field and centre it, so rows line up."""
    for i in range(form.rowCount()):
        item = form.itemAt(i, QFormLayout.LabelRole)
        w = item.widget() if item else None
        field = form.itemAt(i, QFormLayout.FieldRole)
        if field and isinstance(field.widget(), QCheckBox):
            field.widget().setMinimumHeight(30)  # same row height as combo rows
        if w is not None:
            w.setMinimumHeight(30)
            if isinstance(w, QLabel):
                w.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                if not w.styleSheet():
                    w.setStyleSheet(f"color: {_TXT_MID};")


def _hbox(*widgets, stretch_first: bool = True, stretch_last: bool = True) -> QWidget:
    """Row of widgets. Default: first widget fills; stretch_last=False: pack left."""
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(10)
    for i, w in enumerate(widgets):
        lay.addWidget(w, 1 if (i == 0 and stretch_first and stretch_last) else 0)
    if not stretch_last:
        lay.addStretch(1)
    return box


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def _hint_label(parent, text: str = "") -> QLabel:
    lbl = QLabel(text, parent)
    lbl.setWordWrap(True)
    lbl.setStyleSheet(f"color: {_TXT_DIM}; font-size: 12px;")
    return lbl


# One short line under each option: what you gain / lose (also the widget's tooltip)
_HINTS_ENGINE = {
    "piper":  "Nhanh, giọng đều",
    "vieneu": "Giọng tự nhiên hơn, chậm hơn · hỗ trợ voice clone (lần đầu tải ~610 MB)",
}
_HINT_CLONE_ITEM = "Dùng 5–8 giây ghi âm rõ, một người nói"
_HINTS_TRANSLATOR = {
    "auto":  "Dùng Groq; mất mạng hoặc lỗi thì tự chuyển sang MarianMT (offline)",
    "groq":  "Chất lượng tốt nhất · cần Internet và Groq API key (⚙ Cài đặt)",
    "local": "Offline, nhanh, chất lượng thấp hơn",
    "nllb":  "Offline, chất lượng khá hơn MarianMT, chậm · tải ~5,5 GB, cần ~6 GB RAM",
}
_HINT_ASR_VOCALS = "Nhận dạng chính xác hơn với video có nhạc nền, chậm hơn một chút"
_HINT_TRIM = "Chỉ xử lý đoạn đã chọn — nhanh hơn khi chạy thử"
_HINT_REVIEW = ("Bỏ chọn để dừng sau bước dịch: xem / sửa bản dịch (hoặc nhờ AI sửa) trước khi "
                "tạo giọng Việt. Nên giữ chọn khi chạy video dài.")
_HINT_SUBS = "Phụ đề cứng (burn-in) — luôn hiện, không tắt được trên video"
_HINT_OUTLINE_COLOR = "Viền tối + chữ sáng dễ đọc nhất trên mọi nền"
_HINT_OUTLINE = "0 = không viền · viền quá dày làm chữ nặng, khó đọc"
_HINT_BOX = "Hộp nền tối sau chữ — dễ đọc nhưng che nhiều khung hình hơn"
_HINTS_BLUR = {
    "off":    "Không che — giữ nguyên khung hình gốc",
    "auto":   "Vùng làm mờ ôm sát dòng phụ đề dài nhất, hiện suốt video",
    "custom": "Tự chọn vùng che phụ đề tiếng Trung trên khung hình",
}
_HINT_BLUR_PAD = "Lớn hơn: che kín phụ đề gốc hơn nhưng che nhiều khung hình hơn"
DEFAULT_SUB_COLOR, DEFAULT_OUTLINE_COLOR = "#FFFFFF", "#000000"
DEFAULT_OUTLINE, DEFAULT_BLUR_PAD = 2, 12
_HINTS_RES = {None: "Giữ nguyên như video gốc"}
_HINT_RES_OTHER = "Thấp hơn = file nhỏ hơn, render nhanh hơn"
_HINT_FPS = "Giữ “Gốc” nếu không cần đổi"
_HINT_CRF = "CRF thấp = nét hơn, file lớn hơn"
_HINT_PRESET = "Nhanh = render nhanh, file lớn hơn · Chậm = file nhỏ hơn"
_HINT_AUDIO_BR = "192 kbps là đủ cho giọng nói"


def _add_hint(form: "QFormLayout", widget: QWidget, hints, default: str = "") -> QLabel:
    """Add a one-line note under *widget* and use it as the tooltip too.

    *hints* is a fixed string, or {userData: text} for a combo — the note then follows
    the current choice (*default* for values not in the dict).
    """
    lbl = _hint_label(widget.parentWidget())
    form.addRow("", lbl)

    def update(*_):
        text = hints if isinstance(hints, str) else hints.get(widget.currentData(), default)
        lbl.setText(text)
        lbl.setVisible(bool(text))
        widget.setToolTip(text)

    if isinstance(widget, QComboBox) and not isinstance(hints, str):
        widget.currentIndexChanged.connect(update)
    update()
    return lbl


def _small_button(text: str, parent) -> QPushButton:
    b = QPushButton(text, parent)
    b.setFixedHeight(30)
    b.setStyleSheet(_SMALL_BTN_STYLE)
    return b


class _ColorButton(QPushButton):
    """Shows a color swatch + hex; click → QColorDialog."""
    changed = Signal(str)

    def __init__(self, color: str, title: str, parent=None):
        super().__init__(parent)
        self._title = title
        self.setFixedSize(120, 30)
        self.clicked.connect(self._pick)
        self.set_color(color)

    def color(self) -> str:
        return self._color

    def set_color(self, color: str) -> None:
        self._color = QColor(color).name().upper()
        light = QColor(self._color).lightness() > 140
        self.setText(self._color)
        self.setStyleSheet(
            f"QPushButton {{ background: {self._color}; color: {'#111' if light else '#eee'};"
            f" border: 1px solid {_BORDER}; border-radius: 5px; font-size: 12px; }}")

    def _pick(self):
        from PySide6.QtWidgets import QColorDialog
        c = QColorDialog.getColor(QColor(self._color), self, self._title)
        if c.isValid():
            self.set_color(c.name())
            self.changed.emit(self._color)


def _select_data(combo: QComboBox, data) -> None:
    """Select the combo item whose userData == data (no-op if absent)."""
    # Loop instead of findData(): findData(None) doesn't match items stored with None
    for i in range(combo.count()):
        if combo.itemData(i) == data:
            combo.setCurrentIndex(i)
            return


# ── Volume row helper ──────────────────────────────────────────────────────────

def _make_vol_slider(parent, default: int = 20, max_val: int = 100) -> tuple:
    """Return (slider, label) pair."""
    sld = QSlider(Qt.Horizontal, parent)
    sld.setRange(0, max_val)
    sld.setValue(default)
    lbl = QLabel(f"{default}%", parent)
    lbl.setFixedWidth(38)
    lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    lbl.setStyleSheet(f"color: {_TXT_MID}; font-size: 11px;")
    sld.valueChanged.connect(lambda v: lbl.setText(f"{v}%"))
    return sld, lbl


# ── Options panel ──────────────────────────────────────────────────────────────

class OptionsPanel(QWidget):
    sep_label_changed = Signal()  # emitted when sliders/checkboxes change
    layout_edit_requested = Signal(str)  # "blur" | "sub" — open the frame editor

    def __init__(self, parent=None):
        super().__init__(parent)

        from dubber.core.asr.model_manager import PIPER_MODEL_NAME, list_piper_models
        from dubber.core.video.fonts import (
            DEFAULT_FONT, DEFAULT_FONT_SIZE, MAX_FONT_SIZE, MIN_FONT_SIZE, SUBTITLE_FONTS,
        )
        self._voices = list_piper_models()
        register_qt_fonts()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._read_only = False
        self._banner = QLabel("🔒  Video đang xử lý — chỉ xem, không chỉnh sửa", self)
        self._banner.setStyleSheet(
            f"color: {_AMBER}; font-size: 11px; padding: 4px 8px;"
            " background: #2a2416; border-radius: 4px;")
        self._banner.hide()
        outer.addWidget(self._banner)
        self._tabs = QTabWidget(self)
        self._tabs.setDocumentMode(False)
        outer.addWidget(self._tabs)

        # ── Tab: Âm thanh ─────────────────────────────────────────────────────
        page_audio, form = _form_page(self)
        self._sld_vi, self._lbl_vi_vol = _make_vol_slider(self, default=100, max_val=200)
        self._sld_vi.setStyleSheet(
            f"QSlider::sub-page:horizontal {{ background: {_GREEN}; border-radius: 2px; }}"
            f"QSlider::handle:horizontal {{ background: {_GREEN}; width: 14px; height: 14px;"
            f" margin: -5px 0; border-radius: 7px; }}"
        )
        self._sld_vi.valueChanged.connect(self._update_vi_label)
        lbl_vi = QLabel("Giọng Việt", self)
        lbl_vi.setStyleSheet(f"color: {_GREEN}; font-weight: 600;")
        form.addRow(lbl_vi, _hbox(self._sld_vi, self._lbl_vi_vol))

        self._chk_music = QCheckBox("Nhạc nền", self)
        self._chk_music.setChecked(True)
        self._chk_music.toggled.connect(self._on_audio_toggled)
        self._sld_music, self._lbl_music_vol = _make_vol_slider(self, default=40)
        self._sld_music.valueChanged.connect(self.sep_label_changed)
        form.addRow(self._chk_music, _hbox(self._sld_music, self._lbl_music_vol))

        self._chk_cn_voice = QCheckBox("Tiếng Trung", self)
        self._chk_cn_voice.setChecked(True)
        self._chk_cn_voice.toggled.connect(self._on_audio_toggled)
        self._sld_cn_voice, self._lbl_cn_vol = _make_vol_slider(self, default=20)
        self._sld_cn_voice.valueChanged.connect(self.sep_label_changed)
        form.addRow(self._chk_cn_voice, _hbox(self._sld_cn_voice, self._lbl_cn_vol))

        self._hint = _hint_label(self)
        form.addRow("", self._hint)
        form.addRow("", _hint_label(self, "100% = giữ nguyên độ to của âm thanh gốc"))
        self._on_audio_toggled()
        _finish_form(form)
        self._tabs.addTab(_scrollable(page_audio), "Âm thanh")

        # ── Tab: Giọng & dịch ─────────────────────────────────────────────────
        page_voice, form = _form_page(self)
        import importlib.util
        self._vieneu_ok = importlib.util.find_spec("vieneu") is not None
        self._combo_engine = QComboBox(self)
        self._combo_engine.addItem("Piper", userData="piper")
        self._combo_engine.addItem("VieNeu-TTS" if self._vieneu_ok else "VieNeu-TTS — chưa cài",
                                   userData="vieneu")
        if not self._vieneu_ok:
            self._combo_engine.model().item(1).setEnabled(False)
            self._combo_engine.setItemData(1, 'Chưa cài VieNeu-TTS: pip install -e ".[vieneu]"',
                                           Qt.ToolTipRole)
        form.addRow("TTS engine", self._combo_engine)
        _add_hint(form, self._combo_engine, _HINTS_ENGINE)

        self._default_piper = PIPER_MODEL_NAME.removesuffix(".onnx")
        self._last_voice = {"piper": self._default_piper, "vieneu": None}
        self._combo_voice = QComboBox(self)
        self._btn_demo = _small_button("▶ Nghe thử", self)
        self._btn_demo.setToolTip("Nghe một câu mẫu bằng giọng đang chọn")
        self._btn_demo.clicked.connect(self._play_demo)
        self._btn_rename_voice = _small_button("✎", self)
        self._btn_rename_voice.setFixedWidth(36)
        self._btn_rename_voice.setToolTip("Đổi tên giọng clone")
        self._btn_rename_voice.clicked.connect(self._rename_voice)
        self._btn_delete_voice = _small_button("🗑", self)
        self._btn_delete_voice.setFixedWidth(36)
        self._btn_delete_voice.setToolTip("Xóa giọng clone")
        self._btn_delete_voice.clicked.connect(self._delete_voice)
        self._demo_player = None
        self._demo_task = None
        form.addRow("Giọng đọc", _hbox(self._combo_voice, self._btn_demo,
                                       self._btn_rename_voice, self._btn_delete_voice))
        self._hint_voice = _hint_label(self)
        form.addRow("", self._hint_voice)
        self._fill_voices("piper")
        self._combo_engine.currentIndexChanged.connect(self._on_engine_changed)
        self._combo_voice.currentIndexChanged.connect(self._on_voice_changed)

        self._combo_translator = QComboBox(self)
        self._combo_translator.addItem("Tự động (Groq → offline)",          userData="auto")
        self._combo_translator.addItem("Groq (online)",                     userData="groq")
        self._combo_translator.addItem("Offline — MarianMT (~300 MB)",      userData="local")
        self._combo_translator.addItem("Offline — NLLB 1.3B (~5.5 GB)",     userData="nllb")
        form.addRow("Dịch", self._combo_translator)
        _add_hint(form, self._combo_translator, _HINTS_TRANSLATOR)

        self._chk_skip_review = QCheckBox("Bỏ qua (chạy tự động, không dừng)", self)
        self._chk_skip_review.setChecked(True)
        form.addRow("Duyệt bản dịch", self._chk_skip_review)
        _add_hint(form, self._chk_skip_review, _HINT_REVIEW)

        import importlib.util
        self._mdxnet = importlib.util.find_spec("audio_separator") is not None
        self._chk_asr_vocals = QCheckBox("Lọc nhạc nền trước khi nhận dạng", self)
        self._chk_asr_vocals.setChecked(self._mdxnet)
        self._chk_asr_vocals.setEnabled(self._mdxnet)
        self._chk_asr_vocals.toggled.connect(self.sep_label_changed)
        form.addRow("Nhận dạng", self._chk_asr_vocals)
        _add_hint(form, self._chk_asr_vocals, _HINT_ASR_VOCALS if self._mdxnet else
                  "Chưa cài audio-separator — nhận dạng trên âm thanh gốc")

        self._chk_trim = QCheckBox("Chỉ xử lý một đoạn", self)
        self._chk_trim.setChecked(False)
        self._chk_trim.toggled.connect(self._on_trim_toggled)
        form.addRow("Trim", self._chk_trim)
        from dubber.app.trim_editor import TrimEditor
        self._trim = TrimEditor(self)
        self._trim.setEnabled(False)
        form.addRow("", self._trim)
        _add_hint(form, self._chk_trim, _HINT_TRIM)
        _finish_form(form)
        self._tabs.addTab(_scrollable(page_voice), "Giọng && dịch")

        # ── Tab: Phụ đề ───────────────────────────────────────────────────────
        page_sub, form = _form_page(self)
        self._blur_rect: "tuple | None" = None
        self._sub_pos: "tuple | None" = None

        self._chk_subs = QCheckBox("Chèn phụ đề tiếng Việt vào video", self)
        self._chk_subs.setChecked(True)
        form.addRow("Phụ đề", self._chk_subs)
        _add_hint(form, self._chk_subs, _HINT_SUBS)

        self._combo_font = QComboBox(self)
        for family, label in SUBTITLE_FONTS:
            self._combo_font.addItem(label, userData=family)
            f = QFont(family); f.setBold(True); f.setPixelSize(13)
            self._combo_font.setItemData(self._combo_font.count() - 1, f, Qt.FontRole)
        _select_data(self._combo_font, DEFAULT_FONT)
        form.addRow("Font chữ", self._combo_font)

        self._spin_size = QSpinBox(self)
        self._spin_size.setRange(MIN_FONT_SIZE, MAX_FONT_SIZE)
        self._spin_size.setValue(DEFAULT_FONT_SIZE)
        self._spin_size.setSingleStep(2)
        self._spin_size.setFixedWidth(100)
        self._spin_size.setToolTip("Cỡ chữ theo khung 1080p — tự co giãn theo độ phân giải video")
        self._btn_preview = _small_button("Xem trước trên video…", self)
        self._btn_preview.setToolTip("Xem phụ đề trên khung hình thật, giống hệt bản render")
        self._btn_preview.clicked.connect(lambda: self.layout_edit_requested.emit("preview"))
        form.addRow("Cỡ chữ", _hbox(self._spin_size, self._btn_preview, stretch_last=False))

        self._combo_sub = QComboBox(self)
        self._combo_sub.addItem("Mặc định (giữa, sát đáy)", userData="default")
        self._combo_sub.addItem("Tự chọn trên khung hình", userData="custom")
        self._btn_sub = _small_button("Chọn vị trí…", self)
        self._btn_sub.clicked.connect(lambda: self.layout_edit_requested.emit("sub"))
        form.addRow("Vị trí", _hbox(self._combo_sub, self._btn_sub, stretch_first=True))

        self._btn_color = _ColorButton(DEFAULT_SUB_COLOR, "Màu chữ phụ đề", self)
        form.addRow("Màu chữ", _hbox(self._btn_color, stretch_last=False))
        self._btn_outline_color = _ColorButton(DEFAULT_OUTLINE_COLOR, "Màu viền chữ", self)
        form.addRow("Màu viền", _hbox(self._btn_outline_color, stretch_last=False))
        _add_hint(form, self._btn_outline_color, _HINT_OUTLINE_COLOR)
        self._spin_outline = QSpinBox(self)
        self._spin_outline.setRange(0, 8)
        self._spin_outline.setValue(DEFAULT_OUTLINE)
        self._spin_outline.setSuffix(" px")
        self._spin_outline.setFixedWidth(100)
        form.addRow("Độ dày viền", _hbox(self._spin_outline, stretch_last=False))
        _add_hint(form, self._spin_outline, _HINT_OUTLINE)
        self._chk_box = QCheckBox("Hộp nền tối sau chữ (box)", self)
        form.addRow("Nền chữ", self._chk_box)
        _add_hint(form, self._chk_box, _HINT_BOX)

        # Cover the original (Chinese) subtitles: one of off / auto / custom
        self._combo_blur = QComboBox(self)
        self._combo_blur.addItem("Tắt", userData="off")
        self._combo_blur.addItem("Tự động (theo phụ đề Việt, cả video)", userData="auto")
        self._combo_blur.addItem("Tùy chọn (vùng tự chọn, cả video)", userData="custom")
        _select_data(self._combo_blur, "auto")
        self._btn_blur = _small_button("Chọn vùng…", self)
        self._btn_blur.clicked.connect(lambda: self.layout_edit_requested.emit("blur"))
        form.addRow("Che phụ đề gốc", _hbox(self._combo_blur, self._btn_blur, stretch_first=True))
        _add_hint(form, self._combo_blur, _HINTS_BLUR)
        self._spin_blur_pad = QSpinBox(self)
        self._spin_blur_pad.setRange(0, 40)
        self._spin_blur_pad.setValue(DEFAULT_BLUR_PAD)
        self._spin_blur_pad.setSuffix(" px")
        self._spin_blur_pad.setFixedWidth(100)
        form.addRow("Lề vùng làm mờ", _hbox(self._spin_blur_pad, stretch_last=False))
        _add_hint(form, self._spin_blur_pad, _HINT_BLUR_PAD)

        self._lbl_layout = _hint_label(self)
        form.addRow("", self._lbl_layout)
        _finish_form(form)
        self._tabs.addTab(_scrollable(page_sub), "Phụ đề")

        self._combo_blur.currentIndexChanged.connect(self._on_blur_mode_changed)
        self._combo_sub.currentIndexChanged.connect(self._on_sub_mode_changed)
        self._on_layout_changed()

    def add_page(self, widget: QWidget, title: str) -> None:
        """Extra tab (the export settings live here too, so they disable together)."""
        self._tabs.addTab(widget, title)

    # ── Layout (blur / subtitle position) ─────────────────────────────────────

    def _editable(self) -> bool:
        return self.isEnabled() and not self._read_only

    def _on_blur_mode_changed(self, *_):
        if self._combo_blur.currentData() == "custom" and not self._blur_rect and self._editable():
            self.layout_edit_requested.emit("blur")
        self._on_layout_changed()

    def _on_sub_mode_changed(self, *_):
        if self._combo_sub.currentData() == "custom" and not self._sub_pos and self._editable():
            self.layout_edit_requested.emit("sub")
        self._on_layout_changed()

    def _on_layout_changed(self, *_):
        mode = self._combo_blur.currentData()
        self._btn_blur.setEnabled(mode == "custom")
        self._spin_blur_pad.setEnabled(mode == "auto")
        self._btn_sub.setEnabled(self._combo_sub.currentData() == "custom")
        parts = []
        if mode == "custom":
            parts.append("Vùng mờ: " + ("đã chọn" if self._blur_rect else "⚠ chưa chọn → dùng tự động"))
        if self._combo_sub.currentData() == "custom":
            parts.append("Phụ đề: " + ("đã chọn vị trí" if self._sub_pos else "⚠ chưa chọn → mặc định"))
        self._lbl_layout.setText("  ·  ".join(parts))

    def set_layout(self, blur_rect, sub_pos, blur_mode: "str | None" = None) -> None:
        """Store results from the frame editor (normalized coords)."""
        self._blur_rect = tuple(blur_rect) if blur_rect else None
        self._sub_pos = tuple(sub_pos) if sub_pos else None
        if blur_mode:
            self._combo_blur.blockSignals(True)
            _select_data(self._combo_blur, blur_mode)
            self._combo_blur.blockSignals(False)
        if self._sub_pos:
            _select_data(self._combo_sub, "custom")
        self._on_layout_changed()

    def layout_state(self) -> tuple:
        return self._blur_rect, self._sub_pos

    def blur_mode(self) -> str:
        return self._combo_blur.currentData()

    def sub_style(self) -> dict:
        """Look of the subtitle text + auto blur margin (for the preview dialog)."""
        return {"color": self._btn_color.color(), "outline_color": self._btn_outline_color.color(),
                "outline": self._spin_outline.value(), "box": self._chk_box.isChecked(),
                "blur_pad": self._spin_blur_pad.value()}

    def sub_font(self) -> tuple[str, int]:
        return self._combo_font.currentData(), self._spin_size.value()

    def set_sub_font(self, family: str, size: int) -> None:
        _select_data(self._combo_font, family)
        self._spin_size.setValue(size)

    def set_read_only(self, read_only: bool) -> None:
        """While a video is being processed: its settings can be looked at (tabs still
        switch) but not changed."""
        from PySide6.QtWidgets import QScrollArea
        self._read_only = read_only
        self._banner.setVisible(read_only)
        for i in range(self._tabs.count()):
            page = self._tabs.widget(i)
            inner = page.widget() if isinstance(page, QScrollArea) else page
            inner.setEnabled(not read_only)   # the scroll bar stays usable

    # ── Internal slots ────────────────────────────────────────────────────────

    def _on_audio_toggled(self, *_):
        music = self._chk_music.isChecked()
        voice = self._chk_cn_voice.isChecked()
        self._sld_music.setEnabled(music)
        self._lbl_music_vol.setEnabled(music)
        self._sld_cn_voice.setEnabled(voice)
        self._lbl_cn_vol.setEnabled(voice)
        if music and voice:
            msg = "Chỉnh riêng nhạc nền và tiếng Trung (tách bằng AI khi hai mức khác nhau)"
        elif music:
            msg = "Tách bằng AI → chỉ giữ nhạc nền"
        elif voice:
            msg = "Tách bằng AI → chỉ giữ tiếng Trung"
        else:
            msg = "Tắt hoàn toàn âm thanh gốc"
        self._hint.setText(msg)
        self.sep_label_changed.emit()

    def _on_trim_toggled(self, checked: bool):
        self._trim.setEnabled(checked)

    def _update_vi_label(self, v: int):
        self._lbl_vi_vol.setText(f"{v}%")
        if v > 100:
            self._lbl_vi_vol.setStyleSheet(f"color: {_AMBER}; font-size: 11px; font-weight: bold;")
        else:
            self._lbl_vi_vol.setStyleSheet(f"color: {_GREEN}; font-size: 11px; font-weight: bold;")

    # ── Getters ───────────────────────────────────────────────────────────────

    def vi_volume(self) -> float:
        return self._sld_vi.value() / 100.0

    def music_volume(self) -> float:
        return self._sld_music.value() / 100.0 if self._chk_music.isChecked() else 0.0

    def cn_voice_volume(self) -> float:
        return self._sld_cn_voice.value() / 100.0 if self._chk_cn_voice.isChecked() else 0.0

    def keep_music(self) -> bool:
        return self._chk_music.isChecked()

    def keep_cn_voice(self) -> bool:
        return self._chk_cn_voice.isChecked()

    def show_subtitles(self) -> bool:
        return self._chk_subs.isChecked()

    def voice(self) -> str | None:
        return self._combo_voice.currentData()

    def trim_start(self) -> float:
        return self._trim.trim()[0] if self._chk_trim.isChecked() else 0.0

    def set_trim_limit(self, duration: float) -> None:
        """Scale the trim slider to the selected video (0 = unknown)."""
        self._trim.set_duration(duration)

    def trim_end(self) -> "float | None":
        return self._trim.trim()[1] if self._chk_trim.isChecked() else None

    def tts_engine(self) -> str:
        return self._combo_engine.currentData()

    # ── Voices ────────────────────────────────────────────────────────────────

    _CLONE_NEW = "__clone__"

    def _fill_voices(self, engine: str, select: "str | None" = None) -> None:
        """(Re)load the voice list for *engine*; select *select* or the last used voice."""
        combo = self._combo_voice
        combo.blockSignals(True)
        combo.clear()
        if engine == "piper":
            for v in self._voices:
                friendly = v.replace("vi_VN-", "").replace("-", " ").replace("_", " ").title()
                combo.addItem(friendly, userData=v)
        else:
            from dubber.core.tts.voice_library import clone_voices, preset_voices

            def header(text):
                combo.addItem(text, userData=None)
                combo.model().item(combo.count() - 1).setEnabled(False)

            header("— Giọng có sẵn —")
            for v in preset_voices():
                combo.addItem(v.name, userData=v.id)
            clones = clone_voices()
            if clones:
                header("— Giọng đã clone —")
                for v in clones:
                    combo.addItem(v.name, userData=v.id)
            combo.addItem("➕ Clone giọng mới…", userData=self._CLONE_NEW)
            combo.setItemData(combo.count() - 1, _HINT_CLONE_ITEM, Qt.ToolTipRole)
        want = select if select is not None else self._last_voice.get(engine)
        if want and combo.findData(want) < 0 and engine == "vieneu":
            # e.g. a clone deleted after this video was queued — keep it so the run
            # reports it instead of silently switching voices
            combo.insertItem(1, "⚠ Giọng đã bị xóa", userData=want)
        _select_data(combo, want)
        if combo.currentData() in (None, self._CLONE_NEW):   # land on a real voice
            for i in range(combo.count()):
                if combo.itemData(i) not in (None, self._CLONE_NEW):
                    combo.setCurrentIndex(i)
                    break
        combo.blockSignals(False)
        self._refresh_voice_menu()

    def _on_engine_changed(self, *_):
        self._fill_voices(self.tts_engine())
        self._remember_voice()

    def _on_voice_changed(self, *_):
        if self._combo_voice.currentData() == self._CLONE_NEW:
            self._clone_new_voice()
            return
        self._remember_voice()
        self._refresh_voice_menu()

    def _remember_voice(self):
        """Keep the last choice per engine, and in settings.json for the next launch."""
        engine, voice = self.tts_engine(), self._combo_voice.currentData()
        if voice in (None, self._CLONE_NEW):
            return
        self._last_voice[engine] = voice
        from dubber.core.utils.settings import load_settings, save_settings
        st = load_settings()
        if (st.get("tts_engine"), st.get("voice")) != (engine, voice):
            st["tts_engine"], st["voice"] = engine, voice
            save_settings(st)

    def _clone_new_voice(self):
        from dubber.app.voice_dialog import CloneVoiceDialog
        dlg = CloneVoiceDialog(self)
        new = dlg.entry.id if dlg.exec() == QDialog.Accepted and dlg.entry else None
        self._fill_voices("vieneu", select=new or self._last_voice.get("vieneu"))
        self._remember_voice()

    def _refresh_voice_menu(self):
        v = self._combo_voice.currentData()
        vieneu = self.tts_engine() == "vieneu"
        is_clone = isinstance(v, str) and v.startswith("clone:")
        self._btn_rename_voice.setVisible(is_clone)
        self._btn_delete_voice.setVisible(is_clone)
        self._btn_demo.setEnabled(v not in (None, self._CLONE_NEW) and self._demo_task is None)
        if not vieneu:
            text = tip = ""
        elif is_clone:
            text = tip = "Giọng clone của bạn — ✎ đổi tên · 🗑 xóa"
        else:
            text = "Cần giọng riêng: chọn “➕ Clone giọng mới…”"
            tip = f"{text} — {_HINT_CLONE_ITEM.lower()}"
        self._hint_voice.setText(text)
        self._hint_voice.setVisible(bool(text))
        self._combo_voice.setToolTip(tip)

    def _selected_clone(self) -> "tuple[str, str] | None":
        voice = self._combo_voice.currentData() or ""
        if not voice.startswith("clone:"):
            return None
        return voice.split(":", 1)[1], self._combo_voice.currentText()

    def _rename_voice(self):
        from dubber.core.tts import voice_library as vl
        sel = self._selected_clone()
        if not sel:
            return
        vid, name = sel
        new, ok = QInputDialog.getText(self, "Đổi tên giọng", "Tên mới:", text=name)
        if ok and new.strip() and new.strip() != name:
            try:
                vl.rename(vid, new)
            except ValueError as exc:
                QMessageBox.warning(self, "Giọng clone", str(exc))
                return
            self._fill_voices("vieneu", select=f"clone:{vid}")

    def _delete_voice(self):
        from dubber.core.tts import voice_library as vl
        sel = self._selected_clone()
        if not sel:
            return
        vid, name = sel
        if QMessageBox.question(
            self, "Xóa giọng", f"Xóa giọng clone “{name}”? Không thể hoàn tác.",
        ) != QMessageBox.Yes:
            return
        try:
            vl.delete(vid)
        except ValueError as exc:
            QMessageBox.warning(self, "Giọng clone", str(exc))
            return
        self._last_voice["vieneu"] = None
        self._fill_voices("vieneu")
        self._remember_voice()

    # ── Voice preview ─────────────────────────────────────────────────────────

    def _play_demo(self):
        """Read a sample sentence with the selected voice (cached after the first time)."""
        from dubber.app.tasks import Task as _Task
        from dubber.core.tts.demo import make_demo
        engine, voice = self.tts_engine(), self._combo_voice.currentData()
        if voice in (None, self._CLONE_NEW) or self._demo_task is not None:
            return
        if self._demo_player is not None:
            self._demo_player.stop()
        self._btn_demo.setText("… Đang tạo")
        self._btn_demo.setEnabled(False)
        thread, task = QThread(self), _Task(lambda: make_demo(engine, voice))
        task.moveToThread(thread)
        thread.started.connect(task.run)
        task.done.connect(self._demo_ready)      # bound method → runs on the GUI thread
        task.failed.connect(self._demo_failed)
        task.done.connect(thread.quit)
        task.failed.connect(thread.quit)
        # drop our references only once the thread has really stopped
        thread.finished.connect(self._demo_thread_done)
        self._demo_task = (thread, task)
        thread.start()

    def _demo_thread_done(self):
        thread, _task = self._demo_task
        self._demo_task = None
        thread.deleteLater()
        self._refresh_voice_menu()

    def _demo_finished(self):
        self._btn_demo.setText("▶ Nghe thử")

    def _demo_ready(self, path):
        self._demo_finished()
        try:
            from PySide6.QtCore import QUrl
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        except ImportError:
            return
        if self._demo_player is None:
            self._demo_player = QMediaPlayer(self)
            self._demo_audio = QAudioOutput(self)
            self._demo_player.setAudioOutput(self._demo_audio)
        self._demo_player.setSource(QUrl.fromLocalFile(str(path)))
        self._demo_player.play()

    def _demo_failed(self, msg: str):
        self._demo_finished()
        QMessageBox.warning(self, "Nghe thử", f"Không tạo được câu mẫu:\n{msg}")

    def apply_saved_voice(self, settings: dict) -> None:
        """Restore the last engine + voice from settings.json (app start)."""
        engine = settings.get("tts_engine") or "piper"
        if engine == "vieneu" and not self._vieneu_ok:
            engine = "piper"
        voice = settings.get("voice") if engine == settings.get("tts_engine") else None
        if voice:
            self._last_voice[engine] = voice
        self._combo_engine.blockSignals(True)
        _select_data(self._combo_engine, engine)
        self._combo_engine.blockSignals(False)
        self._fill_voices(engine)

    def asr_on_vocals(self) -> bool:
        return self._chk_asr_vocals.isChecked()

    def translator(self) -> str:
        return self._combo_translator.currentData()

    # ── Per-video config snapshot ─────────────────────────────────────────────

    def get_config(self) -> dict:
        return {
            "vi": self._sld_vi.value(),
            "music_on": self._chk_music.isChecked(),
            "music": self._sld_music.value(),
            "cn_on": self._chk_cn_voice.isChecked(),
            "cn": self._sld_cn_voice.value(),
            "subs": self._chk_subs.isChecked(),
            "tts_engine": self.tts_engine(),
            "voice": self._combo_voice.currentData(),
            "translator": self._combo_translator.currentData(),
            "skip_review": self._chk_skip_review.isChecked(),
            "asr_vocals": self._chk_asr_vocals.isChecked(),
            "trim_on": self._chk_trim.isChecked(),
            "trim_start": self._trim.trim()[0],
            "trim_end": self._trim.trim()[1] or 0.0,   # 0 = until the end
            "blur_mode": self._combo_blur.currentData(),
            "blur_rect": self._blur_rect,
            "blur_pad": self._spin_blur_pad.value(),
            "sub_mode": self._combo_sub.currentData(),
            "sub_pos": self._sub_pos,
            "sub_font": self._combo_font.currentData(),
            "sub_size": self._spin_size.value(),
            "sub_color": self._btn_color.color(),
            "sub_outline_color": self._btn_outline_color.color(),
            "sub_outline": self._spin_outline.value(),
            "sub_box": self._chk_box.isChecked(),
        }

    def set_config(self, cfg: dict) -> None:
        self._sld_vi.setValue(cfg["vi"])
        self._chk_music.setChecked(cfg["music_on"])
        self._sld_music.setValue(cfg["music"])
        self._chk_cn_voice.setChecked(cfg["cn_on"])
        self._sld_cn_voice.setValue(cfg["cn"])
        self._chk_subs.setChecked(cfg["subs"])
        # tts_engine: .get() so configs saved before VieNeu still load (Piper)
        engine = cfg.get("tts_engine", "piper")
        if engine == "vieneu" and not self._vieneu_ok:
            engine = "piper"
        self._combo_engine.blockSignals(True)
        _select_data(self._combo_engine, engine)
        self._combo_engine.blockSignals(False)
        self._fill_voices(engine, select=cfg["voice"] if engine == cfg.get("tts_engine", "piper")
                          else None)
        _select_data(self._combo_translator, _translator_of(cfg))
        self._chk_skip_review.setChecked(cfg.get("skip_review", True))
        self._chk_asr_vocals.setChecked(cfg.get("asr_vocals", True) and self._mdxnet)
        self._chk_trim.setChecked(cfg["trim_on"])
        self._trim.set_trim(cfg["trim_start"], cfg["trim_end"] or None)
        # Layout keys: .get() so configs saved before this feature still load
        self._blur_rect = cfg.get("blur_rect")
        self._sub_pos = cfg.get("sub_pos")
        self._combo_blur.blockSignals(True); self._combo_sub.blockSignals(True)
        _select_data(self._combo_blur, _blur_mode_of(cfg))
        _select_data(self._combo_sub, cfg.get("sub_mode", "default"))
        from dubber.core.video.fonts import DEFAULT_FONT, DEFAULT_FONT_SIZE
        _select_data(self._combo_font, cfg.get("sub_font", DEFAULT_FONT))
        self._spin_size.setValue(cfg.get("sub_size", DEFAULT_FONT_SIZE))
        self._spin_blur_pad.setValue(cfg.get("blur_pad", DEFAULT_BLUR_PAD))
        self._btn_color.set_color(cfg.get("sub_color", DEFAULT_SUB_COLOR))
        self._btn_outline_color.set_color(cfg.get("sub_outline_color", DEFAULT_OUTLINE_COLOR))
        self._spin_outline.setValue(cfg.get("sub_outline", DEFAULT_OUTLINE))
        self._chk_box.setChecked(cfg.get("sub_box", False))
        self._combo_blur.blockSignals(False); self._combo_sub.blockSignals(False)
        self._on_layout_changed()


# ── Export panel ──────────────────────────────────────────────────────────────

class ExportPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        page, form = _form_page(self)
        outer.addWidget(page)

        self._combo_res = QComboBox(self)
        self._combo_res.addItem("Gốc (giữ nguyên)",  None)
        self._combo_res.addItem("1080p (1920×1080)",  "-2:1080")
        self._combo_res.addItem("720p (1280×720)",    "-2:720")
        self._combo_res.addItem("480p (854×480)",     "-2:480")
        self._combo_res.addItem("360p (640×360)",     "-2:360")
        form.addRow("Độ phân giải", self._combo_res)
        _add_hint(form, self._combo_res, _HINTS_RES, default=_HINT_RES_OTHER)

        self._combo_fps = QComboBox(self)
        self._combo_fps.addItem("Gốc", None)
        for f in (24, 25, 30, 60):
            self._combo_fps.addItem(str(f), f)
        form.addRow("FPS", self._combo_fps)
        _add_hint(form, self._combo_fps, _HINT_FPS)

        self._combo_quality = QComboBox(self)
        self._combo_quality.addItem("Cao (CRF 18)",      18)
        self._combo_quality.addItem("Tốt (CRF 23)",      23)
        self._combo_quality.addItem("Cân bằng (CRF 28)", 28)
        self._combo_quality.addItem("Nhỏ gọn (CRF 35)",  35)
        self._combo_quality.setCurrentIndex(1)
        form.addRow("Chất lượng", self._combo_quality)
        _add_hint(form, self._combo_quality, _HINT_CRF)

        self._combo_preset = QComboBox(self)
        for name, label in (("ultrafast", "Rất nhanh (file lớn)"), ("fast", "Nhanh"),
                            ("medium", "Cân bằng"), ("slow", "Chậm (file nhỏ hơn)")):
            self._combo_preset.addItem(label, name)
        self._combo_preset.setCurrentIndex(2)
        form.addRow("Encoder preset", self._combo_preset)
        _add_hint(form, self._combo_preset, _HINT_PRESET)

        self._combo_audio_br = QComboBox(self)
        for k in (96, 128, 192, 256, 320):
            self._combo_audio_br.addItem(f"{k} kbps", f"{k}k")
        self._combo_audio_br.setCurrentIndex(2)
        form.addRow("Audio bitrate", self._combo_audio_br)
        _add_hint(form, self._combo_audio_br, _HINT_AUDIO_BR)
        _finish_form(form)

    def scale(self) -> "str | None":
        return self._combo_res.currentData()

    def fps(self) -> "int | None":
        return self._combo_fps.currentData()

    def crf(self) -> int:
        return self._combo_quality.currentData()

    def encoder_preset(self) -> str:
        return self._combo_preset.currentData()

    def audio_bitrate(self) -> str:
        return self._combo_audio_br.currentData()

    def set_source(self, info) -> None:
        """Show the source video's real values on the 'keep original' items."""
        if info and info.width:
            self._combo_res.setItemText(0, f"Gốc ({info.width}×{info.height})")
            self._combo_fps.setItemText(0, f"Gốc ({_fmt_fps(info.fps)})")
        else:
            self._combo_res.setItemText(0, "Gốc (giữ nguyên)")
            self._combo_fps.setItemText(0, "Gốc")

    def get_config(self) -> dict:
        return {
            "scale": self.scale(), "fps": self.fps(), "crf": self.crf(),
            "preset": self.encoder_preset(), "audio_br": self.audio_bitrate(),
        }

    def set_config(self, cfg: dict) -> None:
        _select_data(self._combo_res, cfg["scale"])
        _select_data(self._combo_fps, cfg["fps"])
        _select_data(self._combo_quality, cfg["crf"])
        _select_data(self._combo_preset, cfg["preset"])
        _select_data(self._combo_audio_br, cfg["audio_br"])


# ── Source video info panel ───────────────────────────────────────────────────

def _fmt_fps(fps: float) -> str:
    return f"{fps:.0f}" if abs(fps - round(fps)) < 0.01 else f"{fps:.2f}"


def _fmt_duration(sec: float) -> str:
    sec = int(round(sec))
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _fmt_size(n: int) -> str:
    return f"{n / 1024**3:.2f} GB" if n >= 1024**3 else f"{n / 1024**2:.1f} MB"


class VideoInfoPanel(QGroupBox):
    """Read-only properties of the selected source video."""

    _ROWS = [("res", "Độ phân giải"), ("fps", "FPS"), ("dur", "Thời lượng"),
             ("size", "Dung lượng"), ("vcodec", "Video codec"), ("audio", "Audio")]

    def __init__(self, parent=None):
        super().__init__("Thông tin video gốc", parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(12, 16, 12, 10)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(3)
        self._val: dict[str, QLabel] = {}
        for r, (key, label) in enumerate(self._ROWS):
            k = QLabel(label, self)
            k.setStyleSheet(f"color: {_TXT_DIM}; font-size: 11px;")
            v = QLabel("—", self)
            v.setStyleSheet(f"color: {_TXT_MAIN}; font-size: 12px;")
            v.setTextInteractionFlags(Qt.TextSelectableByMouse)
            grid.addWidget(k, r, 0)
            grid.addWidget(v, r, 1)
            self._val[key] = v
        grid.setColumnStretch(1, 1)
        # A frame of the video with the subtitle look drawn on it (follows the Phụ đề tab)
        self._thumb = QLabel(self)
        self._thumb.setFixedSize(256, 144)
        self._thumb.setAlignment(Qt.AlignCenter)
        self._thumb.setStyleSheet(f"background: #0c0c0c; border: 1px solid {_BORDER};"
                                  f" border-radius: 4px; color: {_TXT_DIM}; font-size: 11px;")
        self._thumb.setToolTip("Xem nhanh phụ đề trên một khung hình (gần đúng).\n"
                               "Tab Phụ đề → “Xem trước trên video…” để xem chính xác.")
        grid.addWidget(self._thumb, 0, 2, len(self._ROWS), 1, Qt.AlignRight | Qt.AlignTop)
        self.show_preview(None)

    def show_preview(self, pixmap, text: str = "") -> None:
        if pixmap is None or pixmap.isNull():
            self._thumb.setPixmap(QPixmap())
            self._thumb.setText(text)
        else:
            self._thumb.setText("")
            self._thumb.setPixmap(pixmap.scaled(self._thumb.size() - QSize(2, 2),
                                                Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def show_info(self, info, error: str = "", loading: bool = False) -> None:
        if error or info is None:
            for v in self._val.values():
                v.setText("—")
            if error:
                self._val["res"].setText(f"Không đọc được: {error}")
            elif loading:
                self._val["res"].setText("⏳ Đang đọc thông tin video…")
            return
        rot = f" · xoay {info.rotation}°" if info.rotation else ""
        self._val["res"].setText(f"{info.width} × {info.height}  ({info.aspect}){rot}")
        self._val["fps"].setText(_fmt_fps(info.fps) if info.fps else "—")
        self._val["dur"].setText(_fmt_duration(info.duration))
        self._val["size"].setText(
            _fmt_size(info.size_bytes) + (f"  ·  {info.bitrate_kbps} kbps" if info.bitrate_kbps else ""))
        vk = f" · {info.video_kbps} kbps" if info.video_kbps else ""
        self._val["vcodec"].setText(f"{info.video_codec.upper()} · {info.bit_depth}-bit{vk}")
        if info.audio_codec:
            ch = {1: "mono", 2: "stereo"}.get(info.channels, f"{info.channels} kênh")
            ak = f" · {info.audio_kbps} kbps" if info.audio_kbps else ""
            self._val["audio"].setText(f"{info.audio_codec.upper()} · {info.sample_rate / 1000:g} kHz · {ch}{ak}")
            self._val["audio"].setStyleSheet(f"color: {_TXT_MAIN}; font-size: 12px;")
        else:
            self._val["audio"].setText("⚠ Không có âm thanh — không thể lồng tiếng")
            self._val["audio"].setStyleSheet(f"color: {_RED}; font-size: 12px;")


# ── Output settings panel (global, persisted) ─────────────────────────────────

class OutputPanel(QGroupBox):
    """Where finished videos are saved. Persisted in settings.json."""

    def __init__(self, parent=None):
        super().__init__("Lưu video kết quả", parent)
        from dubber.core.utils.settings import load_settings
        self._settings = load_settings()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 16, 12, 12)
        lay.setSpacing(6)

        self._chk_beside = QCheckBox("Lưu cạnh video gốc", self)
        lay.addWidget(self._chk_beside)

        row = QHBoxLayout(); row.setSpacing(8)
        self._edit_dir = QLineEdit(self)
        self._edit_dir.setReadOnly(True)
        self._edit_dir.setPlaceholderText("Chọn thư mục…")
        self._edit_dir.setStyleSheet(
            f"QLineEdit {{ background: {_SURFACE}; border: 1px solid #3a3a3a; border-radius: 5px;"
            f" padding: 4px 8px; font-size: 12px; color: {_TXT_MAIN}; min-height: 22px; }}"
            f"QLineEdit:disabled {{ color: {_TXT_DIM}; border-color: {_BORDER}; }}"
        )
        row.addWidget(self._edit_dir, 1)
        self._btn_browse = QPushButton("Chọn…", self)
        self._btn_browse.setFixedHeight(30)
        self._btn_browse.setStyleSheet(_SMALL_BTN_STYLE)
        self._btn_browse.clicked.connect(self._browse)
        row.addWidget(self._btn_browse)
        lay.addLayout(row)

        hint = QLabel("Tên file: <tên gốc>_vi.mp4 (+ _vi.txt) — không ghi đè file đã có", self)
        hint.setStyleSheet(f"color: {_TXT_DIM}; font-size: 10px;")
        lay.addWidget(hint)

        out_dir = self._settings.get("output_dir")
        self._edit_dir.setText(out_dir or "")
        self._chk_beside.setChecked(not out_dir)
        self._chk_beside.toggled.connect(self._on_beside_toggled)
        self._on_beside_toggled(self._chk_beside.isChecked())

    def _on_beside_toggled(self, beside: bool):
        self._edit_dir.setEnabled(not beside)
        self._btn_browse.setEnabled(not beside)
        if not beside and not self._edit_dir.text():
            self._browse()
        self._save()

    def _browse(self):
        start = self._edit_dir.text() or str(Path.home() / ("Videos" if sys.platform == "win32"
                                                            else "Movies"))
        d = QFileDialog.getExistingDirectory(self, "Chọn thư mục lưu video", start)
        if d:
            self._edit_dir.setText(d)
        elif not self._edit_dir.text():
            self._chk_beside.setChecked(True)  # cancelled with nothing chosen
        self._save()

    def _save(self):
        from dubber.core.utils.settings import load_settings, save_settings
        self._settings = load_settings()   # other panels save keys too
        self._settings["output_dir"] = self.output_dir() and str(self.output_dir())
        save_settings(self._settings)

    def output_dir(self) -> "Path | None":
        if self._chk_beside.isChecked() or not self._edit_dir.text():
            return None
        return Path(self._edit_dir.text())


# ── Step row ───────────────────────────────────────────────────────────────────

class StepRow(QWidget):
    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(8)

        self._dot = QLabel("○", self)
        self._dot.setFixedWidth(14)
        self._dot.setAlignment(Qt.AlignCenter)
        self._dot.setStyleSheet(f"color: #333; font-size: 13px;")

        self._lbl = QLabel(label, self)
        self._lbl.setFixedWidth(200)
        self._lbl.setStyleSheet(f"color: {_TXT_DIM}; font-size: 12px;")

        self._bar = QProgressBar(self)
        self._bar.setRange(0, 100); self._bar.setValue(0)
        self._bar.setTextVisible(False); self._bar.setFixedHeight(5)
        self._bar.setStyleSheet(
            "QProgressBar { border-radius: 2px; background: #222; }"
            f"QProgressBar::chunk {{ border-radius: 2px; background: {_ACCENT}; }}"
        )

        self._pct = QLabel("", self)
        self._pct.setFixedWidth(34)
        self._pct.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._pct.setStyleSheet(f"color: {_TXT_DIM}; font-size: 11px;")

        self._time = QLabel("", self)
        self._time.setFixedWidth(52)
        self._time.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._time.setStyleSheet(f"color: {_TXT_DIM}; font-size: 11px;")
        self._time.setToolTip("Thời gian của bước này")

        lay.addWidget(self._dot)
        lay.addWidget(self._lbl)
        lay.addWidget(self._bar, 1)
        lay.addWidget(self._pct)
        lay.addWidget(self._time)

    def set_time(self, text: str) -> None:
        self._time.setText(text)

    def set_running(self):
        self._dot.setText("◉")
        self._dot.setStyleSheet(f"color: {_ACCENT}; font-size: 13px;")
        self._lbl.setStyleSheet(f"color: {_TXT_MAIN}; font-size: 12px;")

    def set_progress(self, pct: int):
        self._bar.setValue(pct)
        self._pct.setText(f"{pct}%")
        self._pct.setStyleSheet(f"color: {_TXT_MID}; font-size: 11px;")

    def set_done(self):
        self._dot.setText("✓")
        self._dot.setStyleSheet(f"color: {_GREEN}; font-size: 13px;")
        self._lbl.setStyleSheet(f"color: {_TXT_MAIN}; font-size: 12px;")
        self._bar.setValue(100)
        self._bar.setStyleSheet(
            "QProgressBar { border-radius: 2px; background: #222; }"
            f"QProgressBar::chunk {{ border-radius: 2px; background: {_GREEN}; }}"
        )
        self._pct.setText("100%")
        self._pct.setStyleSheet(f"color: {_GREEN}; font-size: 11px;")

    def reset(self):
        self._dot.setText("○")
        self._dot.setStyleSheet(f"color: #333; font-size: 13px;")
        self._lbl.setStyleSheet(f"color: {_TXT_DIM}; font-size: 12px;")
        self._bar.setValue(0)
        self._bar.setStyleSheet(
            "QProgressBar { border-radius: 2px; background: #222; }"
            f"QProgressBar::chunk {{ border-radius: 2px; background: {_ACCENT}; }}"
        )
        self._pct.setText("")
        self._pct.setStyleSheet(f"color: {_TXT_DIM}; font-size: 11px;")
        self._time.setText("")


# ── Main window ────────────────────────────────────────────────────────────────

_STEPS = [
    ("Separate",  "Tách nhạc nền"),   # first: runs before ASR when filtering music
    ("ASR",       "Nhận dạng giọng nói (ASR)"),
    ("Translate", "Dịch"),
    ("TTS",       "Tạo giọng Việt (TTS)"),
    ("Mux",       "Ghép âm thanh"),
    ("Render",    "Render video"),
]


def _translator_of(o: dict) -> str:
    """Translator of a per-video config; the removed "manual" mode runs as "auto"."""
    t = o.get("translator") or "auto"
    return "auto" if t == "manual" else t


def _blur_mode_of(o: dict) -> str:
    """off / auto / custom from a per-video config (before M10: a separate blur_on box)."""
    if not o.get("blur_on", True):
        return "off"
    mode = o.get("blur_mode", "auto")
    return mode if mode in ("off", "auto", "custom") else "auto"


def _layout_kwargs(o: dict) -> dict:
    """Per-video config → run_m5 blur/subtitle layout + style arguments."""
    blur_mode = _blur_mode_of(o)
    if blur_mode == "custom" and not o.get("blur_rect"):
        blur_mode = "auto"
    from dubber.core.video.fonts import DEFAULT_FONT, DEFAULT_FONT_SIZE
    return {
        "blur_mode": blur_mode,
        "blur_rect": o.get("blur_rect") if blur_mode == "custom" else None,
        "blur_pad": o.get("blur_pad", DEFAULT_BLUR_PAD),
        "sub_pos": o.get("sub_pos") if o.get("sub_mode") == "custom" else None,
        "sub_font": o.get("sub_font", DEFAULT_FONT),
        "sub_size": o.get("sub_size", DEFAULT_FONT_SIZE),
        "sub_color": o.get("sub_color", DEFAULT_SUB_COLOR),
        "sub_outline_color": o.get("sub_outline_color", DEFAULT_OUTLINE_COLOR),
        "sub_outline": o.get("sub_outline", DEFAULT_OUTLINE),
        "sub_box": o.get("sub_box", False),
    }


class _Job:
    """One queued video: its own options/export config + run state."""

    PENDING, RUNNING, DONE, ERROR, CANCELLED = "pending", "running", "done", "error", "cancelled"
    FINISHED = (DONE, ERROR, CANCELLED)   # can be edited and queued again

    def __init__(self, path: Path, opts: dict, export: dict):
        self.path = path
        self.opts = opts
        self.export = export
        self.status = _Job.PENDING
        self.output: Path | None = None
        self.error = ""
        self.report_text = ""     # full error report (pop-up)
        self.report_path = ""
        self.info = None          # VideoInfo, probed in the background after adding
        self.info_error = ""
        self.waiting = ""         # running but waiting on the user: review / rate_limit
        self.run_seconds = 0.0    # processing time of the last run (waits excluded)
        self.finished_at = None   # datetime the last run ended

    def probed(self) -> bool:
        return self.info is not None or bool(self.info_error)


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} — Lồng tiếng Trung → Việt")
        self.setMinimumWidth(1040)
        self.setStyleSheet(_BASE_STYLE)
        self._jobs: list[_Job] = []
        self._sel: int = -1           # index of job whose config is shown in the panels
        self._running: int = -1       # index of job currently processing
        self._stop_requested = False
        self._cancelling = False
        self._output_path: Path | None = None
        disable_wheel_on_inputs(QApplication.instance())
        self._progress = None         # RunProgress of the running video
        self._ran = 0                 # videos finished in this run of the queue (notifications)
        self._build_ui()
        # Elapsed / time-left labels tick once a second while a video runs
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(1000)
        self._tick_timer.timeout.connect(self._tick)
        self._restore_state()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(10)

        # ── Header ─────────────────────────────────────────────────────────────
        hdr = QHBoxLayout(); hdr.setSpacing(0)
        logo = QLabel(self)
        logo.setPixmap(app_logo(30))
        logo.setFixedSize(30, 30)
        title = QLabel(f"Dubber <span style='color:{_BEE};'>Bee</span>", self)
        title.setTextFormat(Qt.RichText)
        title.setStyleSheet("font-size: 20px; font-weight: 700; color: #fff;"
                            " letter-spacing: -0.3px; margin-left: 10px;")
        tagline = QLabel("Lồng tiếng Trung → Việt", self)
        tagline.setStyleSheet(f"color: {_TXT_MID}; font-size: 12px; margin-left: 12px;"
                              " padding-top: 5px;")
        hdr.addWidget(logo)
        hdr.addWidget(title)
        hdr.addWidget(tagline)
        hdr.addStretch()
        # App-wide settings live in one dialog; the Groq chip mirrors its key lamp
        from dubber.app.settings_dialog import SettingsDialog
        self._settings_dlg = SettingsDialog(self)
        self._btn_groq_chip = QPushButton("Groq  ●", self)
        self._btn_groq_chip.setFixedHeight(28)
        self._btn_groq_chip.setCursor(Qt.PointingHandCursor)
        self._btn_groq_chip.clicked.connect(self._open_settings)
        self._settings_dlg.groq.lamp_changed.connect(self._on_groq_lamp)
        self._btn_settings = QPushButton("⚙  Cài đặt", self)
        self._btn_settings.setFixedHeight(28)
        self._btn_settings.setStyleSheet(_SMALL_BTN_STYLE)
        self._btn_settings.setToolTip("Groq API key, thông báo, prompt AI, cache")
        self._btn_settings.clicked.connect(self._open_settings)
        hdr.addWidget(self._btn_groq_chip)
        hdr.addSpacing(8)
        hdr.addWidget(self._btn_settings)
        self._on_groq_lamp(_TXT_MID, "Groq API key: chưa kiểm tra — bấm để mở Cài đặt")
        root.addLayout(hdr)

        cols = QHBoxLayout(); cols.setSpacing(12)
        left = QVBoxLayout(); left.setSpacing(10)
        right = QVBoxLayout(); right.setSpacing(10)
        cols.addLayout(left, 1)
        cols.addLayout(right, 1)
        root.addLayout(cols)

        # ── Left: drop zone + queue + output folder ───────────────────────────
        self._drop = DropZone(self)
        self._drop.files_chosen.connect(self._add_files)
        left.addWidget(self._drop)

        queue_box = QGroupBox("Hàng đợi — chọn video để chỉnh cấu hình riêng", self)
        q_lay = QVBoxLayout(queue_box)
        q_lay.setContentsMargins(10, 14, 10, 10)
        q_lay.setSpacing(6)
        self._summary = QLabel("", self)
        self._summary.setStyleSheet(f"color: {_TXT_MID}; font-size: 11px;")
        q_lay.addWidget(self._summary)
        self._list = QListWidget(self)
        # Cmd/Shift-click selects several (bulk remove / rerun / apply config); drag a
        # card to change the order the queue runs in
        self._list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._list.setDragDropMode(QAbstractItemView.InternalMove)
        self._list.setDefaultDropAction(Qt.MoveAction)
        self._list.setMinimumHeight(160)
        self._list.setSpacing(2)
        self._list.setStyleSheet(
            f"QListWidget {{ background: {_SURFACE}; border: 1px solid {_BORDER};"
            f" border-radius: 6px; font-size: 12px; color: {_TXT_MAIN}; }}"
            " QListWidget::item { border-radius: 5px; }"
            " QListWidget::item:hover { background: rgba(255, 255, 255, 0.04); }"
            f" QListWidget::item:selected {{ background: rgba(77, 184, 255, 0.20); }}"
        )
        self._list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._context_menu)
        self._list.currentRowChanged.connect(self._on_select)
        self._list.itemSelectionChanged.connect(self._refresh_buttons)
        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._list.model().rowsMoved.connect(self._on_rows_moved)
        q_lay.addWidget(self._list, 1)
        # Reading video info (ffprobe) runs in the background, one file at a time
        self._queue_busy = BusyBar(self)
        q_lay.addWidget(self._queue_busy)
        self._probe_runner = TaskRunner(self)

        q_btns = QHBoxLayout(); q_btns.setSpacing(6)
        self._btn_apply_all = QPushButton("Áp dụng cho tất cả", self)
        self._btn_rerun = QPushButton("↻ Chạy lại", self)
        self._btn_rerun.setToolTip("Đưa video đã xong / lỗi / đã hủy về hàng đợi")
        self._btn_remove = QPushButton("Xóa", self)
        self._btn_clear = QPushButton("Dọn video đã xong", self)
        for b in (self._btn_apply_all, self._btn_rerun, self._btn_remove, self._btn_clear):
            b.setFixedHeight(28)
            b.setStyleSheet(_SMALL_BTN_STYLE)
        self._btn_apply_all.clicked.connect(self._apply_to_all)
        self._btn_rerun.clicked.connect(self._rerun_selected)
        self._btn_remove.clicked.connect(self._remove_selected)
        self._btn_clear.clicked.connect(self._clear_finished)
        q_btns.addWidget(self._btn_apply_all, 1)
        q_btns.addWidget(self._btn_rerun)
        q_btns.addWidget(self._btn_remove)
        q_btns.addWidget(self._btn_clear)
        q_lay.addLayout(q_btns)
        left.addWidget(queue_box, 1)

        self._info = VideoInfoPanel(self)
        left.addWidget(self._info)
        self._frame_runner = TaskRunner(self)
        self._frames: dict = {}            # video path → extracted frame (or error text)
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(120)
        self._preview_timer.timeout.connect(self._refresh_preview)

        self._out = OutputPanel(self)
        left.addWidget(self._out)

        # ── Right: per-video options + export ─────────────────────────────────
        self._opts = OptionsPanel(self)
        from dubber.core.utils.settings import load_settings
        self._opts.apply_saved_voice(load_settings())
        self._opts._chk_music.toggled.connect(self._update_separate_label)
        self._opts._chk_cn_voice.toggled.connect(self._update_separate_label)
        self._opts._sld_music.valueChanged.connect(self._update_separate_label)
        self._opts._sld_cn_voice.valueChanged.connect(self._update_separate_label)
        self._opts.layout_edit_requested.connect(self._edit_layout)
        o = self._opts
        for sig in (o._combo_font.currentIndexChanged, o._spin_size.valueChanged,
                    o._btn_color.changed, o._btn_outline_color.changed,
                    o._spin_outline.valueChanged, o._chk_box.toggled, o._chk_subs.toggled,
                    o._combo_sub.currentIndexChanged, o._combo_blur.currentIndexChanged,
                    o._spin_blur_pad.valueChanged):
            sig.connect(lambda *_: self._preview_timer.start())
        self._export = ExportPanel(self)
        self._opts.add_page(self._export, "Xuất video")

        self._lbl_editing = QLabel("Thêm video để bắt đầu", self)
        self._lbl_editing.setStyleSheet(f"color: {_TXT_MID}; font-size: 13px; padding: 2px 2px 6px 2px;")
        self._lbl_editing.setTextFormat(Qt.RichText)
        right.addWidget(self._lbl_editing)
        right.addWidget(self._opts)

        # ── Progress section ───────────────────────────────────────────────────
        self._prog_box = QGroupBox("Tiến trình", self)
        prog_lay = QVBoxLayout(self._prog_box)
        prog_lay.setContentsMargins(10, 14, 10, 10)
        prog_lay.setSpacing(2)

        self._rows: dict[str, StepRow] = {}
        for key, label in _STEPS:
            row = StepRow(label, self)
            self._rows[key] = row
            prog_lay.addWidget(row)

        right.addWidget(self._prog_box)
        self._update_separate_label()

        # ── Buttons ────────────────────────────────────────────────────────────
        btns = QHBoxLayout(); btns.setSpacing(8)
        self._btn_start = QPushButton("▶   Chạy hàng đợi", self)
        self._btn_start.setEnabled(False)
        self._btn_start.setFixedHeight(36)
        self._btn_start.setStyleSheet(
            f"QPushButton {{ background: {_ACCENT}; color: #000; font-weight: bold;"
            " border-radius: 6px; font-size: 13px; letter-spacing: 0.3px; }"
            f"QPushButton:hover {{ background: #70c8ff; }}"
            " QPushButton:disabled { background: #1e1e1e; color: #444; }"
        )
        self._btn_start.clicked.connect(self._start)
        btns.addWidget(self._btn_start, 3)

        self._btn_stop = QPushButton("⏹  Dừng sau video này", self)
        self._btn_stop.setEnabled(False)
        self._btn_stop.setFixedHeight(36)
        self._btn_stop.setStyleSheet(_SMALL_BTN_STYLE)
        self._btn_stop.clicked.connect(self._request_stop)
        btns.addWidget(self._btn_stop, 2)

        self._btn_cancel = QPushButton("✖  Hủy video này", self)
        self._btn_cancel.setToolTip("Dừng ngay video đang chạy, xóa file tạm của video đó"
                                    " và dừng hàng đợi")
        self._btn_cancel.setEnabled(False)
        self._btn_cancel.setFixedHeight(36)
        self._btn_cancel.setStyleSheet(_SMALL_BTN_STYLE)
        self._btn_cancel.clicked.connect(self._cancel_current)
        btns.addWidget(self._btn_cancel, 2)

        self._btn_open = QPushButton("📂  Mở thư mục kết quả", self)
        self._btn_open.setEnabled(False)
        self._btn_open.setFixedHeight(36)
        self._btn_open.setStyleSheet(
            f"QPushButton {{ background: {_SURFACE}; color: {_TXT_MID};"
            f" border: 1px solid {_BORDER}; border-radius: 6px; font-size: 13px; }}"
            f"QPushButton:hover {{ background: #2a2a2a; color: {_TXT_MAIN}; }}"
            " QPushButton:disabled { background: #161616; color: #333; border-color: #1e1e1e; }"
        )
        self._btn_open.clicked.connect(self._open_folder)
        btns.addWidget(self._btn_open, 2)
        root.addLayout(btns)

        # ── Status ─────────────────────────────────────────────────────────────
        self._status = QLabel("", self)
        self._status.setWordWrap(True)
        self._status.setStyleSheet(f"color: {_TXT_DIM}; font-size: 11px;")
        root.addWidget(self._status)

        self._refresh_buttons()

    def _open_settings(self):
        self._settings_dlg.show()
        self._settings_dlg.raise_()
        self._settings_dlg.activateWindow()

    def _on_groq_lamp(self, color: str, tip: str) -> None:
        """Header chip "Groq ●" takes the key lamp's color (grey / green / red)."""
        self._btn_groq_chip.setToolTip(tip)
        self._btn_groq_chip.setStyleSheet(
            f"QPushButton {{ background: #1e1e1e; color: {color}; border: 1px solid #333;"
            f" border-radius: 14px; padding: 0 12px; font-size: 12px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: #2a2a2a; }}")

    def _update_separate_label(self, *_):
        music = self._opts.keep_music()
        voice = self._opts.keep_cn_voice()
        mv = self._opts._sld_music.value()
        vv = self._opts._sld_cn_voice.value()
        diff_vol = music and voice and abs(mv - vv) >= 1

        if self._opts.asr_on_vocals():
            lbl, will_run = "Tách nhạc nền (lọc cho ASR)", True
        elif music and not voice:
            lbl, will_run = "Tách nhạc nền (giữ nhạc)", True
        elif voice and not music:
            lbl, will_run = "Tách nhạc nền (giữ tiếng Trung)", True
        elif diff_vol:
            lbl, will_run = "Tách nhạc nền (mix riêng)", True
        else:
            lbl, will_run = "Tách nhạc nền (bỏ qua)", False

        self._rows["Separate"]._lbl.setText(lbl)
        self._rows["Separate"]._lbl.setStyleSheet(
            f"color: {'#333' if not will_run else _TXT_DIM}; font-size: 12px;"
        )
        self._rows["Separate"]._dot.setStyleSheet(
            f"color: {'#222' if not will_run else '#444'}; font-size: 13px;"
        )

    # ── Queue management ───────────────────────────────────────────────────────

    def _commit_selected(self):
        """Save the panels' current state into the selected job (any but the running one)."""
        if 0 <= self._sel < len(self._jobs) and self._jobs[self._sel].status != _Job.RUNNING:
            job = self._jobs[self._sel]
            job.opts = self._opts.get_config()
            job.export = self._export.get_config()

    def _add_files(self, paths: list):
        self._commit_selected()
        known = {j.path.resolve(): j for j in self._jobs}
        added = requeued = 0
        for p in paths:
            if p.resolve() in known:
                # same video twice would share one job cache dir — re-adding a finished
                # one puts it back in the queue instead
                job = known[p.resolve()]
                if job.status in _Job.FINISHED:
                    self._requeue(self._jobs.index(job))
                    requeued += 1
                continue
            # New videos start from the config currently shown in the panels
            self._add_job(_Job(p, self._opts.get_config(), self._export.get_config()))
            known[p.resolve()] = self._jobs[-1]
            added += 1
        self._probe_next()
        if added and self._sel < 0:
            self._list.setCurrentRow(0, QItemSelectionModel.ClearAndSelect)
        skipped = len(paths) - added - requeued
        self._set_status(
            f"Đã thêm {added} video"
            + (f" — đưa {requeued} video đã chạy về hàng đợi" if requeued else "")
            + (f" — bỏ qua {skipped} video đã có trong hàng đợi" if skipped else ""),
            _TXT_DIM,
        )
        self._refresh_buttons()

    def _on_select(self, row: int):
        self._commit_selected()
        self._sel = row
        if 0 <= row < len(self._jobs):
            job = self._jobs[row]
            state = {"pending": "", "running": " — đang xử lý, chỉ xem",
                     "done": " — đã xong", "error": " — lỗi",
                     "cancelled": " — đã hủy"}[job.status]
            if job.status in _Job.FINISHED:
                state += " · ↻ Chạy lại để xử lý lần nữa"
            self._lbl_editing.setText(
                f"Cấu hình cho <b style='color:{_TXT_BRIGHT}'>{job.path.name}</b>"
                f"<span style='color:{_TXT_DIM}'>{state}</span>")
            self._show_source_info(job)
            self._opts.set_config(job.opts)
            self._export.set_config(job.export)
            self._sync_panels()
            if job.status == _Job.DONE and job.output:
                self._set_status(f"✓  {job.path.name} → {job.output}", _GREEN)
            elif job.status == _Job.ERROR:
                self._set_status(f"✗  {job.path.name}: {job.error}", _RED)
        else:
            self._opts.set_read_only(False)
            self._info.show_info(None)
            self._export.set_source(None)
            self._lbl_editing.setText("Cấu hình mặc định cho video mới")
        self._refresh_buttons()

    def _show_source_info(self, job: "_Job") -> None:
        # Not probed yet → the background probe fills the panels in when it gets there
        self._info.show_info(job.info, job.info_error, loading=not job.probed())
        self._preview_timer.start()
        self._export.set_source(job.info)
        self._opts.set_trim_limit(job.info.duration if job.info else 0.0)
        if not job.probed():
            self._probe_next()

    def _probe_next(self) -> None:
        """Read the next unprobed video's info on a worker thread (ffprobe can take seconds
        on network / USB drives); the queue shows a busy bar until all are read."""
        if self._probe_runner.busy() or getattr(self, "_closing", False):
            return
        todo = [j for j in self._jobs if not j.probed()]
        if not todo:
            self._queue_busy.stop()
            return
        # the selected video first, so its panels fill in soonest
        sel = self._jobs[self._sel] if 0 <= self._sel < len(self._jobs) else None
        job = sel if sel in todo else todo[0]
        self._queue_busy.start(f"Đang đọc video: {job.path.name}"
                               + (f"  (còn {len(todo) - 1})" if len(todo) > 1 else ""))
        from dubber.core.video.info import probe_info
        self._probe_runner.run(lambda p=job.path: probe_info(p),
                               on_done=lambda info, j=job: self._probed(j, info, ""),
                               on_fail=lambda msg, j=job: self._probed(j, None, msg))

    def _probed(self, job: "_Job", info, error: str) -> None:
        job.info, job.info_error = info, (error[:120] or ("" if info else "không đọc được"))
        if job in self._jobs:
            idx = self._jobs.index(job)
            self._update_item(idx)
            if idx == self._sel:
                self._show_source_info(job)
        self._probe_next()

    def _refresh_preview(self) -> None:
        """Frame of the selected video + the current subtitle settings drawn on it."""
        if not (0 <= self._sel < len(self._jobs)):
            self._info.show_preview(None)
            return
        job = self._jobs[self._sel]
        if job.info is None:
            self._info.show_preview(None, "⏳" if not job.probed() else "")
            return
        frame = self._frames.get(job.path)
        if frame is None:
            if not self._frame_runner.busy():
                from dubber.app.preview import extract_frame
                self._info.show_preview(None, "Đang lấy khung hình…")
                path, dur = job.path, job.info.duration
                self._frame_runner.run(lambda: extract_frame(path, dur),
                                       on_done=lambda f, p=path: self._frame_ready(p, f),
                                       on_fail=lambda m, p=path: self._frame_ready(p, m))
            return
        if isinstance(frame, str):
            self._info.show_preview(None, "Không lấy được khung hình")
            return
        from dubber.app.preview import paint_preview
        o = self._opts.get_config()
        if job.status == _Job.RUNNING:
            o = job.opts
        pix = paint_preview(frame, job.info.width, job.info.height, _layout_kwargs(o),
                            show_subs=o.get("subs", True))
        self._info.show_preview(pix)

    def _frame_ready(self, path: Path, frame) -> None:
        self._frames[path] = frame
        self._preview_timer.start()

    def _apply_to_all(self):
        opts, export = self._opts.get_config(), self._export.get_config()
        n = 0
        for job in self._jobs:
            if job.status == _Job.PENDING:
                job.opts, job.export = dict(opts), dict(export)
                n += 1
        self._set_status(f"Đã áp dụng cấu hình cho {n} video đang chờ", _TXT_DIM)

    def _selected_rows(self) -> list[int]:
        rows = sorted({self._list.row(it) for it in self._list.selectedItems()})
        if not rows and 0 <= self._sel < len(self._jobs):
            rows = [self._sel]
        return rows

    def _remove_selected(self):
        rows = [r for r in self._selected_rows() if r != self._running]
        if not rows:
            return
        self._commit_selected()
        self._remove_rows(rows)

    def _clear_finished(self):
        self._commit_selected()
        self._remove_rows([i for i, j in enumerate(self._jobs) if j.status in _Job.FINISHED])

    def _rerun_selected(self):
        rows = [r for r in self._selected_rows() if self._jobs[r].status in _Job.FINISHED]
        if not rows:
            return
        self._commit_selected()
        for r in rows:
            self._requeue(r)
        names = self._jobs[rows[0]].path.name if len(rows) == 1 else f"{len(rows)} video"
        self._set_status(f"{names} đã về hàng đợi", _TXT_DIM)

    def _requeue(self, idx: int):
        job = self._jobs[idx]
        job.status, job.error, job.output = _Job.PENDING, "", None
        job.waiting, job.finished_at = "", None
        job.report_text = job.report_path = ""
        self._update_item(idx)
        if idx == self._sel:
            self._on_select(idx)
        self._refresh_buttons()

    def _sync_panels(self):
        """Panels are editable for every video except the one being processed — that one
        is read-only (its tabs can still be browsed)."""
        running = (0 <= self._sel < len(self._jobs)
                   and self._jobs[self._sel].status == _Job.RUNNING)
        self._opts.set_read_only(running)

    def _remove_rows(self, rows: list[int]):
        # Block list signals: currentRowChanged mid-removal would commit the panels
        # into a job whose index has shifted.
        self._list.blockSignals(True)
        for i in sorted(rows, reverse=True):
            del self._jobs[i]
            self._list.takeItem(i)
            if self._running > i:
                self._running -= 1
        self._list.blockSignals(False)
        self._sel = -1
        self._on_select(self._list.currentRow())

    # ── Cards ──────────────────────────────────────────────────────────────────

    def _add_job(self, job: "_Job", row: "int | None" = None) -> None:
        """Queue *job* (at *row*, default the end): a list item carrying a QueueCard."""
        row = len(self._jobs) if row is None else row
        if 0 <= row <= self._running:
            self._running += 1          # the running video moves down one row
        self._jobs.insert(row, job)
        item = QListWidgetItem()          # the card draws everything (no text under it)
        item.setData(Qt.UserRole, id(job))   # follows the item when a card is dragged
        self._list.insertItem(row, item)
        self._bind_card(row)

    def _bind_card(self, row: int) -> None:
        from dubber.app.queue_card import QueueCard
        job = self._jobs[row]
        card = QueueCard()
        card.action.connect(lambda key, j=job: self._card_action(j, key))
        item = self._list.item(row)
        self._list.setItemWidget(item, card)
        card.show_job(job, self._state_text(job))
        item.setSizeHint(QSize(card.sizeHint().width(), max(card.sizeHint().height(), 50)))

    def _update_item(self, idx: int):
        job = self._jobs[idx]
        item = self._list.item(idx)
        card = self._list.itemWidget(item)
        if card is None:      # lost by a drag & drop move
            self._bind_card(idx)
        else:
            card.show_job(job, self._state_text(job))
        self._refresh_summary()

    def _state_text(self, job: "_Job") -> str:
        """Second line of a running / finished card."""
        from dubber.app.progress import fmt_clock, fmt_left
        if job.status == _Job.RUNNING and getattr(self, "_progress", None) is not None \
                and self._running >= 0 and self._jobs[self._running] is job:
            p = self._progress
            step = dict(_STEPS).get(p.current or "", "Đang khởi động")
            if p.current == "Separate":
                step = self._rows["Separate"]._lbl.text()
            text = f"{step} · tổng {int(p.fraction() * 100)}% · đã chạy {fmt_clock(p.elapsed())}"
            eta = p.eta()
            return text + (f" · còn {fmt_left(eta)}" if eta is not None else "")
        if job.status == _Job.DONE:
            bits = ["Xong" + (f" {job.finished_at:%H:%M}" if job.finished_at else "")]
            if job.run_seconds:
                bits.append(f"trong {fmt_clock(job.run_seconds)}")
            if job.output and job.output.exists():
                bits.append(_fmt_bytes(job.output.stat().st_size))
            return " · ".join(bits)
        return ""

    def _refresh_summary(self) -> None:
        from dubber.app.progress import fmt_left
        n = len(self._jobs)
        if not n:
            self._summary.setText("Chưa có video — kéo thả video vào ô phía trên")
            return
        count = lambda st: sum(j.status == st for j in self._jobs)
        waiting = sum(bool(j.waiting) for j in self._jobs)
        parts = [f"{n} video"]
        for num, label in ((count(_Job.DONE), "xong"), (count(_Job.RUNNING) - waiting, "đang chạy"),
                           (waiting, "chờ bạn"), (count(_Job.PENDING), "chờ chạy"),
                           (count(_Job.ERROR), "lỗi"), (count(_Job.CANCELLED), "đã hủy")):
            if num:
                parts.append(f"{num} {label}")
        eta = self._progress.eta() if getattr(self, "_progress", None) and self._running >= 0 \
            else None
        if eta is not None:
            parts.append(f"video đang chạy còn {fmt_left(eta)}")
        self._summary.setText("  ·  ".join(parts))

    def _on_rows_moved(self, *_):
        """A card was dragged: the list order becomes the queue order."""
        by_item = {id(j): j for j in self._jobs}
        order = []
        for r in range(self._list.count()):
            jid = self._list.item(r).data(Qt.UserRole)
            order.append(by_item.get(jid))
        if None in order or len(order) != len(self._jobs):
            return
        running = self._jobs[self._running] if self._running >= 0 else None
        selected = self._jobs[self._sel] if 0 <= self._sel < len(self._jobs) else None
        self._jobs = order
        self._running = order.index(running) if running is not None else -1
        self._sel = order.index(selected) if selected is not None else -1
        for r in range(len(order)):
            if self._list.itemWidget(self._list.item(r)) is None:
                self._bind_card(r)
        self._set_status("Đã đổi thứ tự hàng đợi", _TXT_DIM)

    def _card_action(self, job: "_Job", key: str) -> None:
        if job not in self._jobs:
            return
        row = self._jobs.index(job)
        if key == "remove":
            if row != self._running:
                self._commit_selected()
                self._remove_rows([row])
        elif key == "cancel":
            self._cancel_current()
        elif key == "rerun":
            self._commit_selected()
            self._requeue(row)
            self._set_status(f"{job.path.name} đã về hàng đợi", _TXT_DIM)
        elif key == "show_error":
            self._show_error(job)
        elif key == "play":
            self._open_path(job.output)
        elif key == "tiktok":
            self._copy_tiktok(job)
        elif key == "open_txt":
            self._open_path(job.output.with_suffix(".txt") if job.output else None)
        elif key == "reveal":
            self._reveal(job.output)
        elif key == "jobdir":
            from dubber.core.utils.paths import job_dir_for
            d = job_dir_for(job.path)
            self._open_path(d if d.exists() else None)
        elif key == "duplicate":
            self._commit_selected()
            import copy
            clone = _Job(job.path, copy.deepcopy(job.opts), copy.deepcopy(job.export))
            clone.info, clone.info_error = job.info, job.info_error
            self._add_job(clone, row + 1)
            self._list.setCurrentRow(row + 1, QItemSelectionModel.ClearAndSelect)
            self._set_status(f"Đã nhân bản {job.path.name} — chỉnh cấu hình bản mới rồi chạy",
                             _TXT_DIM)
        self._refresh_buttons()

    def _context_menu(self, pos) -> None:
        item = self._list.itemAt(pos)
        if item is None:
            return
        row = self._list.row(item)
        if not item.isSelected():
            self._list.setCurrentRow(row, QItemSelectionModel.ClearAndSelect)
        self._build_context_menu(row).exec(self._list.viewport().mapToGlobal(pos))

    def _build_context_menu(self, row: int) -> QMenu:
        """Right-click menu of card *row* (acts on all selected cards where it makes sense)."""
        job = self._jobs[row]
        rows = self._selected_rows()
        menu = QMenu(self)
        done = job.status == _Job.DONE and job.output is not None
        txt = job.output.with_suffix(".txt") if job.output else None

        def add(text, key, enabled=True):
            act = menu.addAction(text, lambda: self._card_action(job, key))
            act.setEnabled(enabled)

        add("▶  Xem video kết quả", "play", done)
        add("📋  Copy prompt TikTok", "tiktok", done)
        add("📝  Mở file prompt TikTok (.txt)", "open_txt", bool(txt and txt.exists()))
        add(f"📂  Hiện video trong {FILE_MANAGER}", "reveal", done)
        add("📁  Mở thư mục cache của video", "jobdir")
        menu.addSeparator()
        if len(rows) > 1:
            menu.addAction(f"↻  Chạy lại {len(rows)} video đã chọn", self._rerun_selected)
        else:
            add("↻  Chạy lại", "rerun", job.status in _Job.FINISHED)
        add("⧉  Nhân bản (chạy với cấu hình khác)", "duplicate", row != self._running)
        others = [r for r in rows if r != row and self._jobs[r].status == _Job.PENDING]
        act = menu.addAction(f"⚙  Áp cấu hình video này cho {len(others)} video đã chọn",
                             lambda: self._apply_config_from(row, others))
        act.setEnabled(bool(others))
        menu.addSeparator()
        if len(rows) > 1:
            menu.addAction(f"✕  Xóa {len(rows)} video đã chọn", self._remove_selected)
        else:
            add("✕  Xóa khỏi hàng đợi", "remove", row != self._running)
        return menu

    def _apply_config_from(self, src: int, rows: list) -> None:
        self._commit_selected()
        import copy
        for r in rows:
            self._jobs[r].opts = copy.deepcopy(self._jobs[src].opts)
            self._jobs[r].export = copy.deepcopy(self._jobs[src].export)
            self._update_item(r)
        self._set_status(f"Đã áp cấu hình của {self._jobs[src].path.name} cho {len(rows)} video",
                         _TXT_DIM)

    def _copy_tiktok(self, job: "_Job") -> None:
        """Prompt + script → clipboard (from the .txt next to the video, else rebuilt)."""
        txt = job.output.with_suffix(".txt") if job.output else None
        try:
            if txt and txt.exists():
                text = txt.read_text(encoding="utf-8")
            else:
                import json
                from dubber.core.translate.tiktok import tiktok_prompt
                from dubber.core.utils.paths import job_dir_for
                tj = job_dir_for(job.path) / "translate.json"
                text = tiktok_prompt(json.loads(tj.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            self._set_status(f"Không lấy được lời thoại của {job.path.name}: {exc}", _RED)
            return
        QApplication.clipboard().setText(text)
        self._set_status(f"✓ Đã chép prompt TikTok của {job.path.name} — dán vào ChatGPT", _GREEN)

    @staticmethod
    def _open_path(path: "Path | None") -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        if path is not None and Path(path).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    @staticmethod
    def _reveal(target: "Path | None") -> None:
        if not target or not Path(target).exists():
            return
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(target)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(Path(target).parent)])

    def _refresh_buttons(self):
        busy = self._running >= 0
        self._settings_dlg.storage.set_busy(busy)
        has_pending = any(j.status == _Job.PENDING for j in self._jobs)
        self._btn_start.setEnabled(not busy and has_pending)
        self._btn_stop.setEnabled(busy and not self._stop_requested)
        self._btn_cancel.setEnabled(busy and not self._cancelling)
        self._btn_remove.setEnabled(any(r != self._running for r in self._selected_rows()))
        self._btn_clear.setEnabled(any(j.status in _Job.FINISHED for j in self._jobs))
        self._btn_rerun.setEnabled(any(self._jobs[r].status in _Job.FINISHED
                                       for r in self._selected_rows()))
        self._btn_apply_all.setEnabled(has_pending)
        self._btn_open.setEnabled(self._output_path is not None)
        self._drop.set_compact(bool(self._jobs))
        self._refresh_summary()

    def _set_status(self, text: str, color: str):
        self._status.setStyleSheet(f"color: {color}; font-size: 11px;")
        self._status.setText(text)

    def _edit_layout(self, mode: str):
        """Open the frame editor for the selected (pending) video."""
        if not (0 <= self._sel < len(self._jobs)):
            self._set_status("Chọn một video trong hàng đợi trước khi chọn vùng làm mờ / vị trí phụ đề", _AMBER)
            return
        job = self._jobs[self._sel]
        from dubber.app.layout_dialog import LayoutDialog
        blur_rect, sub_pos = self._opts.layout_state()
        family, size = self._opts.sub_font()
        blur_mode = self._opts.blur_mode()
        if mode == "blur" and blur_mode != "off":
            blur_mode = "custom"            # "Chọn vùng…" → manual area
        info = job.info
        dims = (info.width, info.height, info.duration) if info and info.width else None
        dlg = LayoutDialog(job.path, blur_rect, sub_pos, start_mode=mode,
                           font_family=family, font_size=size, blur_mode=blur_mode,
                           style=self._opts.sub_style(), video_info=dims, parent=self)
        if dlg.exec() == QDialog.Accepted:
            self._opts.set_layout(dlg.blur_rect, dlg.sub_pos, dlg.blur_mode)
            self._opts.set_sub_font(dlg.font_family, dlg.font_size)
            self._commit_selected()
        else:
            self._opts._on_layout_changed()

    # ── Sequential run ─────────────────────────────────────────────────────────

    def _start(self):
        self._commit_selected()
        self._stop_requested = False
        self._run_next()

    def _cancel_current(self):
        if self._running < 0 or self._cancelling:
            return
        self._cancelling = True
        self._stop_requested = True   # don't roll on to the next video
        self._worker.cancel()
        self._set_status("Đang hủy… (dừng ở điểm an toàn gần nhất của bước hiện tại)", _AMBER)
        self._refresh_buttons()

    def _on_ask(self, kind: str, payload):
        """The running video waits on the user (translation review / Groq rate limit)."""
        from dubber.app.wait_dialogs import ask_rate_limit
        from dubber.app import notify
        worker = self._worker
        job = self._jobs[self._running] if self._running >= 0 else None
        if job is not None:
            job.waiting = kind
            self._update_item(self._running)
            notify.notify(self, f"{APP_NAME} — cần bạn", {
                "review": f"{job.path.name}: dịch xong, mời bạn duyệt bản dịch",
                "rate_limit": f"{job.path.name}: Groq đang giới hạn",
            }.get(kind, job.path.name))
        if self._progress is not None:
            self._progress.pause()
        if kind == "rate_limit":
            answer = ask_rate_limit(payload, parent=self)
        elif kind == "review":
            from dubber.app.review_dialog import ReviewDialog
            self._set_status(f"{payload['video']}: chờ bạn duyệt bản dịch…", _AMBER)
            dlg = ReviewDialog(payload["video"], payload["rows"], audio=payload["audio"],
                               tts_engine=payload["tts_engine"], voice=payload["voice"],
                               work_dir=payload["work_dir"], parent=self)
            answer = dlg.result_vi if dlg.exec() == QDialog.Accepted else None
            dlg.deleteLater()   # a long video's table is big: don't keep it under the window
        else:
            answer = None
        if job is not None:
            job.waiting = ""
            if self._running >= 0:
                self._update_item(self._running)
        if self._progress is not None:
            self._progress.resume()
        if answer is None:
            self._cancel_current()   # also releases the worker
        else:
            self._set_status("Đang tiếp tục…", _TXT_DIM)
            worker.answer(answer)

    def _on_cancelled(self):
        job = self._jobs[self._running]
        job.status, job.error = _Job.CANCELLED, ""
        self._finish_run(job)
        self._update_item(self._running)
        self._set_status(f"⊘  Đã hủy {job.path.name} — đã xóa file tạm của video", _AMBER)
        self._sync_panels()

    def _request_stop(self):
        self._stop_requested = True
        self._set_status("Sẽ dừng sau khi video hiện tại xong…", _AMBER)
        self._refresh_buttons()

    def _run_next(self):
        nxt = next((i for i, j in enumerate(self._jobs) if j.status == _Job.PENDING), -1)
        if nxt < 0 or self._stop_requested:
            self._running = -1
            self._tick_timer.stop()
            self._progress = None
            if self._ran > 1:
                from dubber.app import notify
                done = sum(j.status == _Job.DONE for j in self._jobs)
                err = sum(j.status == _Job.ERROR for j in self._jobs)
                notify.notify(self, f"{APP_NAME} — hàng đợi xong",
                              f"{done} video xong" + (f", {err} lỗi" if err else ""))
            self._ran = 0
            done = sum(j.status == _Job.DONE for j in self._jobs)
            err = sum(j.status == _Job.ERROR for j in self._jobs)
            if self._cancelling:   # keep the "cancelled" message
                self._cancelling = False
            else:
                msg = ("Đã dừng hàng đợi. " if self._stop_requested and nxt >= 0
                       else "Hàng đợi xong. ")
                self._set_status(f"{msg}✓ {done} thành công  ·  ✗ {err} lỗi",
                                 _GREEN if not err else _AMBER)
            self._stop_requested = False
            self._settings_dlg.storage.refresh_storage()
            self._sync_panels()   # the last video's panels were locked while it ran
            self._refresh_buttons()
            return

        self._commit_selected()
        job = self._jobs[nxt]
        self._running = nxt
        job.status = _Job.RUNNING
        self._update_item(nxt)
        if self._sel == nxt:
            self._opts.set_read_only(True)
        total = len(self._jobs)
        self._prog_box.setTitle(f"Tiến trình — video {nxt + 1}/{total}: {job.path.name}")
        self._set_status(f"Đang xử lý {job.path.name}…", _TXT_DIM)
        for row in self._rows.values():
            row.reset()
        from dubber.app.progress import RunProgress
        self._progress = RunProgress()
        self._tick_timer.start()
        self._refresh_buttons()

        o, e = job.opts, job.export
        self._thread = QThread()
        self._worker = PipelineWorker(
            job.path,
            vi_volume=o["vi"] / 100.0,
            music_volume=o["music"] / 100.0 if o["music_on"] else 0.0,
            cn_voice_volume=o["cn"] / 100.0 if o["cn_on"] else 0.0,
            show_subtitles=o["subs"],
            voice=o["voice"],
            keep_music=o["music_on"],
            keep_cn_voice=o["cn_on"],
            trim_start=o["trim_start"] if o["trim_on"] else 0.0,
            trim_end=(o["trim_end"] or None) if o["trim_on"] else None,
            translator=_translator_of(o),
            asr_on_vocals=o.get("asr_vocals", True),
            tts_engine=o.get("tts_engine", "piper"),
            skip_review=o.get("skip_review", True),
            scale=e["scale"],
            fps=e["fps"],
            crf=e["crf"],
            encoder_preset=e["preset"],
            audio_bitrate=e["audio_br"],
            output_dir=self._out.output_dir(),
            **_layout_kwargs(o),
        )
        self._worker.moveToThread(self._thread)
        self._thread.started      .connect(self._worker.run)
        self._worker.step_started .connect(self._on_started)
        self._worker.step_progress.connect(self._on_progress)
        self._worker.step_done    .connect(self._on_done)
        self._worker.finished     .connect(self._on_finished)
        self._worker.error        .connect(self._on_error)
        self._worker.cancelled    .connect(self._on_cancelled)
        self._worker.error_report .connect(self._on_error_report)
        self._worker.ask          .connect(self._on_ask)
        self._worker.finished     .connect(self._thread.quit)
        self._worker.error        .connect(self._thread.quit)
        self._worker.cancelled    .connect(self._thread.quit)
        self._thread.finished     .connect(self._thread.deleteLater)
        self._thread.finished     .connect(self._run_next)
        self._thread.start()

    def _on_started(self, name: str):
        if self._progress is not None:
            self._progress.started(name)
        if name in self._rows:
            self._rows[name].set_running()
            self._rows[name].set_progress(0)
            job = self._jobs[self._running]
            self._set_status(f"{job.path.name}: đang {dict(_STEPS).get(name, name).lower()}…",
                             _TXT_DIM)
        self._tick()

    def _on_progress(self, name: str, pct: int):
        if self._progress is not None:
            self._progress.progress(name, pct)
        if name in self._rows:
            self._rows[name].set_progress(pct)

    def _on_done(self, name: str):
        if self._progress is not None:
            self._progress.done(name)
        if name in self._rows:
            self._rows[name].set_done()
        self._tick()

    def _tick(self) -> None:
        """Refresh the running card, the step times and the progress title."""
        from dubber.app.progress import fmt_clock, fmt_left
        p = self._progress
        if p is None or not (0 <= self._running < len(self._jobs)):
            return
        for key, row in self._rows.items():
            t = p.step_elapsed(key)
            row.set_time(fmt_clock(t) if t is not None else "")
        job = self._jobs[self._running]
        eta = p.eta()
        title = (f"Tiến trình — video {self._running + 1}/{len(self._jobs)}"
                 f"  ·  {int(p.fraction() * 100)}%  ·  đã chạy {fmt_clock(p.elapsed())}")
        if job.waiting:
            title += "  ·  đang chờ bạn"
        elif eta is not None:
            title += f"  ·  còn {fmt_left(eta)}"
        self._prog_box.setTitle(title)
        self._update_item(self._running)

    def _finish_run(self, job: "_Job") -> None:
        """Common end of a video run (done / error / cancelled)."""
        import datetime
        job.waiting = ""
        job.finished_at = datetime.datetime.now()
        if self._progress is not None:
            job.run_seconds = self._progress.elapsed()
        self._ran += 1

    def _on_finished(self, output: Path):
        job = self._jobs[self._running]
        job.status, job.output = _Job.DONE, output
        self._finish_run(job)
        self._update_item(self._running)
        from dubber.app import notify
        notify.notify(self, f"{APP_NAME} — video xong", f"{job.path.name} → {output.name}")
        self._output_path = output
        size_mb = output.stat().st_size / 1024 / 1024
        self._set_status(f"✓  {job.path.name} → {output}  ({size_mb:.1f} MB)", _GREEN)
        self._sync_panels()

    def _on_error_report(self, text: str, path: str):
        job = self._jobs[self._running]
        job.report_text, job.report_path = text, path

    def _on_error(self, msg: str):
        job = self._jobs[self._running]
        job.status, job.error = _Job.ERROR, msg
        self._finish_run(job)
        self._update_item(self._running)
        from dubber.app import notify
        notify.notify(self, f"{APP_NAME} — lỗi", f"{job.path.name}: {msg[:120]}")
        self._set_status(f"✗  {job.path.name}: {msg}  (bấm “Xem lỗi” trên thẻ video để xem chi tiết)", _RED)
        self._sync_panels()
        self._show_error(job)

    def _show_error(self, job: "_Job"):
        from dubber.app.error_dialog import ErrorDialog
        dlg = ErrorDialog(job.path.name, job.error, job.report_text, job.report_path or None,
                          parent=self)
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        dlg.show()
        self._error_dialog = dlg   # keep a reference (non-modal)

    def _on_item_double_clicked(self, item):
        row = self._list.row(item)
        if 0 <= row < len(self._jobs) and self._jobs[row].status == _Job.ERROR:
            self._show_error(self._jobs[row])

    def _open_folder(self):
        target = self._output_path
        if 0 <= self._sel < len(self._jobs) and self._jobs[self._sel].output:
            target = self._jobs[self._sel].output
        self._reveal(target)

    # ── Remember window, tab and queue between launches ───────────────────────

    def _save_state(self) -> None:
        from dubber.core.utils.settings import load_settings, save_settings
        self._commit_selected()
        queue = []
        for j in self._jobs:
            status = _Job.PENDING if j.status == _Job.RUNNING else j.status   # rerun it
            queue.append({"path": str(j.path), "opts": j.opts, "export": j.export,
                          "status": status, "output": str(j.output) if j.output else None,
                          "error": j.error})
        st = load_settings()
        st["window_geometry"] = bytes(self.saveGeometry().toBase64()).decode()
        st["options_tab"] = self._opts._tabs.currentIndex()
        st["queue"] = queue
        save_settings(st)

    def _restore_state(self) -> None:
        from PySide6.QtCore import QByteArray
        from dubber.core.utils.settings import load_settings
        st = load_settings()
        geo = st.get("window_geometry")
        if geo:
            self.restoreGeometry(QByteArray.fromBase64(geo.encode()))
        tab = st.get("options_tab")
        if isinstance(tab, int) and 0 <= tab < self._opts._tabs.count():
            self._opts._tabs.setCurrentIndex(tab)
        restored = 0
        for q in st.get("queue") or []:
            try:
                path = Path(q["path"])
                if not path.exists():          # moved / deleted since: drop it
                    continue
                job = _Job(path, q["opts"], q["export"])
                if q.get("status") in (_Job.DONE, _Job.ERROR, _Job.CANCELLED):
                    job.status = q["status"]
                job.output = Path(q["output"]) if q.get("output") else None
                job.error = q.get("error") or ""
            except (KeyError, TypeError):
                continue
            self._add_job(job)
            restored += 1
        if restored:
            self._list.setCurrentRow(0, QItemSelectionModel.ClearAndSelect)
            self._probe_next()
            self._set_status(f"Đã mở lại hàng đợi lần trước ({restored} video)", _TXT_DIM)
        self._refresh_buttons()

    def closeEvent(self, event):
        self._closing = True                # don't start reading the next video
        try:
            self._save_state()
        except Exception:   # never block closing the app
            log.exception("Could not save the window / queue state")
        self._probe_runner.wait_all(3000)   # an ffprobe still reading a video
        self._frame_runner.wait_all(3000)
        super().closeEvent(event)


# ── Entry point ────────────────────────────────────────────────────────────────

def run_gui() -> None:
    log_path = _setup_file_logging()
    log.info("%s GUI start (log: %s)", APP_NAME, log_path)
    from dubber.core.utils.api_keys import apply_saved_keys
    apply_saved_keys()   # key saved in the app (OS credential store) → os.environ
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    from PySide6.QtGui import QIcon
    big = _LOGO_DIR / "logo_1024.png"   # not shipped in the Windows bundle (it has icon.ico)
    app.setWindowIcon(QIcon(str(big if big.exists() else _LOGO_DIR / "logo_256.png")))
    register_qt_fonts()
    app.setStyle("Fusion")

    pal = QPalette()
    pal.setColor(QPalette.Window,          QColor(_DARK_BG))
    pal.setColor(QPalette.WindowText,      QColor(_TXT_MAIN))
    pal.setColor(QPalette.Base,            QColor(_SURFACE))
    pal.setColor(QPalette.AlternateBase,   QColor("#242424"))
    pal.setColor(QPalette.Text,            QColor(_TXT_MAIN))
    pal.setColor(QPalette.Button,          QColor("#252525"))
    pal.setColor(QPalette.ButtonText,      QColor(_TXT_MAIN))
    pal.setColor(QPalette.Highlight,       QColor(_ACCENT))
    pal.setColor(QPalette.HighlightedText, QColor("#000"))
    app.setPalette(pal)

    win = MainWindow()
    win.show()
    sys.exit(app.exec())
