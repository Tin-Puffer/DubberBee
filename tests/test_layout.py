"""Tests for custom blur area + subtitle position (core helpers, panel config, frame editor)."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from dubber.core.pipeline import (
    _ass_texts, _norm_point_to_px, _norm_rect_to_px, compute_auto_blur, make_sub_style,
)
from dubber.core.video.subtitles import build_ass, cue_size_px


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
    style = make_sub_style(1920, 1080, "Arial", 48, sub_pos=(0.5, 300 / 1080))
    ass = build_ass([_seg("Xin chào các bạn", 0, 2)], 1920, 1080, tmp_path / "s.ass", style=style)
    x, y, w, h = compute_auto_blur(_ass_texts(ass), 1920, 1080, style, pad=12)
    assert abs((x + w / 2) - 960) <= 2
    assert abs((y + h / 2) - 300) <= 2


def test_auto_blur_wraps_widest_line_plus_pad(tmp_path):
    style = make_sub_style(1920, 1080, "Roboto", 48)
    texts = ["Ngắn thôi", "Một dòng phụ đề dài hơn hẳn các dòng khác"]
    x, y, w, h = compute_auto_blur(texts, 1920, 1080, style, pad=12)
    tw, th = cue_size_px(texts[1], style, 1080)
    assert tw + 24 - 2 <= w <= tw + 24 + 2 and th + 24 - 2 <= h <= th + 24 + 2
    assert x % 2 == y % 2 == w % 2 == h % 2 == 0
    # bottom-center: the box ends pad below the line, which sits margin_v above the edge
    assert abs((y + h) - (1080 - style.margin_v + 12)) <= 2
    w0 = compute_auto_blur(texts, 1920, 1080, style, pad=0)[2]
    assert w - w0 in (23, 24, 25)


def test_auto_blur_without_subtitles_is_none():
    assert compute_auto_blur([], 1920, 1080, make_sub_style(1920, 1080, "Arial", 48)) is None


# ── GUI: panel config + frame editor ─────────────────────────────────────────

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


_STYLE_DEFAULTS = {"sub_color": "#FFFFFF", "sub_outline_color": "#000000", "sub_outline": 2,
                   "sub_box": False, "blur_pad": 12, "logo": None, "flip": False}


def test_layout_kwargs_mapping():
    from dubber.app.gui import _layout_kwargs
    base = {"blur_mode": "custom", "blur_rect": (0.1, 0.8, 0.8, 0.1),
            "sub_mode": "custom", "sub_pos": (0.5, 0.3)}
    assert _layout_kwargs({**base, "sub_font": "Roboto", "sub_size": 60,
                           "sub_color": "#FFFF00", "sub_outline": 4}) == {
        "blur_mode": "custom", "blur_rect": (0.1, 0.8, 0.8, 0.1), "sub_pos": (0.5, 0.3),
        "sub_font": "Roboto", "sub_size": 60,
        **_STYLE_DEFAULTS, "sub_color": "#FFFF00", "sub_outline": 4}
    assert _layout_kwargs({**base, "blur_mode": "off"})["blur_mode"] == "off"
    # config saved before M10: separate on/off box
    assert _layout_kwargs({**base, "blur_on": False})["blur_mode"] == "off"
    # custom chosen but no rect drawn → fall back to auto
    assert _layout_kwargs({**base, "blur_rect": None})["blur_mode"] == "auto"
    assert _layout_kwargs({**base, "sub_mode": "default"})["sub_pos"] is None
    # config saved before this feature existed
    assert _layout_kwargs({}) == {"blur_mode": "auto", "blur_rect": None, "sub_pos": None,
                                  "sub_font": "Arial", "sub_size": 48, **_STYLE_DEFAULTS}


def test_options_panel_layout_roundtrip(app):
    from dubber.app.gui import OptionsPanel, _select_data
    a = OptionsPanel()
    a.set_layout((0.1, 0.8, 0.8, 0.1), (0.5, 0.3), "custom")
    _select_data(a._combo_font, "Montserrat")
    a._spin_size.setValue(64)
    a._btn_color.set_color("#ffee00")
    a._btn_outline_color.set_color("#102030")
    a._spin_outline.setValue(5)
    a._chk_box.setChecked(True)
    a._spin_blur_pad.setValue(20)
    cfg = a.get_config()
    assert cfg["blur_mode"] == "custom" and cfg["sub_mode"] == "custom"
    assert cfg["sub_color"] == "#FFEE00" and cfg["sub_outline"] == 5 and cfg["blur_pad"] == 20
    b = OptionsPanel()
    b.set_config(cfg)
    assert b.get_config() == cfg


def test_options_panel_loads_old_config_without_layout_keys(app):
    from dubber.app.gui import OptionsPanel
    a = OptionsPanel()
    new_keys = ("blur_mode", "blur_rect", "blur_pad", "sub_mode", "sub_pos", "sub_font",
                "sub_size", "sub_color", "sub_outline_color", "sub_outline", "sub_box")
    old = {k: v for k, v in a.get_config().items() if k not in new_keys}
    a.set_config(old)
    cfg = a.get_config()
    assert cfg["blur_mode"] == "auto" and cfg["sub_outline"] == 2 and cfg["sub_box"] is False
    a.set_config({**old, "blur_on": False, "blur_mode": "auto"})   # pre-M10 "blur off"
    assert a.get_config()["blur_mode"] == "off"


def test_running_video_settings_are_read_only_but_tabs_switch(app):
    from dubber.app.gui import OptionsPanel
    a = OptionsPanel()
    a.set_read_only(True)
    assert a._tabs.tabBar().isEnabled()
    assert not a._combo_font.isEnabled() and not a._spin_outline.isEnabled()
    a._tabs.setCurrentIndex(0)
    a._tabs.setCurrentIndex(a._tabs.count() - 1)
    assert a._tabs.currentIndex() == a._tabs.count() - 1
    a.set_read_only(False)
    assert a._combo_font.isEnabled()


def _canvas():
    from dubber.app.layout_dialog import FrameCanvas
    c = FrameCanvas(1920, 1080)
    c.resize(960, 540)  # same aspect → frame fills the widget, 0.5 px scale
    c.sub_size, c.sub_default = (0.5, 0.06), (0.5, 0.95)
    c.show()
    return c


def _drag(c, a: QPoint, b: QPoint) -> None:
    QTest.mousePress(c, Qt.LeftButton, pos=a)
    QTest.mouseMove(c, b)
    QTest.mouseRelease(c, Qt.LeftButton, pos=b)


def test_dragging_on_empty_frame_draws_a_blur_area(app):
    c = _canvas()
    _drag(c, QPoint(96, 54), QPoint(864, 135))
    x, y, w, h = c.blur_rect
    assert (round(x, 2), round(y, 2), round(w, 2), round(h, 2)) == (0.1, 0.1, 0.8, 0.15)
    assert c.blur_enabled
    QTest.mouseClick(c, Qt.LeftButton, pos=QPoint(480, 300))   # a click draws nothing new
    assert c.blur_rect == (x, y, w, h)


def test_dragging_the_subtitle_line_moves_it(app):
    c = _canvas()
    _drag(c, QPoint(480, 513), QPoint(480, 135))     # grab the line (center 0.5, 0.95) …
    assert tuple(round(v, 2) for v in c.sub_pos) == (0.5, 0.25)   # … and drop it higher
    assert c.blur_rect is None                        # it didn't draw a blur area


def test_blur_area_moves_and_resizes_by_its_corner(app):
    c = _canvas()
    c.blur_rect, c.blur_enabled = (0.1, 0.1, 0.4, 0.2), True
    _drag(c, QPoint(288, 108), QPoint(384, 162))      # middle of the area → +0.1, +0.1
    assert tuple(round(v, 2) for v in c.blur_rect) == (0.2, 0.2, 0.4, 0.2)
    _drag(c, QPoint(576, 216), QPoint(672, 270))      # bottom-right corner → bigger
    assert tuple(round(v, 2) for v in c.blur_rect) == (0.2, 0.2, 0.5, 0.3)


def test_hit_priority_logo_over_blur_over_subtitle(app):
    c = _canvas()
    c.blur_rect, c.blur_enabled = (0.3, 0.85, 0.4, 0.15), True     # over the subtitle line
    assert c.hit(QPoint(480, 513))[0] == "sub"        # the line wins inside its own box …
    assert c.hit(QPoint(300, 470))[0] == "blur"       # … the blur area elsewhere
    assert c.hit(QPoint(288, 459))[0] == "blur_corner"
    c.logo_size, c.logo_center, c.logo_aspect = 20.0, (0.5, 0.9), 1.0
    assert c.hit(QPoint(480, 486))[0] == "logo"       # the logo sits on top of everything
    c.blur_enabled = False                            # "Tắt" / "Tự động": no area to grab
    c.logo_size = None
    assert c.hit(QPoint(300, 470))[0] == "empty"


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


def test_auto_blur_box_grows_with_font_size():
    texts = ["Xin chào các bạn"]
    *_, w_small, h_small = compute_auto_blur(texts, 1920, 1080, make_sub_style(1920, 1080, "Arial", 32))
    *_, w_big, h_big = compute_auto_blur(texts, 1920, 1080, make_sub_style(1920, 1080, "Arial", 80))
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


def test_preview_sample_is_one_single_line_subtitle(tmp_path):
    from dubber.app.layout_dialog import SAMPLE_TEXT
    out = build_ass([_seg(SAMPLE_TEXT, 0, 60)], 1920, 1080, tmp_path / "s.ass")
    events = [l for l in out.read_text(encoding="utf-8-sig").splitlines() if l.startswith("Dialogue:")]
    assert len(events) == 1 and r"\N" not in events[0]


def test_drawing_an_area_switches_the_editor_to_custom_blur(app, monkeypatch, tmp_path):
    import dubber.app.layout_dialog as ld
    from PySide6.QtGui import QImage
    monkeypatch.setattr(ld, "render_preview", lambda *a, **k: QImage(64, 36, QImage.Format_RGB32))
    dlg = ld.LayoutDialog(tmp_path / "v.mp4", blur_mode="auto", video_info=(1920, 1080, 10.0))
    dlg._runner.wait_all()
    assert dlg.blur_mode == "auto" and not dlg.canvas.blur_enabled
    dlg.canvas.resize(960, 540)
    _drag(dlg.canvas, QPoint(96, 54), QPoint(864, 135))
    assert dlg.blur_mode == "custom" and dlg._combo_blur.currentData() == "custom"
    dlg._reset("blur")
    assert dlg.blur_rect is None and dlg.blur_mode == "auto"
    dlg.reject()


def test_subtitle_tab_has_one_editor_button_and_a_position_reset(app):
    from dubber.app.gui import OptionsPanel
    a = OptionsPanel()
    opened = []
    a.layout_edit_requested.connect(opened.append)
    a._btn_frame.click()
    assert opened == ["frame"]
    assert not a._btn_sub_reset.isEnabled() and "mặc định" in a._lbl_sub_pos.text()
    a.set_layout(None, (0.5, 0.3))
    assert a._btn_sub_reset.isEnabled() and a.get_config()["sub_mode"] == "custom"
    a._btn_sub_reset.click()
    cfg = a.get_config()
    assert cfg["sub_mode"] == "default" and cfg["sub_pos"] is None

def test_vietnamese_voice_can_be_turned_off(app):
    from dubber.app.gui import OptionsPanel
    a = OptionsPanel()
    assert a.get_config()["vi_on"] is True and a._sld_vi.isEnabled()
    a._chk_vi.setChecked(False)
    cfg = a.get_config()
    assert cfg["vi_on"] is False and not a._sld_vi.isEnabled()
    assert "Chỉ phụ đề" in a._hint.text()
    a._chk_music.setChecked(False); a._chk_cn_voice.setChecked(False)
    assert "không có tiếng" in a._hint.text()
    b = OptionsPanel()
    b.set_config(cfg)
    assert b.get_config()["vi_on"] is False
    old = {k: v for k, v in cfg.items() if k != "vi_on"}       # saved before the option
    b.set_config(old)
    assert b.get_config()["vi_on"] is True


def test_flip_option_roundtrip_and_layout(app):
    from dubber.app.gui import OptionsPanel, _layout_kwargs
    a = OptionsPanel()
    assert a.get_config()["flip_h"] is False and _layout_kwargs(a.get_config())["flip"] is False
    seen = []
    a.preview_changed.connect(lambda: seen.append(1))
    a._chk_flip.setChecked(True)
    cfg = a.get_config()
    assert cfg["flip_h"] is True and _layout_kwargs(cfg)["flip"] is True and seen
    b = OptionsPanel()
    b.set_config(cfg)
    assert b.get_config()["flip_h"] is True
    b.set_config({k: v for k, v in cfg.items() if k != "flip_h"})   # older config
    assert b.get_config()["flip_h"] is False


def test_quick_preview_is_mirrored(app, tmp_path):
    from PySide6.QtGui import QColor, QImage
    from dubber.app.preview import paint_preview
    img = QImage(64, 36, QImage.Format_RGB32)
    img.fill(QColor("black"))
    for y in range(36):
        for x in range(16):
            img.setPixelColor(x, y, QColor("red"))          # red strip on the left
    frame = tmp_path / "f.png"
    img.save(str(frame))
    base = {"blur_mode": "off", "sub_pos": None}
    left = paint_preview(frame, 64, 36, {**base, "flip": False}, False, width=64).toImage()
    right = paint_preview(frame, 64, 36, {**base, "flip": True}, False, width=64).toImage()
    assert left.pixelColor(2, 18).red() > 200 and right.pixelColor(61, 18).red() > 200
