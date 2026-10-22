"""VieNeu voice library: clone / list / rename / delete (SDK mocked, real FFmpeg)."""
from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np
import pytest

from dubber.core.tts import voice_library as vl


def _ffmpeg_or_skip():
    from dubber.core.utils.ffmpeg_locator import find_ffmpeg
    try:
        find_ffmpeg()
    except FileNotFoundError:
        pytest.skip("FFmpeg not available")


def _wav(path: Path, seconds: float, sr: int = 22050, channels: int = 2) -> Path:
    t = np.arange(int(seconds * sr)) / sr
    x = (0.3 * np.sin(2 * np.pi * 220 * t) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels); wf.setsampwidth(2); wf.setframerate(sr)
        wf.writeframes(np.repeat(x, channels).tobytes())
    return path


class FakeSDK:
    def __init__(self):
        self.refs = []

    def encode_reference(self, path):
        self.refs.append(path)
        with wave.open(path) as wf:
            assert wf.getnchannels() == 1
        return np.linspace(0, 1, 192, dtype=np.float32), np.ones((40, 16), dtype=np.int64)


def test_add_clone_stores_cut_reference_and_embedding(tmp_path):
    _ffmpeg_or_skip()
    src = _wav(tmp_path / "long.wav", 12.0)
    sdk = FakeSDK()
    entry = vl.add_clone(src, "Giọng Đức", sdk, start_s=2.0, root=tmp_path / "lib")
    assert entry.kind == "clone" and entry.name == "Giọng Đức"
    vid = entry.id.split(":", 1)[1]
    assert vid.startswith("giong-duc-")
    d = tmp_path / "lib" / vid
    with wave.open(str(d / "reference.wav")) as wf:
        dur = wf.getnframes() / wf.getframerate()
        assert wf.getnchannels() == 1
    assert dur == pytest.approx(vl.MAX_REF_S, abs=0.05)       # cut to 8 s
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    assert meta["source_file_name"] == "long.wav" and meta["duration"] == pytest.approx(8.0, abs=0.05)
    data = vl.load_voice_data(vid, root=tmp_path / "lib")
    assert data["speaker_emb"].shape == (192,) and data["speaker_emb"].dtype == np.float32
    assert data["codes"].shape == (40, 16) and data["codes"].dtype == np.int64
    assert not list((tmp_path / "lib").glob(".*.tmp"))       # no leftovers


def test_list_rename_delete(tmp_path):
    _ffmpeg_or_skip()
    root = tmp_path / "lib"
    src = _wav(tmp_path / "a.wav", 5.0)
    a = vl.add_clone(src, "Bé Na", FakeSDK(), root=root)
    vl.add_clone(src, "An", FakeSDK(), root=root)
    assert [v.name for v in vl.clone_voices(root)] == ["An", "Bé Na"]
    with pytest.raises(ValueError):
        vl.add_clone(src, "bé na", FakeSDK(), root=root)          # names are unique
    a_id = a.id.split(":", 1)[1]
    with pytest.raises(ValueError):
        vl.rename(a_id, "AN", root=root)
    vl.rename(a_id, "Bé Na 2", root=root)
    assert "Bé Na 2" in [v.name for v in vl.clone_voices(root)]
    vl.rename(a_id, "Bé Na 2", root=root)                        # same name on itself is fine
    vl.delete(a_id, root=root)
    assert not vl.clone_exists(a_id, root=root)
    with pytest.raises(ValueError, match="đã bị xóa"):
        vl.load_voice_data(a_id, root=root)
    assert [v.name for v in vl.clone_voices(root)] == ["An"]


def test_bad_audio_leaves_nothing(tmp_path):
    _ffmpeg_or_skip()
    bad = tmp_path / "x.wav"
    bad.write_bytes(b"not audio")
    with pytest.raises(RuntimeError):
        vl.add_clone(bad, "X", FakeSDK(), root=tmp_path / "lib")
    assert vl.clone_voices(tmp_path / "lib") == []


def test_empty_name_rejected(tmp_path):
    with pytest.raises(ValueError):
        vl.add_clone(tmp_path / "a.wav", "  ", FakeSDK(), root=tmp_path)


def test_presets_from_installed_sdk():
    pytest.importorskip("vieneu")
    presets = vl.preset_voices()
    assert len(presets) >= 10
    assert all(p.id == f"preset:{p.name}" and p.kind == "preset" for p in presets)
    assert "Hải Đăng" in [p.name for p in presets]


def test_engine_resolves_clone(tmp_path, monkeypatch):
    _ffmpeg_or_skip()
    from dubber.core.tts.vieneu_tts import VieNeuTTSEngine
    root = tmp_path / "lib"
    monkeypatch.setattr(vl, "voices_dir", lambda: root)
    e = vl.add_clone(_wav(tmp_path / "a.wav", 5.0), "Thử", FakeSDK())

    class SDK:
        sample_rate = 48_000
        def resolve_voice_name(self, n): return None
        def infer(self, text, voice=None):
            assert isinstance(voice, dict) and voice["speaker_emb"].shape == (192,)
            return np.zeros(4800, dtype=np.float32)

    eng = VieNeuTTSEngine(e.id, tmp_path / "tts", tts=SDK())
    import asyncio
    assert asyncio.run(eng.synthesize_one("xin chào", 0)).duration == pytest.approx(0.1)
    vl.delete(e.id.split(":", 1)[1])
    with pytest.raises(ValueError, match="đã bị xóa"):
        VieNeuTTSEngine(e.id, tmp_path / "tts", tts=SDK())
