"""Tests for custom blur area + subtitle position (core helpers, panel config, frame editor)."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from dubber.core.pipeline import _compute_sub_blurs, _norm_point_to_px, _norm_rect_to_px
from dubber.core.video.subtitles import build_ass


# ── core helpers ──────────────────────────────────────────────────────────────

def test_norm_rect_to_px_even_and_clamped():
    assert _norm_rect_to_px((0.1, 0.8, 0.8, 0.15), 1920, 1080) == (192, 864, 1536, 162)
    x, y, w, h = _norm_rect_to_px((0.9, 0.9, 0.5, 0.5), 1151, 719)  # overflows frame
    assert x % 2 == y % 2 == w % 2 == h % 2 == 0
    assert x + w <= 1151 and y + h <= 719


def test_norm_rect_too_small_is_ignored():
    assert _norm_rect_to_px((0.5, 0.5, 0.001, 0.001), 1920, 1080) is None
    assert _norm_rect_to_px(None, 1920, 1080) is None


def test_norm_point_to_px():
    assert _norm_point_to_px((0.5, 0.25), 1920, 1080) == (960, 270)
    assert _norm_point_to_px((1.0, 1.0), 1920, 1080) == (1919, 1079)
    assert _norm_point_to_px(None, 1920, 1080) is None


def _seg(text, start, end):
    return SimpleNamespace(vi_text=text, final_start=start, final_end=end)


def test_ass_custom_position_tag(tmp_path):
    from dubber.core.video.subtitles import SubtitleStyle
    out = build_ass([_seg("Xin chào", 0, 2)], 1920, 1080, tmp_path / "s.ass",
                    style=SubtitleStyle(pos=(960, 300)))
    assert r"{\an5\pos(960,300)}Xin chào" in out.read_text(encoding="utf-8-sig")


def test_ass_default_has_no_position_tag(tmp_path):
    out = build_ass([_seg("Xin chào", 0, 2)], 1920, 1080, tmp_path / "s.ass")
    assert r"\pos" not in out.read_text(encoding="utf-8-sig")


def test_auto_blur_follows_custom_subtitle_center(tmp_path):
    from dubber.core.video.subtitles import SubtitleStyle
    ass = build_ass([_seg("Xin chào các bạn", 0, 2)], 1920, 1080, tmp_path / "s.ass",
                    style=SubtitleStyle(pos=(960, 300)))
    (_, _, x, y, w, h), = _compute_sub_blurs(ass, 1920, 1080, 21, 48, center=(960, 300))
    assert abs((x + w / 2) - 960) <= 1
    assert abs((y + h / 2) - 300) <= 1


# ── GUI: panel config + frame editor ─────────────────────────────────────────

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_layout_kwargs_mapping():
    from dubber.app.gui import _layout_kwargs
    base = {"blur_on": True, "blur_mode": "custom", "blur_rect": (0.1, 0.8, 0.8, 0.1),
            "sub_mode": "custom", "sub_pos": (0.5, 0.3)}
    assert _layout_kwargs({**base, "sub_font": "Roboto", "sub_size": 60}) == {
        "blur_mode": "custom", "blur_rect": (0.1, 0.8, 0.8, 0.1), "sub_pos": (0.5, 0.3),
        "sub_font": "Roboto", "sub_size": 60}
    assert _layout_kwargs({**base, "blur_on": False})["blur_mode"] == "off"
    # custom chosen but no rect drawn → fall back to auto
    assert _layout_kwargs({**base, "blur_rect": None})["blur_mode"] == "auto"
    assert _layout_kwargs({**base, "sub_mode": "default"})["sub_pos"] is None
    # config saved before this feature existed
    assert _layout_kwargs({}) == {"blur_mode": "auto", "blur_rect": None, "sub_pos": None,
                                  "sub_font": "Arial", "sub_size": 48}


def test_options_panel_layout_roundtrip(app):
    from dubber.app.gui import OptionsPanel
    a = OptionsPanel()
    a.set_layout((0.1, 0.8, 0.8, 0.1), (0.5, 0.3))
    a._chk_blur.setChecked(False)
    from dubber.app.gui import _select_data
    _select_data(a._combo_font, "Montserrat")
    a._spin_size.setValue(64)
    cfg = a.get_config()
    assert cfg["blur_mode"] == "custom" and cfg["sub_mode"] == "custom"
    b = OptionsPanel()
    b.set_config(cfg)
    assert b.get_config() == cfg


def test_options_panel_loads_old_config_without_layout_keys(app):
    from dubber.app.gui import OptionsPanel
    a = OptionsPanel()
    old = {k: v for k, v in a.get_config().items()
           if k not in ("blur_on", "blur_mode", "blur_rect", "sub_mode", "sub_pos", "sub_font", "sub_size")}
    a.set_config(old)
    assert a.get_config()["blur_on"] is True and a.get_config()["blur_mode"] == "auto"


def test_canvas_drag_sets_blur_rect_and_click_sets_sub(app):
    from dubber.app.layout_dialog import FrameCanvas
    c = FrameCanvas(1920, 1080)
    c.resize(960, 540)  # same aspect → frame fills the widget, 0.5 px scale
    c.show()
    QTest.mousePress(c, Qt.LeftButton, pos=QPoint(96, 432))
    QTest.mouseMove(c, QPoint(864, 513))
    QTest.mouseRelease(c, Qt.LeftButton, pos=QPoint(864, 513))
    x, y, w, h = c.blur_rect
    assert (round(x, 2), round(y, 2), round(w, 2), round(h, 2)) == (0.1, 0.8, 0.8, 0.15)

    c.mode = "sub"
    QTest.mouseClick(c, Qt.LeftButton, pos=QPoint(480, 135))
    assert tuple(round(v, 2) for v in c.sub_pos) == (0.5, 0.25)


# ── fonts ─────────────────────────────────────────────────────────────────────

def test_bundled_fonts_cover_every_non_system_choice():
    from dubber.core.video.fonts import SUBTITLE_FONTS, fonts_dir
    files = {f.name for f in fonts_dir().glob("*.ttf")}
    assert len(files) == len(SUBTITLE_FONTS) - 1  # all but system Arial are bundled
    assert not [f for f in fonts_dir().iterdir() if f.suffix != ".ttf"]  # libass tries to load every file


def test_ass_uses_chosen_font_and_scaled_size(tmp_path):
    from dubber.core.video.subtitles import SubtitleStyle
    out = build_ass([_seg("Xin chào", 0, 2)], 1280, 720, tmp_path / "s.ass",
                    style=SubtitleStyle(font_name="Be Vietnam Pro", font_size=60))
    assert "Style: Default,Be Vietnam Pro,40," in out.read_text(encoding="utf-8-sig")  # 60 × 720/1080


def test_render_passes_bundled_fonts_dir(tmp_path, monkeypatch):
    import dubber.core.video.render as render
    from dubber.core.video.fonts import fonts_dir
    cmd = {}
    monkeypatch.setattr(render, "_detect_hw_encoder", lambda _f: "libx264")
    monkeypatch.setattr(render, "_run_ffmpeg_with_progress",
                        lambda c, cb: (cmd.update(c=c), Path(c[-1]).write_bytes(b"x")))
    render.VideoRenderer(Path("ffmpeg")).render_final(
        tmp_path / "in.mp4", tmp_path / "d.wav", tmp_path / "s.ass", tmp_path / "o.mp4",
        fonts_dir=fonts_dir())
    fc = cmd["c"][cmd["c"].index("-filter_complex") + 1]
    assert "fontsdir=" in fc and "assets/fonts" in fc


def test_auto_blur_box_grows_with_font_size(tmp_path):
    ass = build_ass([_seg("Xin chào các bạn", 0, 2)], 1920, 1080, tmp_path / "s.ass")
    (*_, w_small, h_small), = _compute_sub_blurs(ass, 1920, 1080, 21, 32)
    (*_, w_big, h_big), = _compute_sub_blurs(ass, 1920, 1080, 21, 80)
    assert w_big > w_small and h_big > h_small


def test_render_preview_draws_sample_text_at_chosen_center(app, tmp_path):
    import subprocess
    from dubber.app.layout_dialog import render_preview
    from dubber.core.utils.ffmpeg_locator import find_ffmpeg
    clip = tmp_path / "c.mp4"
    subprocess.run([str(find_ffmpeg()), "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=black:s=640x360:d=2", "-pix_fmt", "yuv420p", str(clip)], check=True)
    img = render_preview(clip, 1.0, 640, 360, font="Roboto", size=48,
                         sub_pos=(0.5, 0.3), blur_mode="off", blur_rect=None)
    assert (img.width(), img.height()) == (640, 360)
    bright = [(x, y) for y in range(0, 360, 2) for x in range(0, 640, 2)
              if img.pixelColor(x, y).value() > 200]
    ys = [y for _, y in bright]
    assert bright and abs((min(ys) + max(ys)) / 2 - 108) < 20  # text centered at 30% height


def test_preview_sample_is_one_two_line_subtitle(tmp_path):
    from dubber.app.layout_dialog import SAMPLE_TEXT
    out = build_ass([_seg(SAMPLE_TEXT, 0, 60)], 1920, 1080, tmp_path / "s.ass")
    events = [l for l in out.read_text(encoding="utf-8-sig").splitlines() if l.startswith("Dialogue:")]
    assert len(events) == 1 and r"\N" in events[0]
