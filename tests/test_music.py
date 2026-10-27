"""The user's own background music: import, config mapping, render filter, real mix."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import dubber.core.audio.music as music_mod


def _ff():
    from dubber.core.utils.ffmpeg_locator import find_ffmpeg
    return find_ffmpeg()


def _tone(path: Path, seconds: float, freq: int = 440) -> Path:
    subprocess.run([str(_ff()), "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"sine=f={freq}:d={seconds}", str(path)], check=True)
    return path


def test_import_copies_and_dedups(tmp_path, monkeypatch):
    monkeypatch.setattr(music_mod, "music_dir", lambda: tmp_path / "music")
    src = _tone(tmp_path / "song.mp3", 1.5)
    a = music_mod.import_music(src)
    assert a == music_mod.import_music(src) and a.parent == tmp_path / "music"
    assert a.suffix == ".mp3"
    assert music_mod.probe_duration(a) == pytest.approx(1.5, abs=0.1)
    src.unlink()
    assert a.exists()


def test_import_rejects_files_without_audio(tmp_path, monkeypatch):
    monkeypatch.setattr(music_mod, "music_dir", lambda: tmp_path / "music")
    bad = tmp_path / "x.mp3"
    bad.write_text("not audio")
    with pytest.raises(ValueError):
        music_mod.import_music(bad)


def test_music_config_mapping(tmp_path):
    from dubber.app.gui import _bgm_of
    song = str(_tone(tmp_path / "s.mp3", 1))
    on = {"bgm_on": True, "bgm_path": song, "bgm_db": -12}
    assert _bgm_of(on) == {"path": song, "db": -12}
    assert _bgm_of({**on, "bgm_on": False}) is None
    assert _bgm_of({**on, "bgm_path": str(tmp_path / "gone.mp3")}) is None
    assert _bgm_of({}) is None


def test_render_filter_loops_trims_and_fades_the_music(tmp_path, monkeypatch):
    import dubber.core.video.render as render
    captured = {}

    def fake_run(cmd, on_progress):
        captured["cmd"] = cmd
        Path(cmd[-1]).write_bytes(b"x")
        return ""
    monkeypatch.setattr(render, "_detect_hw_encoder", lambda _ff: "libx264")
    monkeypatch.setattr(render, "_run_ffmpeg_with_progress", fake_run)
    render.VideoRenderer(Path("ffmpeg")).render_final(
        tmp_path / "in.mp4", tmp_path / "dub.wav", tmp_path / "s.ass", tmp_path / "out.mp4",
        background_wav=tmp_path / "bg.wav", orig_volume=0.4,
        music=(tmp_path / "song.mp3", 0.3, 60.0), logo=(tmp_path / "l.png", 0, 0, 10, 10, 1.0))
    cmd = captured["cmd"]
    i = cmd.index(str(tmp_path / "song.mp3"))
    assert cmd[i - 3:i] == ["-stream_loop", "-1", "-i"]
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "[3:a]volume=0.3,atrim=0:60.000,asetpts=PTS-STARTPTS,afade=t=out:st=58.000:d=2.0" in fc
    assert "[bg_a][dub_a][music_a]amix=inputs=3" in fc
    assert "[4:v]scale=10:10" in fc                    # logo comes after the music input


def test_music_alone_is_mixed_into_a_real_export(tmp_path):
    """Subtitles only, original audio off, just the user's music: the video has sound
    for its whole length even though the song is shorter (looped)."""
    from dubber.core.utils.ffmpeg_locator import find_ffprobe
    from dubber.core.video.render import VideoRenderer
    video = tmp_path / "in.mp4"
    subprocess.run([str(_ff()), "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=black:s=320x180:d=4", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    str(video)], check=True)
    song = _tone(tmp_path / "song.mp3", 1.0)
    ass = tmp_path / "s.ass"
    ass.write_text("[Script Info]\nScriptType: v4.00+\nPlayResX: 320\nPlayResY: 180\n\n"
                   "[V4+ Styles]\nFormat: Name, Fontname, Fontsize\nStyle: Default,Arial,20\n\n"
                   "[Events]\nFormat: Layer, Start, End, Style, Text\n", encoding="utf-8")
    out = VideoRenderer(_ff()).render_final(video, None, ass, tmp_path / "out.mp4",
                                            orig_volume=0.0, music=(song, 0.5, 4.0))
    dur = subprocess.run([str(find_ffprobe()), "-v", "error", "-select_streams", "a:0",
                          "-show_entries", "stream=duration", "-of", "csv=p=0", str(out)],
                         capture_output=True, text=True).stdout.strip()
    assert float(dur) == pytest.approx(4.0, abs=0.2)


def test_music_option_in_the_audio_tab(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from dubber.app.gui import OptionsPanel
    song = str(_tone(tmp_path / "s.mp3", 75))
    a = OptionsPanel()
    assert a.get_config()["bgm_on"] is False and "Chưa chọn" in a._lbl_bgm_file.text()
    a._bgm_path, a._bgm_name = song, "Bài hát.mp3"
    a._chk_bgm.setChecked(True)
    a._sld_bgm.setValue(-12)
    assert "Bài hát.mp3 · 1:15" in a._lbl_bgm_file.text()
    assert "nhạc của bạn" in a._hint.text()
    cfg = a.get_config()
    assert (cfg["bgm_on"], cfg["bgm_path"], cfg["bgm_db"]) == (True, song, -12)
    b = OptionsPanel()                                  # remembered like the logo
    assert b.get_config()["bgm_path"] == song and b.get_config()["bgm_db"] == -12
    b.set_config({k: v for k, v in cfg.items() if not k.startswith("bgm")})   # old config
    assert b.get_config()["bgm_on"] is False
