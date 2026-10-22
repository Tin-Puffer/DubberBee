"""M6: PySide6 GUI for Dubber — drag-and-drop video, live progress bars."""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtGui import QColor, QPalette, QDragEnterEvent, QDropEvent, QFont, QFontDatabase
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QProgressBar, QFileDialog,
    QFrame, QSizePolicy, QCheckBox, QComboBox,
    QGroupBox, QSlider, QDoubleSpinBox, QSpinBox,
    QListWidget, QListWidgetItem, QLineEdit, QAbstractItemView, QDialog,
    QInputDialog, QMenu, QMessageBox,
    QTabWidget, QFormLayout,
)

_DARK_BG   = "#111"
_PANEL_BG  = "#181818"
_SURFACE   = "#1e1e1e"
_BORDER    = "#2a2a2a"
_ACCENT    = "#4db8ff"
_GREEN     = "#4caf50"
_AMBER     = "#ffb74d"
_RED       = "#f44336"
_TXT_DIM   = "#555"
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
        asr_on_vocals: bool = True,
        tts_engine: str = "piper",
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
        self.sub_font        = sub_font
        self.sub_size        = sub_size
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
        keys = ("translator", "tts_engine", "voice", "asr_on_vocals", "trim_start", "trim_end",
                "vi_volume", "music_volume", "cn_voice_volume", "keep_music", "keep_cn_voice",
                "show_subtitles", "scale", "fps", "crf", "encoder_preset", "audio_bitrate",
                "blur_mode", "sub_font", "sub_size", "output_dir")
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

    def _manual_translate(self, script: Path, count: int, can_reuse: bool, error):
        """Copy the Chinese script next to the output, then wait for the Vietnamese file."""
        import shutil
        folder = Path(self.output_dir) if self.output_dir else self.input_path.parent
        dest = folder / f"{self.input_path.stem}_zh.txt"
        if not dest.exists() or dest not in self._created:
            folder.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(script, dest)
            self._created.append(dest)
        answer = self._ask("manual", {"video": self.input_path.name, "script": dest,
                                      "count": count, "can_reuse": can_reuse, "error": error})
        if answer is None:
            from dubber.core.utils.cancel import JobCancelled
            raise JobCancelled("Đã hủy")
        return answer

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
                    raise RuntimeError("Giọng clone của video này đã bị xóa — chọn giọng khác")
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
                            f"FFmpeg trim thất bại:\n{result.stderr[-2000:]}"
                        )
                    part.replace(trimmed)
                input_path = trimmed

            check()
            pipeline = Pipeline(ffmpeg=ffmpeg, ffprobe=ffprobe, job_dir=job_dir)
            pipeline.cancel_check = check
            pipeline.manual_translate = self._manual_translate
            pipeline.on_rate_limit = lambda info: self._ask("rate_limit", info) or "continue"

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

            _, _, _, _, output_mp4 = pipeline.run_m5(
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
            # Keep the cacheable intermediates; drop the rendered videos (the result was
            # copied to the output folder) and job dirs beyond the most recent few.
            elif job_dir is not None:
                from dubber.core.utils.paths import prune_jobs, slim_job_dir
                slim_job_dir(job_dir)
                prune_jobs()


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

        self._label = QLabel("Kéo thả video vào đây (được nhiều file)  ·  hoặc  click để chọn", self)
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

    def mousePressEvent(self, event):
        ps, _ = QFileDialog.getOpenFileNames(
            self, "Chọn video", str(Path.home()),
            "Video (*.mp4 *.mkv *.mov *.avi *.webm)",
        )
        if ps: self.files_chosen.emit([Path(p) for p in ps])


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
    lbl.setStyleSheet(f"color: {_TXT_DIM}; font-size: 11px;")
    return lbl


# One short line under each option: what you gain / lose (also the widget's tooltip)
_HINTS_ENGINE = {
    "piper":  "Nhanh, giọng hơi đều",
    "vieneu": "Giọng tự nhiên hơn, xử lý lâu hơn (lần đầu tải ~610 MB)",
}
_HINT_CLONE_ITEM = "Gửi đoạn ghi âm 5–8 giây rõ tiếng, một người nói"
_HINTS_TRANSLATOR = {
    "auto":  "Dùng Groq, mất mạng thì tự chuyển sang MarianMT",
    "groq":  "Dịch hay nhất, cần mạng và khóa Groq (đặt ở dòng “Khóa Groq” bên dưới)",
    "local": "Không cần mạng, nhanh, dịch kém hơn",
    "nllb":  "Không cần mạng, dịch khá hơn, chậm, tải ~5,5 GB, cần ~6 GB RAM",
    "manual": "Xuất file .txt tiếng Trung, chờ bạn tải file .txt tiếng Việt lên rồi làm tiếp",
}
_HINT_ASR_VOCALS = "Nhận diện chính xác hơn với video có nhạc, chậm hơn một chút"
_HINT_TRIM = "Chỉ lồng tiếng đoạn đã chọn, chạy nhanh hơn khi thử"
_HINT_SUBS = "Chèn phụ đề tiếng Việt vào video"
_HINTS_RES = {None: "Giữ nguyên như video gốc"}
_HINT_RES_OTHER = "Thấp hơn = file nhỏ hơn, xuất nhanh hơn"
_HINT_FPS = "Để “Gốc” nếu không cần đổi"
_HINT_CRF = "Cao = nét hơn nhưng file nặng hơn"
_HINT_PRESET = "Nhanh = xuất lẹ nhưng file nặng hơn; chậm = file nhẹ hơn"
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
        form.addRow("Bộ đọc", self._combo_engine)
        _add_hint(form, self._combo_engine, _HINTS_ENGINE)

        self._default_piper = PIPER_MODEL_NAME.removesuffix(".onnx")
        self._last_voice = {"piper": self._default_piper, "vieneu": None}
        self._combo_voice = QComboBox(self)
        self._btn_demo = _small_button("▶ Nghe thử", self)
        self._btn_demo.setToolTip("Đọc một câu mẫu bằng giọng đang chọn")
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
        self._combo_translator.addItem("Thủ công — tự dịch file",           userData="manual")
        form.addRow("Dịch thuật", self._combo_translator)
        _add_hint(form, self._combo_translator, _HINTS_TRANSLATOR)

        self._lbl_groq = QLabel("", self)
        self._btn_groq = _small_button("🔑 Đặt khóa Groq…", self)
        self._btn_groq.setToolTip("Nhập / kiểm tra / xóa khóa Groq (lưu trong kho mật khẩu máy)")
        self._btn_groq.clicked.connect(self._edit_groq_key)
        form.addRow("Khóa Groq", _hbox(self._lbl_groq, self._btn_groq))
        self._refresh_groq_key()

        import importlib.util
        self._mdxnet = importlib.util.find_spec("audio_separator") is not None
        self._chk_asr_vocals = QCheckBox("Lọc nhạc trước khi nhận diện", self)
        self._chk_asr_vocals.setChecked(self._mdxnet)
        self._chk_asr_vocals.setEnabled(self._mdxnet)
        self._chk_asr_vocals.toggled.connect(self.sep_label_changed)
        form.addRow("Nhận diện", self._chk_asr_vocals)
        _add_hint(form, self._chk_asr_vocals, _HINT_ASR_VOCALS if self._mdxnet else
                  "Chưa cài bộ tách nhạc (audio-separator) — nhận diện trên âm thanh gốc")

        self._chk_trim = QCheckBox("Chỉ xử lý một đoạn", self)
        self._chk_trim.setChecked(False)
        self._chk_trim.toggled.connect(self._on_trim_toggled)
        form.addRow("Cắt video", self._chk_trim)
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

        self._chk_subs = QCheckBox("Đốt phụ đề tiếng Việt vào video", self)
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
        self._spin_size.setToolTip("Cỡ chữ trên khung 1080p — tự co giãn theo độ phân giải video")
        self._btn_preview = _small_button("Xem trước trên video…", self)
        self._btn_preview.setToolTip("Xem chữ trên khung hình thật của video, giống hệt khi xuất")
        self._btn_preview.clicked.connect(lambda: self.layout_edit_requested.emit("preview"))
        form.addRow("Cỡ chữ", _hbox(self._spin_size, self._btn_preview, stretch_last=False))

        self._combo_sub = QComboBox(self)
        self._combo_sub.addItem("Mặc định (giữa, sát đáy)", userData="default")
        self._combo_sub.addItem("Tự chọn trên khung hình", userData="custom")
        self._btn_sub = _small_button("Chọn vị trí…", self)
        self._btn_sub.clicked.connect(lambda: self.layout_edit_requested.emit("sub"))
        form.addRow("Vị trí", _hbox(self._combo_sub, self._btn_sub, stretch_first=True))

        self._chk_blur = QCheckBox("Làm mờ nền phụ đề (che phụ đề Trung)", self)
        self._chk_blur.setChecked(True)
        form.addRow("Làm mờ", self._chk_blur)
        self._combo_blur = QComboBox(self)
        self._combo_blur.addItem("Tự động (sau phụ đề Việt)", userData="auto")
        self._combo_blur.addItem("Vùng tự chọn (cả video)", userData="custom")
        self._btn_blur = _small_button("Chọn vùng…", self)
        self._btn_blur.clicked.connect(lambda: self.layout_edit_requested.emit("blur"))
        form.addRow("Vùng mờ", _hbox(self._combo_blur, self._btn_blur, stretch_first=True))

        self._lbl_layout = _hint_label(self)
        form.addRow("", self._lbl_layout)
        _finish_form(form)
        self._tabs.addTab(_scrollable(page_sub), "Phụ đề")

        self._chk_blur.toggled.connect(self._on_layout_changed)
        self._combo_blur.currentIndexChanged.connect(self._on_blur_mode_changed)
        self._combo_sub.currentIndexChanged.connect(self._on_sub_mode_changed)
        self._on_layout_changed()

    def add_page(self, widget: QWidget, title: str) -> None:
        """Extra tab (the export settings live here too, so they disable together)."""
        self._tabs.addTab(widget, title)

    # ── Layout (blur / subtitle position) ─────────────────────────────────────

    def _on_blur_mode_changed(self, *_):
        if self._combo_blur.currentData() == "custom" and not self._blur_rect and self.isEnabled():
            self.layout_edit_requested.emit("blur")
        self._on_layout_changed()

    def _on_sub_mode_changed(self, *_):
        if self._combo_sub.currentData() == "custom" and not self._sub_pos and self.isEnabled():
            self.layout_edit_requested.emit("sub")
        self._on_layout_changed()

    def _on_layout_changed(self, *_):
        blur_on = self._chk_blur.isChecked()
        self._combo_blur.setEnabled(blur_on)
        custom_blur = blur_on and self._combo_blur.currentData() == "custom"
        self._btn_blur.setEnabled(custom_blur)
        self._btn_sub.setEnabled(self._combo_sub.currentData() == "custom")
        parts = []
        if not blur_on:
            parts.append("Không làm mờ")
        elif custom_blur:
            parts.append("Vùng mờ: " + ("đã chọn" if self._blur_rect else "⚠ chưa chọn → dùng tự động"))
        if self._combo_sub.currentData() == "custom":
            parts.append("Phụ đề: " + ("đã chọn vị trí" if self._sub_pos else "⚠ chưa chọn → mặc định"))
        self._lbl_layout.setText("  ·  ".join(parts))

    def set_layout(self, blur_rect, sub_pos) -> None:
        """Store results from the frame editor (normalized coords)."""
        self._blur_rect = tuple(blur_rect) if blur_rect else None
        self._sub_pos = tuple(sub_pos) if sub_pos else None
        if self._blur_rect:
            _select_data(self._combo_blur, "custom")
        if self._sub_pos:
            _select_data(self._combo_sub, "custom")
        self._on_layout_changed()

    def layout_state(self) -> tuple:
        return self._blur_rect, self._sub_pos

    def sub_font(self) -> tuple[str, int]:
        return self._combo_font.currentData(), self._spin_size.value()

    def set_sub_font(self, family: str, size: int) -> None:
        _select_data(self._combo_font, family)
        self._spin_size.setValue(size)

    # ── Internal slots ────────────────────────────────────────────────────────

    def _on_audio_toggled(self, *_):
        music = self._chk_music.isChecked()
        voice = self._chk_cn_voice.isChecked()
        self._sld_music.setEnabled(music)
        self._lbl_music_vol.setEnabled(music)
        self._sld_cn_voice.setEnabled(voice)
        self._lbl_cn_vol.setEnabled(voice)
        if music and voice:
            msg = "Chỉnh riêng nhạc nền và tiếng Trung (tách bằng AI nếu hai mức khác nhau)"
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

    def _refresh_groq_key(self):
        from dubber.core.utils import api_keys
        src = api_keys.source()
        if src == "app":
            text, color = f"✓ Đã lưu ({api_keys.mask(api_keys.get_saved())})", _GREEN
        elif src == "env":
            text, color = f"Từ biến môi trường ({api_keys.mask(api_keys.current())})", _TXT_MID
        else:
            text, color = "Chưa có — Groq / Tự động sẽ dịch bằng MarianMT", _AMBER
        self._lbl_groq.setText(text)
        self._lbl_groq.setStyleSheet(f"color: {color}; font-size: 12px;")

    def _edit_groq_key(self):
        from dubber.app.api_key_dialog import GroqKeyDialog
        GroqKeyDialog(self).exec()
        self._refresh_groq_key()

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
            text = tip = "Giọng bạn đã clone — ✎ đổi tên, 🗑 xóa"
        else:
            text = "Muốn giọng riêng: chọn “➕ Clone giọng mới…”"
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
        from dubber.app.voice_dialog import _Task
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
        QMessageBox.warning(self, "Nghe thử giọng", f"Không tạo được câu mẫu:\n{msg}")

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
            "asr_vocals": self._chk_asr_vocals.isChecked(),
            "trim_on": self._chk_trim.isChecked(),
            "trim_start": self._trim.trim()[0],
            "trim_end": self._trim.trim()[1] or 0.0,   # 0 = until the end
            "blur_on": self._chk_blur.isChecked(),
            "blur_mode": self._combo_blur.currentData(),
            "blur_rect": self._blur_rect,
            "sub_mode": self._combo_sub.currentData(),
            "sub_pos": self._sub_pos,
            "sub_font": self._combo_font.currentData(),
            "sub_size": self._spin_size.value(),
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
        _select_data(self._combo_translator, cfg["translator"])
        self._chk_asr_vocals.setChecked(cfg.get("asr_vocals", True) and self._mdxnet)
        self._chk_trim.setChecked(cfg["trim_on"])
        self._trim.set_trim(cfg["trim_start"], cfg["trim_end"] or None)
        # Layout keys: .get() so configs saved before this feature still load
        self._blur_rect = cfg.get("blur_rect")
        self._sub_pos = cfg.get("sub_pos")
        self._combo_blur.blockSignals(True); self._combo_sub.blockSignals(True)
        self._chk_blur.setChecked(cfg.get("blur_on", True))
        _select_data(self._combo_blur, cfg.get("blur_mode", "auto"))
        _select_data(self._combo_sub, cfg.get("sub_mode", "default"))
        from dubber.core.video.fonts import DEFAULT_FONT, DEFAULT_FONT_SIZE
        _select_data(self._combo_font, cfg.get("sub_font", DEFAULT_FONT))
        self._spin_size.setValue(cfg.get("sub_size", DEFAULT_FONT_SIZE))
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
        form.addRow("Khung hình/giây", self._combo_fps)
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
        for name, label in (("ultrafast", "Rất nhanh (file to)"), ("fast", "Nhanh"),
                            ("medium", "Cân bằng"), ("slow", "Chậm (file nhỏ hơn)")):
            self._combo_preset.addItem(label, name)
        self._combo_preset.setCurrentIndex(2)
        form.addRow("Tốc độ mã hoá", self._combo_preset)
        _add_hint(form, self._combo_preset, _HINT_PRESET)

        self._combo_audio_br = QComboBox(self)
        for k in (96, 128, 192, 256, 320):
            self._combo_audio_br.addItem(f"{k} kbps", f"{k}k")
        self._combo_audio_br.setCurrentIndex(2)
        form.addRow("Âm thanh", self._combo_audio_br)
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

    _ROWS = [("res", "Độ phân giải"), ("fps", "Khung hình/giây"), ("dur", "Thời lượng"),
             ("size", "Dung lượng"), ("vcodec", "Video"), ("audio", "Âm thanh")]

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

    def show_info(self, info, error: str = "") -> None:
        if error or info is None:
            for v in self._val.values():
                v.setText("—")
            if error:
                self._val["res"].setText(f"Không đọc được: {error}")
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
            self._val["audio"].setText("⚠ Không có âm thanh — không lồng tiếng được")
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

        hint = QLabel("Tên file: <tên gốc>_vi.mp4 — không ghi đè file đã có", self)
        hint.setStyleSheet(f"color: {_TXT_DIM}; font-size: 10px;")
        lay.addWidget(hint)

        # Temporary files: cleaned automatically (≤10 videos, ≤5 GB, ≤7 days); this
        # shows how much they use and empties them on demand.
        row = QHBoxLayout(); row.setSpacing(8)
        self._lbl_storage = QLabel("", self)
        self._lbl_storage.setStyleSheet(f"color: {_TXT_DIM}; font-size: 11px;")
        self._lbl_storage.setToolTip(
            "File tạm giúp chạy lại cùng video nhanh hơn (không phải nhận diện / dịch lại).\n"
            "Tự dọn: giữ tối đa 10 video gần nhất, tổng ≤ 5 GB, không quá 7 ngày.")
        self._btn_clean = QPushButton("Dọn ngay", self)
        self._btn_clean.setFixedHeight(26)
        self._btn_clean.setStyleSheet(_SMALL_BTN_STYLE)
        self._btn_clean.clicked.connect(self._clean_storage)
        self._btn_workspace = QPushButton("📁 Thư mục làm việc…", self)
        self._btn_workspace.setToolTip("Mở / đổi nơi lưu file tạm, log, báo cáo lỗi, giọng clone")
        self._btn_workspace.setFixedHeight(26)
        self._btn_workspace.setStyleSheet(_SMALL_BTN_STYLE)
        self._btn_workspace.clicked.connect(self._open_workspace)
        row.addWidget(self._lbl_storage, 1)
        row.addWidget(self._btn_workspace)
        row.addWidget(self._btn_clean)
        lay.addLayout(row)
        self.refresh_storage()

        out_dir = self._settings.get("output_dir")
        self._edit_dir.setText(out_dir or "")
        self._chk_beside.setChecked(not out_dir)
        self._chk_beside.toggled.connect(self._on_beside_toggled)
        self._on_beside_toggled(self._chk_beside.isChecked())

    def refresh_storage(self) -> None:
        from dubber.core.tts.demo import demo_dir
        from dubber.core.utils.paths import _dir_size, jobs_size
        demos = demo_dir()
        size = jobs_size() + (_dir_size(demos) if demos.is_dir() else 0)
        self._storage_bytes = size
        self._lbl_storage.setText(f"Bộ nhớ tạm: {_fmt_bytes(size)} · tự dọn sau 7 ngày / 5 GB")
        self._btn_clean.setEnabled(size > 0 and not getattr(self, "_busy", False))

    def set_busy(self, busy: bool) -> None:
        """No cleaning while a video is being processed (its files are in use)."""
        self._busy = busy
        self._btn_clean.setEnabled(not busy and getattr(self, "_storage_bytes", 0) > 0)

    def _open_workspace(self):
        from dubber.app.workspace_dialog import WorkspaceDialog
        dlg = WorkspaceDialog(busy=getattr(self, "_busy", False),
                              on_logs_changed=_setup_file_logging, parent=self)
        dlg.exec()
        self.refresh_storage()

    def _clean_storage(self):
        import shutil
        from dubber.core.tts.demo import demo_dir
        from dubber.core.utils.paths import clear_jobs
        if QMessageBox.question(
            self, "Dọn bộ nhớ tạm",
            f"Xóa toàn bộ file tạm ({_fmt_bytes(self._storage_bytes)})?\n"
            "Video đã xuất và giọng clone không bị ảnh hưởng; chỉ là lần sau chạy lại "
            "một video sẽ phải nhận diện và dịch lại từ đầu.",
        ) != QMessageBox.Yes:
            return
        clear_jobs()
        shutil.rmtree(demo_dir(), ignore_errors=True)
        self.refresh_storage()

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
        self._lbl.setFixedWidth(180)
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

        lay.addWidget(self._dot)
        lay.addWidget(self._lbl)
        lay.addWidget(self._bar, 1)
        lay.addWidget(self._pct)

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


# ── Main window ────────────────────────────────────────────────────────────────

_STEPS = [
    ("Separate",  "Tách nhạc/giọng"),   # first: runs before ASR when filtering music
    ("ASR",       "Nhận dạng giọng nói"),
    ("Translate", "Dịch thuật (Groq)"),
    ("TTS",       "Tổng hợp giọng Việt"),
    ("Mux",       "Ghép âm thanh"),
    ("Render",    "Xuất video cuối"),
]


def _layout_kwargs(o: dict) -> dict:
    """Per-video config → run_m5 blur/subtitle-position arguments."""
    if not o.get("blur_on", True):
        blur_mode = "off"
    elif o.get("blur_mode") == "custom" and o.get("blur_rect"):
        blur_mode = "custom"
    else:
        blur_mode = "auto"
    from dubber.core.video.fonts import DEFAULT_FONT, DEFAULT_FONT_SIZE
    return {
        "blur_mode": blur_mode,
        "blur_rect": o.get("blur_rect") if blur_mode == "custom" else None,
        "sub_pos": o.get("sub_pos") if o.get("sub_mode") == "custom" else None,
        "sub_font": o.get("sub_font", DEFAULT_FONT),
        "sub_size": o.get("sub_size", DEFAULT_FONT_SIZE),
    }


class _Job:
    """One queued video: its own options/export config + run state."""

    PENDING, RUNNING, DONE, ERROR, CANCELLED = "pending", "running", "done", "error", "cancelled"
    FINISHED = (DONE, ERROR, CANCELLED)   # can be edited and queued again
    _ICON = {PENDING: "⏳", RUNNING: "▶", DONE: "✓", ERROR: "✗", CANCELLED: "⊘"}

    def __init__(self, path: Path, opts: dict, export: dict):
        self.path = path
        self.opts = opts
        self.export = export
        self.status = _Job.PENDING
        self.output: Path | None = None
        self.error = ""
        self.report_text = ""     # full error report (pop-up)
        self.report_path = ""
        self.info = None          # VideoInfo, probed on first selection
        self.info_error = ""

    def label(self) -> str:
        return f"{self._ICON[self.status]}   {self.path.name}"


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Dubber — Lồng tiếng Trung → Việt")
        self.setMinimumWidth(1040)
        self.setStyleSheet(_BASE_STYLE)
        self._jobs: list[_Job] = []
        self._sel: int = -1           # index of job whose config is shown in the panels
        self._running: int = -1       # index of job currently processing
        self._stop_requested = False
        self._cancelling = False
        self._output_path: Path | None = None
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(10)

        # ── Header ─────────────────────────────────────────────────────────────
        hdr = QHBoxLayout(); hdr.setSpacing(0)
        title = QLabel("Dubber", self)
        title.setStyleSheet(
            "font-size: 20px; font-weight: bold; color: #fff;"
            f" letter-spacing: -0.5px;"
        )
        badge = QLabel("ZH → VI", self)
        badge.setStyleSheet(
            f"background: rgba(77, 184, 255, 0.14); color: {_ACCENT}; font-size: 10px;"
            " font-weight: bold; border-radius: 4px; padding: 2px 7px;"
            " margin-left: 10px;"
        )
        hdr.addWidget(title)
        hdr.addWidget(badge)
        hdr.addStretch()
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

        queue_box = QGroupBox("Hàng đợi — chọn một video để chỉnh cấu hình riêng", self)
        q_lay = QVBoxLayout(queue_box)
        q_lay.setContentsMargins(10, 14, 10, 10)
        q_lay.setSpacing(6)
        self._list = QListWidget(self)
        self._list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._list.setMinimumHeight(160)
        self._list.setStyleSheet(
            f"QListWidget {{ background: {_SURFACE}; border: 1px solid {_BORDER};"
            f" border-radius: 6px; font-size: 12px; color: {_TXT_MAIN}; }}"
            " QListWidget::item { padding: 5px 6px; }"
            f" QListWidget::item:selected {{ background: rgba(77, 184, 255, 0.22); color: {_TXT_BRIGHT}; }}"
        )
        self._list.currentRowChanged.connect(self._on_select)
        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)
        q_lay.addWidget(self._list, 1)

        q_btns = QHBoxLayout(); q_btns.setSpacing(6)
        self._btn_apply_all = QPushButton("Áp dụng cấu hình này cho tất cả", self)
        self._btn_rerun = QPushButton("↻ Chạy lại", self)
        self._btn_rerun.setToolTip("Đưa video đã xong / lỗi / đã hủy về hàng đợi để chạy lại")
        self._btn_remove = QPushButton("Xoá", self)
        self._btn_clear = QPushButton("Xoá xong/lỗi", self)
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
        self._btn_cancel.setToolTip("Dừng ngay video đang chạy và xóa các file đã tạo cho nó;"
                                    " hàng đợi cũng dừng lại")
        self._btn_cancel.setEnabled(False)
        self._btn_cancel.setFixedHeight(36)
        self._btn_cancel.setStyleSheet(_SMALL_BTN_STYLE)
        self._btn_cancel.clicked.connect(self._cancel_current)
        btns.addWidget(self._btn_cancel, 2)

        self._btn_open = QPushButton("📂  Mở thư mục", self)
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

    def _update_separate_label(self, *_):
        music = self._opts.keep_music()
        voice = self._opts.keep_cn_voice()
        mv = self._opts._sld_music.value()
        vv = self._opts._sld_cn_voice.value()
        diff_vol = music and voice and abs(mv - vv) >= 1

        if self._opts.asr_on_vocals():
            lbl, will_run = "Tách nhạc/giọng → lọc nhạc", True
        elif music and not voice:
            lbl, will_run = "Tách nhạc/giọng → giữ nhạc", True
        elif voice and not music:
            lbl, will_run = "Tách nhạc/giọng → giữ giọng", True
        elif diff_vol:
            lbl, will_run = "Tách nhạc/giọng → mix riêng", True
        else:
            lbl, will_run = "Tách nhạc/giọng (bỏ qua)", False

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
            self._jobs.append(_Job(p, self._opts.get_config(), self._export.get_config()))
            self._list.addItem(QListWidgetItem(self._jobs[-1].label()))
            known[p.resolve()] = self._jobs[-1]
            added += 1
        if added and self._sel < 0:
            self._list.setCurrentRow(0)
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
            state = {"pending": "", "running": " — đang xử lý, không sửa được",
                     "done": " — đã xong", "error": " — lỗi",
                     "cancelled": " — đã hủy"}[job.status]
            if job.status in _Job.FINISHED:
                state += " · ↻ Chạy lại để chạy lần nữa"
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
            self._opts.setEnabled(True)
            self._export.setEnabled(True)
            self._info.show_info(None)
            self._export.set_source(None)
            self._lbl_editing.setText("Cấu hình mặc định cho video sẽ thêm")
        self._refresh_buttons()

    def _show_source_info(self, job: "_Job") -> None:
        if job.info is None and not job.info_error:
            from dubber.core.video.info import probe_info
            try:
                job.info = probe_info(job.path)
            except Exception as exc:  # unreadable / missing file
                job.info_error = str(exc)[:120]
        self._info.show_info(job.info, job.info_error)
        self._export.set_source(job.info)
        self._opts.set_trim_limit(job.info.duration if job.info else 0.0)

    def _apply_to_all(self):
        opts, export = self._opts.get_config(), self._export.get_config()
        n = 0
        for job in self._jobs:
            if job.status == _Job.PENDING:
                job.opts, job.export = dict(opts), dict(export)
                n += 1
        self._set_status(f"Đã áp dụng cấu hình cho {n} video đang chờ", _TXT_DIM)

    def _remove_selected(self):
        row = self._sel
        if not (0 <= row < len(self._jobs)) or row == self._running:
            return
        self._commit_selected()
        self._remove_rows([row])

    def _clear_finished(self):
        self._commit_selected()
        self._remove_rows([i for i, j in enumerate(self._jobs) if j.status in _Job.FINISHED])

    def _rerun_selected(self):
        if 0 <= self._sel < len(self._jobs) and self._jobs[self._sel].status in _Job.FINISHED:
            self._commit_selected()
            self._requeue(self._sel)
            self._set_status(f"{self._jobs[self._sel].path.name} đã về hàng đợi", _TXT_DIM)

    def _requeue(self, idx: int):
        job = self._jobs[idx]
        job.status, job.error, job.output = _Job.PENDING, "", None
        job.report_text = job.report_path = ""
        self._update_item(idx)
        if idx == self._sel:
            self._on_select(idx)
        self._refresh_buttons()

    def _sync_panels(self):
        """Panels are editable for every video except the one being processed."""
        editable = not (0 <= self._sel < len(self._jobs)
                        and self._jobs[self._sel].status == _Job.RUNNING)
        self._opts.setEnabled(editable)
        self._export.setEnabled(editable)

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

    def _update_item(self, idx: int):
        self._list.item(idx).setText(self._jobs[idx].label())

    def _refresh_buttons(self):
        busy = self._running >= 0
        self._out.set_busy(busy)
        has_pending = any(j.status == _Job.PENDING for j in self._jobs)
        self._btn_start.setEnabled(not busy and has_pending)
        self._btn_stop.setEnabled(busy and not self._stop_requested)
        self._btn_cancel.setEnabled(busy and not self._cancelling)
        self._btn_remove.setEnabled(0 <= self._sel < len(self._jobs) and self._sel != self._running)
        self._btn_clear.setEnabled(any(j.status in _Job.FINISHED for j in self._jobs))
        self._btn_rerun.setEnabled(0 <= self._sel < len(self._jobs)
                                   and self._jobs[self._sel].status in _Job.FINISHED)
        self._btn_apply_all.setEnabled(has_pending)
        self._btn_open.setEnabled(self._output_path is not None)

    def _set_status(self, text: str, color: str):
        self._status.setStyleSheet(f"color: {color}; font-size: 11px;")
        self._status.setText(text)

    def _edit_layout(self, mode: str):
        """Open the frame editor for the selected (pending) video."""
        if not (0 <= self._sel < len(self._jobs)):
            self._set_status("Chọn một video trong hàng đợi trước khi chọn vùng mờ / vị trí phụ đề", _AMBER)
            return
        job = self._jobs[self._sel]
        from dubber.app.layout_dialog import LayoutDialog
        blur_rect, sub_pos = self._opts.layout_state()
        family, size = self._opts.sub_font()
        blur_mode = "auto" if self._opts._chk_blur.isChecked() else "off"
        dlg = LayoutDialog(job.path, blur_rect, sub_pos, start_mode=mode,
                           font_family=family, font_size=size, blur_mode=blur_mode, parent=self)
        if dlg.exec() == QDialog.Accepted:
            self._opts.set_layout(dlg.blur_rect, dlg.sub_pos)
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
        """The running video waits on the user (manual translation / Groq rate limit)."""
        from dubber.app.manual_dialog import ManualTranslateDialog, ask_rate_limit
        worker = self._worker
        if kind == "manual":
            self._set_status(f"{payload['video']}: chờ bạn tải file tiếng Việt…", _AMBER)
            dlg = ManualTranslateDialog(payload["video"], payload["script"], payload["count"],
                                        payload["can_reuse"], payload["error"], parent=self)
            answer = dlg.answer if dlg.exec() == QDialog.Accepted else None
        elif kind == "rate_limit":
            answer = ask_rate_limit(payload, parent=self)
        else:
            answer = None
        if answer is None:
            self._cancel_current()   # also releases the worker
        else:
            self._set_status("Đang tiếp tục…", _TXT_DIM)
            worker.answer(answer)

    def _on_cancelled(self):
        job = self._jobs[self._running]
        job.status, job.error = _Job.CANCELLED, ""
        self._update_item(self._running)
        self._set_status(f"⊘  Đã hủy {job.path.name} — đã xóa các file tạm của video này", _AMBER)
        self._sync_panels()

    def _request_stop(self):
        self._stop_requested = True
        self._set_status("Sẽ dừng sau khi video hiện tại xong…", _AMBER)
        self._refresh_buttons()

    def _run_next(self):
        nxt = next((i for i, j in enumerate(self._jobs) if j.status == _Job.PENDING), -1)
        if nxt < 0 or self._stop_requested:
            self._running = -1
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
            self._out.refresh_storage()
            self._sync_panels()   # the last video's panels were locked while it ran
            self._refresh_buttons()
            return

        self._commit_selected()
        job = self._jobs[nxt]
        self._running = nxt
        job.status = _Job.RUNNING
        self._update_item(nxt)
        if self._sel == nxt:
            self._opts.setEnabled(False)
            self._export.setEnabled(False)
        total = len(self._jobs)
        self._prog_box.setTitle(f"Tiến trình — video {nxt + 1}/{total}: {job.path.name}")
        self._set_status(f"Đang xử lý {job.path.name}…", _TXT_DIM)
        for row in self._rows.values():
            row.reset()
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
            translator=o["translator"],
            asr_on_vocals=o.get("asr_vocals", True),
            tts_engine=o.get("tts_engine", "piper"),
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
        if name in self._rows:
            self._rows[name].set_running()
            self._rows[name].set_progress(0)
            job = self._jobs[self._running]
            self._set_status(f"{job.path.name}: đang {name}…", _TXT_DIM)

    def _on_progress(self, name: str, pct: int):
        if name in self._rows:
            self._rows[name].set_progress(pct)

    def _on_done(self, name: str):
        if name in self._rows:
            self._rows[name].set_done()

    def _on_finished(self, output: Path):
        job = self._jobs[self._running]
        job.status, job.output = _Job.DONE, output
        self._update_item(self._running)
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
        self._update_item(self._running)
        self._set_status(f"✗  {job.path.name}: {msg}  (bấm đúp vào video để xem lại lỗi)", _RED)
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
        if not target: return
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(target)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target.parent)])


# ── Entry point ────────────────────────────────────────────────────────────────

def run_gui() -> None:
    log_path = _setup_file_logging()
    log.info("Dubber GUI start (log: %s)", log_path)
    from dubber.core.utils.api_keys import apply_saved_keys
    apply_saved_keys()   # key saved in the app (OS credential store) → os.environ
    from dubber.core.utils.paths import prune_jobs
    prune_jobs()   # nothing runs yet: old / oversized job caches can go
    app = QApplication.instance() or QApplication(sys.argv)
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
