"""Review the translation before TTS: edit in place, listen, copy to / paste from a chatbot."""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QDialog, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

from dubber.app.tasks import BusyBar, TaskRunner
from dubber.core.translate.review import chatbot_text, parse_chatbot_reply, row_flags
from dubber.app.theme import FS_HINT, SP_L, SP_M

log = logging.getLogger(__name__)

_AMBER = "#ffb74d"
_RED = "#f44336"
_DIM = "#888"
_EDITED = QColor(255, 183, 77, 40)

COL_NUM, COL_TIME, COL_ZH, COL_VI, COL_NOTE = range(5)
CHUNK = 150   # sentences per "next part" copy: long lists make chatbots skip lines


def _clock(t: float) -> str:
    t = max(0.0, float(t))
    h, rem = divmod(int(t), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _load_instructions() -> str:
    from dubber.core.translate.prompts import get_prompt
    return get_prompt("review")


def edit_instructions(parent) -> None:
    """Edit the chatbot instructions for translation review (saved; can be reset)."""
    from dubber.app.prompt_dialog import edit_prompt
    edit_prompt("review", parent)


class PasteReplyDialog(QDialog):
    """Paste a chatbot reply, preview old → new per sentence, apply the ticked ones.

    Result: ``self.changes`` = {number: new Vietnamese} for the ticked sentences.
    """

    def __init__(self, vi: list, text: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Dán kết quả AI")
        self.resize(900, 620)
        self.changes: dict = {}
        self._vi = vi
        self._parsed: dict = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(SP_L, SP_L, SP_L, SP_L)
        lay.setSpacing(SP_M)
        lay.addWidget(QLabel('Dán câu trả lời của AI (mỗi dòng "[số] câu tiếng Việt"). '
                             "Câu không có trong câu trả lời được giữ nguyên.", self))
        self._edit = QPlainTextEdit(self)
        self._edit.setPlaceholderText("1. Sao anh lại tới đây?\n3. Im hết cho ta!")
        self._edit.setPlainText(text)
        self._edit.textChanged.connect(self._reparse)
        lay.addWidget(self._edit, 2)

        self._info = QLabel("", self)
        self._info.setWordWrap(True)
        lay.addWidget(self._info)
        self._table = QTableWidget(0, 3, self)
        self._table.setHorizontalHeaderLabels(["#", "Bản hiện tại", "Bản mới"])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setWordWrap(True)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.Stretch)
        lay.addWidget(self._table, 3)

        btns = QHBoxLayout()
        b_cancel = QPushButton("Hủy", self)
        b_cancel.clicked.connect(self.reject)
        self._btn_apply = QPushButton("Áp dụng", self)
        self._btn_apply.setDefault(True)
        self._btn_apply.clicked.connect(self._apply)
        btns.addWidget(b_cancel)
        btns.addStretch(1)
        btns.addWidget(self._btn_apply)
        lay.addLayout(btns)
        self._reparse()

    def _reparse(self):
        text = self._edit.toPlainText()
        self._table.setRowCount(0)
        self._parsed = {}
        if not text.strip():
            self._info.setText("")
            self._btn_apply.setEnabled(False)
            return
        try:
            parsed, bad = parse_chatbot_reply(text, len(self._vi))
        except ValueError as exc:
            self._info.setText(f"<span style='color:{_RED}'>{exc}</span>")
            self._btn_apply.setEnabled(False)
            return
        self._parsed = {n: t for n, t in parsed.items() if t != self._vi[n - 1]}
        same = len(parsed) - len(self._parsed)
        msg = f"<b>{len(self._parsed)}</b> câu sẽ thay"
        if same:
            msg += f" · {same} câu giống bản hiện tại"
        if bad:
            shown = ", ".join(map(str, bad[:10])) + (" …" if len(bad) > 10 else "")
            msg += (f" · <span style='color:{_AMBER}'>bỏ qua số không tồn tại: {shown} "
                    f"(chỉ có 1–{len(self._vi)})</span>")
        self._info.setText(msg)
        for n in sorted(self._parsed):
            r = self._table.rowCount()
            self._table.insertRow(r)
            num = QTableWidgetItem(str(n))
            num.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            num.setCheckState(Qt.Checked)
            self._table.setItem(r, 0, num)
            self._table.setItem(r, 1, QTableWidgetItem(self._vi[n - 1]))
            self._table.setItem(r, 2, QTableWidgetItem(self._parsed[n]))
        # after the layout pass: rows sized against the columns' real widths
        QTimer.singleShot(0, self._table.resizeRowsToContents)
        self._btn_apply.setEnabled(bool(self._parsed))

    def _apply(self):
        for r in range(self._table.rowCount()):
            item = self._table.item(r, 0)
            if item.checkState() == Qt.Checked:
                n = int(item.text())
                self.changes[n] = self._parsed[n]
        self.accept()


class ReviewDialog(QDialog):
    """Result: ``self.result_vi`` = Vietnamese text per row (after Tiếp tục), else None."""

    def __init__(self, video: str, rows: list, audio: "Path | None" = None,
                 tts_engine: str = "piper", voice: "str | None" = None,
                 work_dir: "Path | None" = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Duyệt bản dịch — {video}")
        self.resize(1180, 760)
        self.result_vi = None
        self._rows = rows
        self._orig = [r.get("vi_text", "") for r in rows]
        self._vi = list(self._orig)
        self._undo: list = []
        self._filling = False
        self._audio = Path(audio) if audio else None
        self._tts_engine, self._voice = tts_engine, voice
        self._work = Path(work_dir) if work_dir else None
        self._tts = None                 # TTS engine, created on the first 🔊 (slow to load)
        self._player = None
        self._next_chunk = 1
        self._copied = None              # last text put on the clipboard for the chatbot
        self._runner = TaskRunner(self)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(SP_L, SP_L, SP_L, SP_L)
        lay.setSpacing(SP_M)
        head = QLabel(
            f"<b>{len(rows)} câu</b>. Bấm đúp ô <b>Tiếng Việt</b> để sửa, hoặc dùng "
            "<b>Copy cho AI</b> → <b>Dán kết quả AI</b>. <b>Tiếp tục</b> để tạo giọng Việt và "
            "render video (câu không sửa dùng lại giọng đã tạo).", self)
        head.setWordWrap(True)
        lay.addWidget(head)

        top = QHBoxLayout()
        self._search = QLineEdit(self)
        self._search.setPlaceholderText("Tìm trong câu Trung / Việt…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)
        self._chk_flagged = QCheckBox("", self)
        self._chk_flagged.toggled.connect(self._apply_filter)
        top.addWidget(self._search, 1)
        top.addWidget(self._chk_flagged)
        lay.addLayout(top)

        self._table = QTableWidget(len(rows), 5, self)
        self._table.setHorizontalHeaderLabels(
            ["#", "Thời gian", "Tiếng Trung", "Tiếng Việt (sửa được)", "Cần chú ý"])
        self._table.verticalHeader().setVisible(False)
        self._table.setWordWrap(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.DoubleClicked
                                    | QAbstractItemView.EditKeyPressed
                                    | QAbstractItemView.AnyKeyPressed)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(COL_NUM, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(COL_TIME, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(COL_ZH, QHeaderView.Stretch)
        hdr.setSectionResizeMode(COL_VI, QHeaderView.Stretch)
        hdr.setSectionResizeMode(COL_NOTE, QHeaderView.ResizeToContents)
        self._fill()
        self._table.itemChanged.connect(self._on_item_changed)
        lay.addWidget(self._table, 1)

        tools = QHBoxLayout()
        self._btn_src = QPushButton("▶ Nghe câu gốc", self)
        self._btn_src.setToolTip("Phát đoạn âm thanh gốc của câu đang chọn")
        self._btn_src.clicked.connect(self._play_source)
        self._btn_src.setEnabled(bool(self._audio and self._audio.exists() and self._work))
        self._btn_tts = QPushButton("🔊 Nghe thử", self)
        self._btn_tts.setToolTip("Nghe câu tiếng Việt đang chọn bằng giọng của video này")
        self._btn_tts.clicked.connect(self._play_tts)
        self._btn_tts.setEnabled(self._work is not None)
        self._btn_copy = QPushButton("📋 Copy cho AI ▾", self)
        self._copy_menu = QMenu(self)
        self._copy_menu.aboutToShow.connect(self._fill_copy_menu)
        self._btn_copy.setMenu(self._copy_menu)
        self._btn_paste = QPushButton("📥 Dán kết quả AI…", self)
        self._btn_paste.clicked.connect(self._paste)
        self._btn_prompt = QPushButton("✎ Prompt…", self)
        self._btn_prompt.setToolTip("Sửa prompt đặt ở đầu khi copy (được lưu lại)")
        self._btn_prompt.clicked.connect(self._edit_prompt)
        self._btn_undo = QPushButton("↶ Hoàn tác", self)
        self._btn_undo.clicked.connect(self._undo_last)
        for b in (self._btn_src, self._btn_tts, self._btn_copy, self._btn_paste,
                  self._btn_prompt):
            tools.addWidget(b)
        tools.addStretch(1)
        tools.addWidget(self._btn_undo)
        lay.addLayout(tools)
        QShortcut(QKeySequence.Undo, self, activated=self._undo_last)

        self._busy = BusyBar(self)
        lay.addWidget(self._busy)
        self._status = QLabel("", self)
        self._status.setStyleSheet(f"color: {_DIM}; font-size: {FS_HINT}px;")
        lay.addWidget(self._status)

        btns = QHBoxLayout()
        b_cancel = QPushButton("Hủy video này", self)
        b_cancel.clicked.connect(self.reject)
        self._btn_go = QPushButton("Tiếp tục ▶", self)
        self._btn_go.setDefault(True)
        self._btn_go.clicked.connect(self._continue)
        btns.addWidget(b_cancel)
        btns.addStretch(1)
        btns.addWidget(self._btn_go)
        lay.addLayout(btns)
        self._refresh()

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, "_sized", False):   # columns have their real widths only now
            self._sized = True
            QTimer.singleShot(0, self._table.resizeRowsToContents)

    # ── Table ──────────────────────────────────────────────────────────────────

    def _fill(self):
        self._filling = True
        fixed = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        for i, row in enumerate(self._rows):
            cells = {
                COL_NUM: str(i + 1),
                COL_TIME: f"{_clock(row['start'])}–{_clock(row['end'])}",
                COL_ZH: row.get("src_text", ""),
            }
            for col, text in cells.items():
                item = QTableWidgetItem(text)
                item.setFlags(fixed)
                self._table.setItem(i, col, item)
            vi = QTableWidgetItem(self._vi[i])
            vi.setFlags(fixed | Qt.ItemIsEditable)
            self._table.setItem(i, COL_VI, vi)
            note = QTableWidgetItem("")
            note.setFlags(fixed)
            self._table.setItem(i, COL_NOTE, note)
            self._update_row(i)
        self._filling = False
        self._table.resizeRowsToContents()

    def _update_row(self, i: int):
        """Warning + edited marks of row i (from self._vi)."""
        flags = row_flags({**self._rows[i], "vi_text": self._vi[i]})
        note = self._table.item(i, COL_NOTE)
        note.setText(("⚠ " + ", ".join(flags)) if flags else "")
        note.setForeground(QColor(_AMBER))
        vi_item = self._table.item(i, COL_VI)
        if vi_item.text() != self._vi[i]:
            vi_item.setText(self._vi[i])
        edited = self._vi[i] != self._orig[i]
        vi_item.setBackground(_EDITED if edited else QColor(0, 0, 0, 0))
        vi_item.setToolTip(f"Bản dịch máy: {self._orig[i]}" if edited else "")

    def _on_item_changed(self, item: QTableWidgetItem):
        if self._filling or item.column() != COL_VI:
            return
        i, text = item.row(), " ".join(item.text().split())
        if text == self._vi[i]:
            return
        self._undo.append(list(self._vi))
        self._vi[i] = text
        self._filling = True
        self._update_row(i)
        self._filling = False
        self._table.resizeRowToContents(i)
        self._refresh()

    def _set_all(self, vi: list):
        self._filling = True
        for i, text in enumerate(vi):
            if text != self._vi[i]:
                self._vi[i] = text
                self._update_row(i)
                self._table.resizeRowToContents(i)
        self._filling = False
        self._refresh()

    def _flagged(self) -> list:
        return [i + 1 for i in range(len(self._rows))
                if row_flags({**self._rows[i], "vi_text": self._vi[i]})]

    def _refresh(self):
        n_flag = len(self._flagged())
        self._chk_flagged.setText(f"Chỉ hiện câu cần chú ý ({n_flag})")
        edited = sum(a != b for a, b in zip(self._vi, self._orig))
        self._btn_undo.setEnabled(bool(self._undo))
        self._status.setText(f"Đã sửa {edited}/{len(self._rows)} câu" if edited else
                             "Chưa sửa câu nào")
        if self._chk_flagged.isChecked():
            self._apply_filter()

    def _apply_filter(self, *_):
        q = self._search.text().strip().lower()
        only_flagged = self._chk_flagged.isChecked()
        flagged = set(self._flagged()) if only_flagged else set()
        for i, row in enumerate(self._rows):
            hit = not q or q in row.get("src_text", "").lower() or q in self._vi[i].lower()
            show = hit and (not only_flagged or (i + 1) in flagged)
            self._table.setRowHidden(i, not show)

    def _selected_numbers(self) -> list:
        rows = sorted({ix.row() for ix in self._table.selectionModel().selectedRows()})
        return [r + 1 for r in rows]

    def _current_row(self) -> int:
        sel = self._selected_numbers()
        if sel:
            return sel[0] - 1
        return self._table.currentRow()

    # ── Chatbot ────────────────────────────────────────────────────────────────

    def _fill_copy_menu(self):
        m = self._copy_menu
        m.clear()
        total = len(self._rows)
        m.addAction(f"Tất cả ({total} câu)", lambda: self._copy(range(1, total + 1)))
        if total > CHUNK:
            a = self._next_chunk
            b = min(total, a + CHUNK - 1)
            m.addAction(f"Phần tiếp theo: câu {a}–{b}", self._copy_next_chunk)
        sel = self._selected_numbers()
        act = m.addAction(f"Câu đang chọn ({len(sel)})", lambda: self._copy(sel))
        act.setEnabled(bool(sel))
        flagged = self._flagged()
        act = m.addAction(f"Câu cần chú ý ({len(flagged)})", lambda: self._copy(flagged))
        act.setEnabled(bool(flagged))

    def _copy_next_chunk(self):
        total = len(self._rows)
        a = self._next_chunk
        b = min(total, a + CHUNK - 1)
        self._copy(range(a, b + 1))
        self._next_chunk = 1 if b >= total else b + 1

    def _current_rows(self) -> list:
        return [{**r, "vi_text": v} for r, v in zip(self._rows, self._vi)]

    def _copy(self, numbers):
        self._commit_open_cell()
        numbers = list(numbers)
        text = chatbot_text(self._current_rows(), numbers, _load_instructions())
        QGuiApplication.clipboard().setText(text)
        self._copied = text
        span = (f"câu {numbers[0]}–{numbers[-1]}" if numbers == list(
            range(numbers[0], numbers[-1] + 1)) else f"{len(numbers)} câu")
        self._status.setText(f"✓ Đã copy {span} — dán vào ChatGPT / Claude / Gemini, "
                             "rồi bấm “Dán kết quả AI”")

    def _paste(self):
        self._commit_open_cell()
        clip = QGuiApplication.clipboard().text()
        if clip == self._copied:
            clip = ""   # still our own copy (its instructions have numbered lines too)
        try:
            parse_chatbot_reply(clip, len(self._rows))
        except ValueError:
            clip = ""   # the clipboard holds something else: start empty
        dlg = PasteReplyDialog(self._vi, clip, parent=self)
        if dlg.exec() != QDialog.Accepted or not dlg.changes:
            return
        self._undo.append(list(self._vi))
        vi = list(self._vi)
        for n, text in dlg.changes.items():
            vi[n - 1] = text
        self._set_all(vi)
        self._status.setText(f"✓ Đã áp dụng {len(dlg.changes)} câu từ AI "
                             "(Hoàn tác để bỏ)")

    def _edit_prompt(self):
        edit_instructions(self)

    def _undo_last(self):
        if self._undo:
            self._set_all(self._undo.pop())

    # ── Listening ──────────────────────────────────────────────────────────────

    def _play(self, path):
        self._busy.stop()
        try:
            from PySide6.QtCore import QUrl
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        except ImportError:
            return
        if self._player is None:
            self._player = QMediaPlayer(self)
            self._audio_out = QAudioOutput(self)
            self._player.setAudioOutput(self._audio_out)
        self._player.stop()
        self._player.setSource(QUrl())
        self._player.setSource(QUrl.fromLocalFile(str(path)))
        self._player.play()

    def _listen_failed(self, msg: str):
        self._busy.stop()
        QMessageBox.warning(self, "Nghe thử", f"Không phát được:\n{msg}")

    def _play_source(self):
        i = self._current_row()
        if i < 0 or self._runner.busy():
            return
        row = self._rows[i]
        # named by its time range: still right if a rerun re-segments the video
        audio = self._audio
        out = self._work / f"src_{float(row['start']):.2f}_{float(row['end']):.2f}.wav"

        def cut():
            import subprocess
            from dubber.core.utils.ffmpeg_locator import find_ffmpeg, no_window
            if not out.exists():
                out.parent.mkdir(parents=True, exist_ok=True)
                start = max(0.0, float(row["start"]) - 0.15)
                cmd = [str(find_ffmpeg()), "-y", "-loglevel", "error", "-ss", f"{start:.3f}",
                       "-to", f"{float(row['end']) + 0.15:.3f}", "-i", str(audio), str(out)]
                r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", **no_window())
                if r.returncode != 0:
                    raise RuntimeError(r.stderr[-500:] or "FFmpeg lỗi")
            return out
        self._busy.start(f"Đang cắt âm thanh gốc câu {i + 1}…")
        self._runner.run(cut, on_done=self._play, on_fail=self._listen_failed)

    def _play_tts(self):
        i = self._current_row()
        if i < 0 or self._runner.busy():
            return
        text = self._vi[i].strip()
        if not text:
            return
        work = self._work / "review_tts"

        def speak():
            import asyncio
            if self._tts is None:
                from dubber.core.tts.factory import make_tts_engine
                self._tts = make_tts_engine(self._tts_engine, self._voice, work)
            return asyncio.run(self._tts.synthesize_one(text, i)).wav_path
        self._busy.start(f"Đang tạo giọng câu {i + 1}…" if self._tts else
                         "Đang tải giọng đọc (lần đầu mất vài giây)…")
        self._runner.run(speak, on_done=self._play, on_fail=self._listen_failed)

    # ── Close ──────────────────────────────────────────────────────────────────

    def _stop_audio(self):
        if self._player is not None:
            self._player.stop()

    def _commit_open_cell(self):
        """A cell still being typed in counts (macOS buttons don't take focus on click)."""
        w = QApplication.focusWidget()
        if w is not None and self._table.isAncestorOf(w):
            w.clearFocus()   # focus-out → the delegate writes the text into the cell

    def _continue(self):
        self._commit_open_cell()
        empty = [i + 1 for i, v in enumerate(self._vi) if not v.strip()]
        if empty:
            shown = ", ".join(map(str, empty[:10])) + (" …" if len(empty) > 10 else "")
            ans = QMessageBox.question(
                self, "Còn câu trống",
                f"{len(empty)} câu chưa có tiếng Việt (số {shown}) — các câu này sẽ không "
                "được đọc. Vẫn tiếp tục?")
            if ans != QMessageBox.Yes:
                return
        self._runner.wait_all()
        self._stop_audio()
        self.result_vi = list(self._vi)
        self.accept()

    def reject(self):
        edited = sum(a != b for a, b in zip(self._vi, self._orig))
        if edited:
            ans = QMessageBox.question(
                self, "Hủy video này?",
                f"Bạn đã sửa {edited} câu. Hủy video sẽ bỏ các sửa đổi này. Vẫn hủy?")
            if ans != QMessageBox.Yes:
                return
        self._runner.wait_all()
        self._stop_audio()
        super().reject()
