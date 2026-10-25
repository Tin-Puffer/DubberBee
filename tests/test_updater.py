"""In-app update (dubber/core/updater.py), resumable download, first-run model catalog."""
from __future__ import annotations

import hashlib
import http.server
import io
import tarfile
import threading
from pathlib import Path

import pytest

from dubber.core import model_setup, updater
from dubber.core.utils import download

# ── release check ─────────────────────────────────────────────────────────────


def _release(tag, assets=("Dubber-macos.zip", "Dubber-windows.zip"), **kw):
    return {"tag_name": tag, "body": f"notes {tag}", "html_url": f"https://x/{tag}",
            "assets": [{"name": n, "browser_download_url": f"https://dl/{tag}/{n}", "size": 10,
                        "digest": "sha256:" + "ab" * 32} for n in assets], **kw}


def test_parse_version():
    assert updater.parse_version("v0.3.0") == (0, 3, 0)
    assert updater.parse_version("0.10.1") == (0, 10, 1)
    assert updater.parse_version("models-v1") == ()
    assert updater.parse_version("v0.10.0") > updater.parse_version("v0.9.9")


def test_check_picks_newest_release_with_this_platforms_zip(monkeypatch):
    releases = [
        _release("v0.6.0", draft=True),
        _release("v0.5.0", prerelease=True),
        _release("v0.4.1", assets=("other.zip",)),
        _release("v0.4.0"),
        _release("models-v1", assets=("adam1.onnx",)),
        _release("v0.3.0"),
    ]
    monkeypatch.setattr(download, "get_json", lambda url: releases)
    rel = updater.check("0.3.0")
    assert rel.version == "0.4.0"
    assert rel.url.endswith(updater.asset_name())
    assert rel.sha256 == "ab" * 32
    assert rel.notes == "notes v0.4.0"


def test_check_none_when_up_to_date(monkeypatch):
    monkeypatch.setattr(download, "get_json", lambda url: [_release("v0.3.0")])
    assert updater.check("0.3.0") is None


def test_running_from_source_cannot_self_update():
    assert updater.install_dir() is None
    assert "mã nguồn" in updater.cannot_update_reason()


# ── resumable download ────────────────────────────────────────────────────────

PAYLOAD = bytes(range(256)) * 4096   # 1 MiB


class _RangeHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        start = 0
        rng = self.headers.get("Range")
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
        body = PAYLOAD[start:]
        self.send_response(206 if rng else 200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):   # client cancelled mid-file
            pass

    def log_message(self, *a):
        pass


@pytest.fixture
def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RangeHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/f"
    srv.shutdown()


def test_fetch_resumes_partial_file_and_checks_sha(server, tmp_path):
    dest = tmp_path / "f.bin"
    (tmp_path / "f.bin.part").write_bytes(PAYLOAD[:1000])   # left by an interrupted run
    seen = []
    download.fetch(server, dest, progress=lambda d, t: seen.append((d, t)),
                   sha256=hashlib.sha256(PAYLOAD).hexdigest())
    assert dest.read_bytes() == PAYLOAD
    assert seen[-1] == (len(PAYLOAD), len(PAYLOAD))
    assert not (tmp_path / "f.bin.part").exists()


def test_fetch_wrong_sha_removes_file(server, tmp_path):
    with pytest.raises(ValueError):
        download.fetch(server, tmp_path / "f.bin", sha256="00" * 32)
    assert not list(tmp_path.iterdir())


def test_fetch_cancel_keeps_part(server, tmp_path):
    with pytest.raises(download.DownloadCancelled):
        download.fetch(server, tmp_path / "f.bin", cancel=lambda: True)
    assert (tmp_path / "f.bin.part").exists()
    assert not (tmp_path / "f.bin").exists()


# ── first-run models ──────────────────────────────────────────────────────────


def test_catalog_has_everything_default_voice_first():
    names = [m.asset for m in model_setup.catalog()]
    assert names[0].startswith("sherpa-onnx-sense-voice")
    voices = [n for n in names if n.endswith(".onnx") and not n.startswith(("UVR", "silero", "fsmn"))]
    assert voices[0] == model_setup.DEFAULT_PIPER_VOICE + ".onnx"
    assert len(voices) == len(model_setup.PIPER_VOICES) == 16
    assert "vieneu-v3-turbo-onnx.tar" in names
    assert "UVR-MDX-NET-Inst_HQ_5.onnx" in names


def test_missing_models_and_download_unpacks_sherpa(tmp_path, monkeypatch):
    models, uvr = tmp_path / "models", tmp_path / "uvr"
    monkeypatch.setattr(model_setup, "_search_bases", lambda: [models])
    monkeypatch.setattr("dubber.core.audio.separate._uvr_model_dir", lambda: uvr)
    items = model_setup.catalog(voices=[model_setup.DEFAULT_PIPER_VOICE], vieneu=False)
    assert [m for m in items if not m.present()] == items

    # sherpa archive: the app keeps only model.int8.onnx + tokens.txt
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:bz2") as tf:
        for name in ("model.int8.onnx", "tokens.txt", "test_wavs/zh.wav", "model.onnx"):
            data = name.encode()
            info = tarfile.TarInfo(f"{model_setup.SHERPA_MODEL_NAME}/{name}")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    archive = buf.getvalue()

    def fake_fetch(url, dest, progress=None, cancel=None, sha256=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(archive if url.endswith(".tar.bz2") else b"x")
        if progress:
            progress(1, 1)
        return dest

    monkeypatch.setattr(download, "fetch", fake_fetch)
    monkeypatch.setattr(model_setup, "mirror_info", lambda: {})
    model_setup.download_models(items, dest=models)

    assert all(m.present() for m in items)
    sherpa = models / model_setup.SHERPA_MODEL_NAME
    assert sorted(p.name for p in sherpa.iterdir()) == ["model.int8.onnx", "tokens.txt"]
    assert not (models / ".download").exists()


def _fake_items(tmp_path, monkeypatch):
    models, uvr = tmp_path / "models", tmp_path / "uvr"
    monkeypatch.setattr(model_setup, "_search_bases", lambda: [models])
    monkeypatch.setattr("dubber.core.audio.separate._uvr_model_dir", lambda: uvr)
    monkeypatch.setattr(model_setup, "mirror_info", lambda: {})
    return models, uvr


def test_mirror_down_uses_original_sources_and_fallbacks(tmp_path, monkeypatch):
    """Releases repo unreachable / private: URLs fall back upstream; UVR + VieNeu use
    their own downloads (audio-separator / HuggingFace)."""
    models, uvr = _fake_items(tmp_path, monkeypatch)
    from dubber.core.audio.separate import UVR_FILES
    fetched = []

    def fake_fetch(url, dest, progress=None, cancel=None, sha256=None):
        if model_setup.MODELS_MIRROR_URL in url:
            raise ConnectionError("404")
        fetched.append(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"x")
        return dest

    def uvr_upstream(base):
        for f in UVR_FILES:
            (base / f).parent.mkdir(parents=True, exist_ok=True)
            (base / f).write_bytes(b"x")

    def vieneu_upstream(base):
        root = base / "vieneu"
        for f in ("VieNeu-TTS-v3-Turbo/onnx_update/vieneu_prefill.onnx",
                  "MOSS-Audio-Tokenizer-Nano-ONNX/moss_audio_tokenizer_decode_full.onnx"):
            (root / f).parent.mkdir(parents=True, exist_ok=True)
            (root / f).write_bytes(b"x")

    monkeypatch.setattr(download, "fetch", fake_fetch)
    monkeypatch.setattr("dubber.core.audio.separate.download_uvr_upstream", uvr_upstream)
    monkeypatch.setattr("dubber.core.asr.model_manager.download_vieneu_upstream", vieneu_upstream)
    items = [m for m in model_setup.catalog(voices=[model_setup.DEFAULT_PIPER_VOICE])
             if m.unpack != "sherpa"]
    assert model_setup.download_models(items, dest=models) == []
    assert all(m.present() for m in items)
    assert any("huggingface.co" in u for u in fetched)


def test_optional_failure_is_skipped_required_failure_raises(tmp_path, monkeypatch):
    models, _ = _fake_items(tmp_path, monkeypatch)

    def broken(*a, **k):
        raise ConnectionError("offline")

    monkeypatch.setattr(download, "fetch", broken)
    monkeypatch.setattr("dubber.core.audio.separate.download_uvr_upstream", broken)
    monkeypatch.setattr("dubber.core.asr.model_manager.download_vieneu_upstream", broken)
    items = model_setup.catalog(voices=["adam1"])
    optional = [m for m in items if not m.required]
    assert model_setup.download_models(optional, dest=models) == optional
    with pytest.raises(ConnectionError):
        model_setup.download_models([m for m in items if m.required], dest=models)
    assert {m.asset for m in model_setup.catalog() if m.required} >= {
        "silero_vad.onnx", model_setup.DEFAULT_PIPER_VOICE + ".onnx"}
