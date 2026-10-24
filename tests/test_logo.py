"""Channel logo: placement math, image import, config mapping, real FFmpeg overlay."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from PIL import Image

import dubber.core.video.logo as logo_mod
from dubber.core.video.logo import MARGIN, logo_rect


def _png(path: Path, size=(200, 100), color=(255, 0, 0, 255)) -> Path:
    Image.new("RGBA", size, color).save(path)
    return path


# ── placement ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("position,left,top", [("tr", False, True), ("tl", True, True),
                                               ("br", False, False), ("bl", True, False)])
def test_corner_presets_sit_inside_the_margin(position, left, top):
    x, y, w, h = logo_rect(1280, 720, 200, 100, 12, position)
    assert w == round(1280 * 0.12) & ~1 and h == pytest.approx(w / 2, abs=2)
    assert all(v % 2 == 0 for v in (x, y, w, h))
    m_x, m_y = round(MARGIN * 1280), round(MARGIN * 720)
    assert (abs(x - m_x) <= 2) if left else (abs(1280 - (x + w) - m_x) <= 2)
    assert (abs(y - m_y) <= 2) if top else (abs(720 - (y + h) - m_y) <= 2)


def test_custom_position_is_kept_inside_the_frame():
    x, y, w, h = logo_rect(1280, 720, 100, 100, 20, "custom", (0.99, 0.01))
    assert x + w <= 1280 and y >= 0
    x, y, w, h = logo_rect(1280, 720, 100, 100, 20, "custom", (0.5, 0.5))
    assert abs((x + w / 2) - 640) <= 2 and abs((y + h / 2) - 360) <= 2


def test_import_keeps_transparency_and_dedups(tmp_path, monkeypatch):
    monkeypatch.setattr(logo_mod, "logos_dir", lambda: tmp_path / "logos")
    src = _png(tmp_path / "a.png", color=(0, 0, 255, 128))
    a = logo_mod.import_logo(src)
    b = logo_mod.import_logo(src)
    assert a == b and a.parent == tmp_path / "logos"
    with Image.open(a) as im:
        assert im.mode == "RGBA" and im.getpixel((0, 0))[3] == 128
    src.unlink()                                   # the copy lives on
    assert a.exists()


def test_import_rejects_non_images(tmp_path, monkeypatch):
    monkeypatch.setattr(logo_mod, "logos_dir", lambda: tmp_path / "logos")
    bad = tmp_path / "x.png"
    bad.write_text("not an image")
    with pytest.raises(Exception):
        logo_mod.import_logo(bad)


# ── config → run_m5 ───────────────────────────────────────────────────────────

def test_logo_config_mapping(tmp_path):
    from dubber.app.gui import _logo_of
    img = str(_png(tmp_path / "l.png"))
    on = {"logo_on": True, "logo_path": img, "logo_size": 15, "logo_opacity": 80,
          "logo_position": "bl"}
    assert _logo_of(on) == {"path": img, "size": 15, "opacity": 80, "position": "bl", "pos": None}
    assert _logo_of({**on, "logo_on": False}) is None
    assert _logo_of({**on, "logo_path": str(tmp_path / "gone.png")}) is None
    assert _logo_of({**on, "logo_position": "custom"})["position"] == "tr"   # no point picked
    assert _logo_of({**on, "logo_position": "custom", "logo_pos": [0.2, 0.3]})["pos"] == (0.2, 0.3)
    assert _logo_of({}) is None                                              # old configs


def test_unreadable_logo_is_skipped_not_fatal(tmp_path):
    from dubber.core.pipeline import _logo_arg
    bad = tmp_path / "x.png"
    bad.write_text("nope")
    assert _logo_arg({"path": str(bad)}, 1280, 720) is None
    assert _logo_arg(None, 1280, 720) is None


# ── real render ───────────────────────────────────────────────────────────────

def test_logo_is_burned_in_at_its_place(tmp_path):
    from dubber.core.pipeline import _logo_arg
    from dubber.core.utils.ffmpeg_locator import find_ffmpeg
    from dubber.core.video.render import VideoRenderer
    ff = find_ffmpeg()
    video, dub = tmp_path / "in.mp4", tmp_path / "dub.wav"
    subprocess.run([str(ff), "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:s=640x360:d=1",
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(video)], check=True)
    subprocess.run([str(ff), "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=300:r=22050:d=1",
                    str(dub)], check=True)   # loudnorm can't take pure digital silence
    ass = tmp_path / "s.ass"
    ass.write_text("[Script Info]\nScriptType: v4.00+\nPlayResX: 640\nPlayResY: 360\n\n"
                   "[V4+ Styles]\nFormat: Name, Fontname, Fontsize\nStyle: Default,Arial,20\n\n"
                   "[Events]\nFormat: Layer, Start, End, Style, Text\n", encoding="utf-8")
    lg = _logo_arg({"path": str(_png(tmp_path / "l.png")), "size": 20, "opacity": 100,
                    "position": "tl"}, 640, 360)
    out = VideoRenderer(ff).render_final(video, dub, ass, tmp_path / "out.mp4", orig_volume=0.0,
                                         logo=lg)
    frame = tmp_path / "f.png"
    subprocess.run([str(ff), "-v", "error", "-y", "-ss", "0.5", "-i", str(out), "-frames:v", "1",
                    str(frame)], check=True)
    _, x, y, w, h, _ = lg
    with Image.open(frame) as im:
        r, g, b = im.convert("RGB").getpixel((x + w // 2, y + h // 2))
        assert r > 200 and g < 60 and b < 60                 # red logo
        assert max(im.convert("RGB").getpixel((600, 320))) < 40   # rest still black
    from dubber.core.utils.ffmpeg_locator import find_ffprobe
    probe = subprocess.run([str(find_ffprobe()), "-v", "error", "-select_streams",
                            "a:0", "-show_entries", "stream=sample_rate", "-of", "csv=p=0",
                            str(out)], capture_output=True, text=True)
    assert probe.stdout.strip() == "48000"          # loudnorm's 192 kHz resampled for AAC


# ── frame editor ──────────────────────────────────────────────────────────────

def _logo_dialog(tmp_path, monkeypatch, logo):
    pytest.importorskip("PySide6")
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    import dubber.app.layout_dialog as ld
    seen = []
    monkeypatch.setattr(ld, "render_preview",
                        lambda *a, **k: (seen.append(k.get("logo")), QImage(64, 36, QImage.Format_RGB32))[1])
    dlg = ld.LayoutDialog(tmp_path / "v.mp4", video_info=(1280, 720, 10.0), logo=logo)
    dlg._runner.wait_all()
    dlg.canvas.resize(640, 360)            # frame fills the canvas: 0.5 px per video px
    return dlg, seen


def _px(dlg, nx, ny):
    from PySide6.QtCore import QPointF
    r = dlg.canvas._target()
    return QPointF(r.x() + nx * r.width(), r.y() + ny * r.height())


def test_logo_box_starts_where_the_export_puts_it(tmp_path, monkeypatch):
    logo = {"path": str(_png(tmp_path / "l.png", size=(200, 100))), "size": 12, "opacity": 90,
            "position": "tr", "pos": None}
    dlg, seen = _logo_dialog(tmp_path, monkeypatch, logo)
    assert seen[0]["position"] == "tr"
    x, y, w, h = dlg.canvas.logo_box()
    ex, ey, ew, eh = logo_rect(1280, 720, 200, 100, 12, "tr")
    assert (x * 1280, y * 720, w * 1280, h * 720) == pytest.approx((ex, ey, ew, eh), abs=3)
    assert dlg.logo["position"] == "tr"            # untouched: still the preset
    dlg.reject()


def test_dragging_the_logo_moves_it(tmp_path, monkeypatch):
    logo = {"path": str(_png(tmp_path / "l.png")), "size": 12, "opacity": 90,
            "position": "tr", "pos": None}
    dlg, _ = _logo_dialog(tmp_path, monkeypatch, logo)
    c = dlg.canvas
    x, y, w, h = c.logo_box()
    c.press(_px(dlg, x + w / 2, y + h / 2))   # grab the middle …
    c.drag(_px(dlg, 0.3, 0.6))                # … and drop it
    c.release()
    out = dlg.logo
    assert out["position"] == "custom" and out["pos"] == pytest.approx((0.3, 0.6), abs=0.01)
    assert out["size"] == 12                       # moving doesn't resize
    dlg.reject()


def test_dragging_a_corner_resizes_with_the_aspect_kept(tmp_path, monkeypatch):
    logo = {"path": str(_png(tmp_path / "l.png", size=(200, 100))), "size": 12, "opacity": 90,
            "position": "custom", "pos": (0.5, 0.5)}
    dlg, _ = _logo_dialog(tmp_path, monkeypatch, logo)
    c = dlg.canvas
    x, y, w, h = c.logo_box()
    c.press(_px(dlg, x + w, y + h))           # bottom-right handle
    c.drag(_px(dlg, x + 0.24, y + 0.2))       # pull it out, mostly to the right
    c.release()
    nx, ny, nw, nh = c.logo_box()
    assert (nx, ny) == pytest.approx((x, y), abs=0.002)          # top-left stayed put
    assert nw == pytest.approx(0.24, abs=0.005)                   # 24 % of the width
    assert nh * 720 / (nw * 1280) == pytest.approx(0.5, abs=0.01)  # 2:1 image stays 2:1
    dlg._on_canvas_changed()
    assert dlg._spin_logo.value() == 24 and dlg.logo["size"] == 24
    dlg.reject()


def test_resize_is_clamped_to_the_allowed_sizes(tmp_path, monkeypatch):
    from dubber.core.video.logo import MAX_SIZE, MIN_SIZE
    logo = {"path": str(_png(tmp_path / "l.png")), "size": 12, "opacity": 90,
            "position": "custom", "pos": (0.5, 0.5)}
    dlg, _ = _logo_dialog(tmp_path, monkeypatch, logo)
    c = dlg.canvas
    x, y, w, h = c.logo_box()
    c.press(_px(dlg, x + w, y + h))
    c.drag(_px(dlg, x + 0.001, y + 0.001))    # crushed to nothing
    assert c.logo_size == pytest.approx(MIN_SIZE)
    c.drag(_px(dlg, 1.0, 1.0))                # dragged across the frame
    assert c.logo_size <= MAX_SIZE
    dlg.reject()


def test_spin_box_resizes_the_box_too(tmp_path, monkeypatch):
    logo = {"path": str(_png(tmp_path / "l.png")), "size": 12, "opacity": 90,
            "position": "tr", "pos": None}
    dlg, _ = _logo_dialog(tmp_path, monkeypatch, logo)
    dlg._spin_logo.setValue(20)
    assert dlg.canvas.logo_box()[2] == pytest.approx(0.20, abs=0.002)
    assert dlg.logo["size"] == 20 and dlg.logo["position"] == "tr"
    dlg.reject()


def test_editor_without_logo_has_no_logo_mode(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    import dubber.app.layout_dialog as ld
    monkeypatch.setattr(ld, "render_preview", lambda *a, **k: QImage(64, 36, QImage.Format_RGB32))
    dlg = ld.LayoutDialog(tmp_path / "v.mp4", video_info=(1280, 720, 10.0))
    dlg._runner.wait_all()
    assert dlg.logo is None and dlg.canvas.logo_box() is None
    dlg.reject()


# ── remembered for later videos / app restarts ────────────────────────────────

def test_last_logo_is_remembered_across_restarts(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from dubber.app.gui import OptionsPanel, _select_data
    img = str(_png(tmp_path / "l.png"))
    a = OptionsPanel()
    assert not a._chk_logo.isChecked()                       # nothing remembered yet
    a._logo_path = img
    a._chk_logo.setChecked(True)                             # user turns it on …
    a._spin_logo_size.setValue(18)
    _select_data(a._combo_logo_pos, "bl")
    b = OptionsPanel()                                       # … app restarted
    cfg = b.get_config()
    assert (cfg["logo_on"], cfg["logo_path"], cfg["logo_size"], cfg["logo_position"]) == \
        (True, img, 18, "bl")


def test_switching_videos_does_not_overwrite_the_remembered_logo(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from dubber.app.gui import OptionsPanel
    img = str(_png(tmp_path / "l.png"))
    a = OptionsPanel()
    a._logo_path = img
    a._chk_logo.setChecked(True)
    old = a.get_config()
    a.set_config({**old, "logo_on": False, "logo_path": None})   # select a video without logo
    assert OptionsPanel().get_config()["logo_path"] == img


def test_missing_remembered_image_is_ignored(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from dubber.app.gui import OptionsPanel
    from dubber.core.utils.settings import load_settings, save_settings
    st = load_settings()
    st["last_logo"] = {"logo_on": True, "logo_path": str(tmp_path / "gone.png"), "logo_size": 12,
                       "logo_opacity": 90, "logo_position": "tr", "logo_pos": None}
    save_settings(st)
    cfg = OptionsPanel().get_config()
    assert cfg["logo_path"] is None and not cfg["logo_on"]
