"""🎧 Nghe thử: plays a 20 s window of the selected video mixed at the Audio tab's volumes;
moving a slider is heard at once (one mixed stream, so the tracks never drift apart)."""
from __future__ import annotations

import logging
from typing import Callable, Optional

import numpy as np
from PySide6.QtCore import QIODevice, Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget

from dubber.core.audio import preview_mix as pm

log = logging.getLogger(__name__)

_GREEN, _AMBER, _MID, _DIM, _ACCENT = "#4caf50", "#e0a030", "#9a9a9a", "#6a6a6a", "#3d9df3"
# Meter rows: (track gains summed into it, label, colour)
METERS = ((("dub",), "Giọng Việt", _GREEN), (("music", "orig"), "Nhạc nền", _ACCENT),
          (("cn",), "Tiếng Trung", _MID), (("bgm",), "Nhạc của bạn", "#b07cf0"))
LEVEL_FLOOR_DB = -50.0


def _clock(t: float) -> str:
    return f"{int(t // 60):02d}:{int(t % 60):02d}"


def level(chunk: "np.ndarray | None") -> float:
    """0–1 meter value (dB scale above LEVEL_FLOOR_DB) of a chunk's RMS."""
    if chunk is None or not chunk.size:
        return 0.0
    rms = float(np.sqrt(np.mean(chunk ** 2)))
    if rms <= 0:
        return 0.0
    return float(min(1.0, max(0.0, 1 - 20 * np.log10(rms) / LEVEL_FLOOR_DB)))


class MixDevice(QIODevice):
    """Endless 16-bit stereo stream: the window looped, mixed at the current volumes."""

    def __init__(self, tracks: pm.PreviewTracks, volumes: dict, parent=None):
        super().__init__(parent)
        self.tracks = tracks
        self.pos = 0
        self.levels = {label: 0.0 for _, label, _ in METERS}
        self.covered = False              # background louder than the voice just now
        self._gain = 1.0                  # smoothed towards the loudness target
        self.set_volumes(volumes)

    def set_volumes(self, volumes: dict) -> None:
        self.gains = pm.track_gains(self.tracks, volumes)
        self._target = pm.loudness_gain(self.tracks, self.gains)

    def isSequential(self) -> bool:
        return True

    def bytesAvailable(self) -> int:
        return (1 << 20) + super().bytesAvailable()

    def readData(self, maxlen: int) -> bytes:
        n = max(0, min(int(maxlen) // 4, pm.SR // 10))   # ≤ 0.1 s per read
        if not n:
            return b""
        return self.render(n)

    def writeData(self, data) -> int:
        return -1

    def render(self, n: int) -> bytes:
        """The next *n* frames as int16 bytes (also updates the meters)."""
        tr, out, parts = self.tracks, [], []
        left = n
        while left:
            j = min(self.pos + left, tr.frames)
            parts.append((self.pos, j))
            left -= j - self.pos
            self.pos = j % tr.frames
        start_gain = self._gain
        self._gain += (self._target - self._gain) * 0.5    # no jump when a slider moves
        for i, j in parts:
            out.append(pm.mix(tr, self.gains, i, j))
        chunk = np.vstack(out)
        ramp = np.linspace(start_gain, self._gain, len(chunk), dtype=np.float32)[:, None]
        chunk *= ramp
        peak = float(np.max(np.abs(chunk))) if chunk.size else 0.0
        if peak > 0.89:                     # crude limiter, like the export's -1.5 dB TP
            chunk *= 0.89 / peak
        self._meter(parts)
        return (np.clip(chunk, -1, 1) * 32767).astype("<i2").tobytes()

    def _meter(self, parts) -> None:
        tr, g = self.tracks, self.gains
        for names, label, _ in METERS:
            bits = []
            for i, j in parts:
                seg = np.zeros((j - i, 2), np.float32)
                for name in names:
                    a = tr.get(name)
                    if g.get(name) and a is not None:
                        seg += a[i:j] * np.float32(g[name] * self._gain)
                bits.append(seg)
            new = level(np.vstack(bits))
            old = self.levels[label]
            self.levels[label] = new if new > old else old * 0.8 + new * 0.2   # fall slowly
        voice = self.levels["Giọng Việt"]
        back = max(self.levels["Nhạc nền"], self.levels["Tiếng Trung"],
                   self.levels["Nhạc của bạn"])
        self.covered = voice > 0.3 and back > voice - 0.08    # within ~4 dB of the voice


class _Meter(QWidget):
    def __init__(self, label: str, color: str, parent=None):
        super().__init__(parent)
        self.value, self.color = 0.0, color
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        name = QLabel(label, self)
        name.setFixedWidth(84)
        name.setStyleSheet(f"color: {_MID}; font-size: 11px;")
        self.bar = QLabel(self)
        self.bar.setFixedHeight(6)
        lay.addWidget(name)
        lay.addWidget(self.bar, 1)
        self.set_value(0.0)

    def set_value(self, v: float) -> None:
        self.value = v
        stop = max(0.001, min(0.999, v))
        self.bar.setStyleSheet(
            "border-radius: 3px; background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
            f"stop:0 {self.color}, stop:{stop:.3f} {self.color}, "
            f"stop:{stop + 0.001:.3f} #2a2a2a, stop:1 #2a2a2a);")


class MixPreview(QWidget):
    """Play / stop, the window's position, a meter per track and a warning when the
    background covers the Vietnamese voice."""

    state_changed = Signal(str)   # status text (tests)

    def __init__(self, volumes: Callable[[], dict], context: Callable[[], Optional[dict]],
                 parent=None):
        """volumes(): the Audio tab's {vi_on, vi, music_on, …}; context(): the selected
        video {path, duration, trim, engine, voice, overlap, bgm_path} or None."""
        super().__init__(parent)
        self._volumes, self._context = volumes, context
        self._tracks: Optional[pm.PreviewTracks] = None
        self._key = None
        self._device: Optional[MixDevice] = None
        self._sink = None
        self._preparing = False
        from dubber.app.tasks import TaskRunner
        self._runner = TaskRunner(self)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.btn = QPushButton("▶ Nghe thử", self)
        self.btn.setToolTip("Phát 20 giây của video, trộn theo các thanh âm lượng ở trên — "
                            "vừa nghe vừa kéo thanh để chỉnh")
        self.btn.clicked.connect(self.toggle)
        self.pos = QSlider(Qt.Horizontal, self)
        self.pos.setToolTip("Chọn đoạn 20 giây để nghe")
        self.pos.sliderReleased.connect(self._on_seek)
        self.pos.valueChanged.connect(self._show_range)
        self.range = QLabel("", self)
        self.range.setStyleSheet(f"color: {_MID}; font-size: 11px;")
        self.range.setFixedWidth(88)
        top.addWidget(self.btn)
        top.addWidget(self.pos, 1)
        top.addWidget(self.range)
        lay.addLayout(top)
        self.status = QLabel("", self)
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color: {_DIM}; font-size: 11px;")
        lay.addWidget(self.status)
        self.meters = {label: _Meter(label, color, self) for _, label, color in METERS}
        for m in self.meters.values():
            lay.addWidget(m)
            m.hide()
        self.warn = QLabel("⚠ Nền đang lấn giọng Việt — giảm nhạc / tiếng Trung hoặc "
                           "tăng giọng Việt", self)
        self.warn.setWordWrap(True)
        self.warn.setStyleSheet(f"color: {_AMBER}; font-size: 11px;")
        self.warn.hide()
        lay.addWidget(self.warn)
        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._tick)
        self.refresh_source()

    # ── source / volumes ──────────────────────────────────────────────────────

    def refresh_source(self) -> None:
        """The selected video changed: stop and fit the position slider to it."""
        self.stop()
        ctx = self._context()
        ok = bool(ctx and ctx.get("path") and ctx.get("duration"))
        self.btn.setEnabled(ok)
        self.pos.setEnabled(ok)
        if not ok:
            self.range.setText("")
            self._set_status("Chọn một video trong hàng đợi để nghe thử")
            return
        self._fit_range(ctx)
        self._set_status("")

    def _fit_range(self, ctx: dict) -> None:
        lo, hi = pm.window_bounds(ctx["duration"], ctx.get("trim") or (0.0, None))
        self.pos.blockSignals(True)
        self.pos.setRange(int(lo), int(hi))
        if not lo <= self.pos.value() <= hi:
            self.pos.setValue(int(lo))
        self.pos.blockSignals(False)
        self._show_range()

    def refresh_volumes(self, *_) -> None:
        """A slider / checkbox moved: heard at once while playing."""
        if self._device is not None:
            self._device.set_volumes(self._volumes())
            self._update_meter_rows()

    # ── play / stop ───────────────────────────────────────────────────────────

    def wait(self, ms: int) -> None:
        self._runner.wait_all(ms)

    def playing(self) -> bool:
        return self._sink is not None

    def toggle(self) -> None:
        if self.playing() or self._preparing:
            self.stop()
        else:
            self.play()

    def play(self) -> None:
        ctx = self._context()
        if not ctx:
            return
        self._fit_range(ctx)          # the trim may have changed since
        start = float(self.pos.value())
        key = (str(ctx["path"]), start, tuple(ctx.get("trim") or (0.0, None)),
               ctx.get("engine"), ctx.get("voice"), ctx.get("overlap"), ctx.get("bgm_path"))
        if key == self._key and self._tracks is not None:
            self._start(self._tracks)
            return
        self._preparing = True
        self.btn.setText("■ Dừng")
        self._set_status("Đang chuẩn bị đoạn nghe thử…")
        status = _StatusRelay(self)

        def work():
            return pm.prepare(ctx["path"], start, trim=ctx.get("trim") or (0.0, None),
                              engine=ctx.get("engine") or "piper", voice=ctx.get("voice"),
                              overlap=ctx.get("overlap"), bgm_path=ctx.get("bgm_path"),
                              on_status=status.text.emit)

        def done(tracks):
            if not self._preparing:   # stopped meanwhile
                return
            self._preparing = False
            self._tracks, self._key = tracks, key
            self._start(tracks)

        def failed(msg):
            self._preparing = False
            self.btn.setText("▶ Nghe thử")
            self._set_status(f"Không nghe thử được: {msg}", _AMBER)

        self._runner.run(work, done, failed)

    def _start(self, tracks: pm.PreviewTracks) -> None:
        from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices
        fmt = QAudioFormat()
        fmt.setSampleRate(pm.SR)
        fmt.setChannelCount(2)
        fmt.setSampleFormat(QAudioFormat.Int16)
        self._device = MixDevice(tracks, self._volumes(), self)
        self._device.open(QIODevice.ReadOnly)
        self._sink = QAudioSink(QMediaDevices.defaultAudioOutput(), fmt, self)
        self._sink.setBufferSize(pm.SR * 4 // 10)          # ~0.1 s: sliders react fast
        self._sink.start(self._device)
        self.btn.setText("■ Dừng")
        self._update_meter_rows()
        self._set_status(self._source_text(tracks))
        self._timer.start()

    def stop(self) -> None:
        self._preparing = False
        self._timer.stop()
        if self._sink is not None:
            self._sink.stop()
            self._sink.deleteLater()
            self._sink = None
        if self._device is not None:
            self._device.close()
            self._device.deleteLater()
            self._device = None
        self.btn.setText("▶ Nghe thử")
        for m in self.meters.values():
            m.set_value(0.0)
        self.warn.hide()

    # ── display ───────────────────────────────────────────────────────────────

    def _on_seek(self) -> None:
        if self.playing() or self._preparing:
            self.stop()
            self.play()

    def _show_range(self, *_) -> None:
        s = float(self.pos.value())
        self.range.setText(f"{_clock(s)} – {_clock(s + pm.WINDOW_S)}")

    def _set_status(self, text: str, color: str = _DIM) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {color}; font-size: 11px;")
        self.status.setVisible(bool(text))
        self.state_changed.emit(text)

    @staticmethod
    def _source_text(tr: pm.PreviewTracks) -> str:
        voice = {"dub": "giọng Việt đã lồng lần trước",
                 "lines": "giọng đã chọn đọc bản dịch của đoạn này",
                 "sample": "giọng đã chọn đọc câu mẫu (video chưa dịch)"}.get(
            tr.dub_source, "không có giọng Việt")
        stems = {"cache": "", "mdxnet": "", "ffmpeg": " · tách nhạc kiểu đơn giản (chưa cài "
                 "MDX-Net)", "": " · không tách được nhạc — nghe âm gốc"}[tr.stems_source]
        return (f"Đang phát (lặp lại) · {voice}{stems}. Độ to tổng chỉ gần đúng; "
                "tỉ lệ giữa các thanh đúng như video xuất ra.")

    def _update_meter_rows(self) -> None:
        g = self._device.gains if self._device else {}
        for names, label, _ in METERS:
            self.meters[label].setVisible(any(g.get(n) for n in names))

    def _tick(self) -> None:
        if self._device is None:
            return
        for label, m in self.meters.items():
            m.set_value(self._device.levels[label])
        self.warn.setVisible(self._device.covered)


class _StatusRelay(QWidget):
    """Carries progress text from the prepare thread to the GUI thread."""
    text = Signal(str)

    def __init__(self, owner: MixPreview):
        super().__init__(owner)
        self.hide()
        self._owner = owner
        self.text.connect(self._show)   # bound method of a GUI-thread object → queued

    def _show(self, text: str) -> None:
        if self._owner._preparing:
            self._owner._set_status(text)
