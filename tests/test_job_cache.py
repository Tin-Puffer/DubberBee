"""asr.json / translate.json are reused only when their signature matches."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dubber.core.asr.base import ASRSegment
from dubber.core.pipeline import Pipeline
from dubber.core.translate.base import TranslateResult
from dubber.core.utils.job_cache import sig_matches, sig_path, write_sig


class _Env:
    """Pipeline with ASR engine + translator mocked; counts how often each runs."""

    def __init__(self, job_dir: Path, models: Path) -> None:
        self.job_dir = job_dir
        self.models = models
        self.vad_model: Path | None = models / "silero_vad.onnx"
        self.fsmn_dir: Path | None = None
        self.asr_calls = 0
        self.translate_calls: list[str] = []

    def _transcribe(self, _wav):
        self.asr_calls += 1
        if self.vad_model is None:
            return [ASRSegment(0.0, 3.5, "你好。再见。")]
        if self.vad_used == "fsmn":
            return [ASRSegment(0.0, 1.6, "你好。"), ASRSegment(2.0, 3.5, "再见。")]
        return [ASRSegment(0.0, 1.5, "你好。"), ASRSegment(2.0, 3.5, "再见。")]

    def _translator(self, name):
        tr = MagicMock()

        def translate(segs):
            self.translate_calls.append(name)
            return [TranslateResult(id=s.id, vi=f"{name}-{s.id}") for s in segs]
        tr.translate.side_effect = translate
        return tr

    def run(self, translator: str = "auto", vad: str = "fsmn", review=None):
        pipe = Pipeline(ffmpeg=Path("ffmpeg"), ffprobe=Path("ffprobe"), job_dir=self.job_dir)
        pipe.review_translation = review
        pipe._extractor = MagicMock()
        engine = MagicMock()
        engine.transcribe.side_effect = self._transcribe
        engine.vad_used = self.vad_used = ("fsmn" if vad == "fsmn" and self.fsmn_dir
                                           else "silero" if self.vad_model else "none")
        with patch("dubber.core.asr.model_manager.find_sherpa_model_dir",
                   return_value=self.models / "sense-voice"), \
             patch("dubber.core.asr.model_manager.find_silero_vad",
                   return_value=self.vad_model), \
             patch("dubber.core.asr.model_manager.find_fsmn_vad_dir",
                   return_value=self.fsmn_dir), \
             patch("dubber.core.asr.sensevoice_sherpa.SherpaOnnxASR", return_value=engine), \
             patch("dubber.core.pipeline._resolve_translator", side_effect=self._translator):
            return pipe.run_m2(Path("in.mp4"), translator=translator, asr_on_vocals=False,
                               vad=vad)


@pytest.fixture
def env(tmp_path):
    job = tmp_path / "job"
    job.mkdir()
    (job / "audio.wav").write_bytes(b"RIFF fake audio")
    return _Env(job, tmp_path / "models")


def test_same_settings_reuse_asr_and_translation(env):
    env.run("groq")
    env.run("groq")
    assert env.asr_calls == 1
    assert env.translate_calls == ["groq"]


def test_changing_translator_retranslates_only(env):
    env.run("groq")
    _, tj = env.run("local")
    assert env.asr_calls == 1
    assert env.translate_calls == ["groq", "local"]
    assert "Local-0." in tj.read_text(encoding="utf-8")   # normalized: capital, full stop


def test_changing_vad_reruns_asr_and_translation(env):
    env.run("groq")
    env.vad_model = None   # no VAD → different ASR signature
    env.run("groq")
    assert env.asr_calls == 2
    assert env.translate_calls == ["groq", "groq"]


def test_switching_fsmn_and_silero_reruns_asr(env):
    env.fsmn_dir = env.models / "fsmn-vad"
    env.run("groq", vad="fsmn")
    env.run("groq", vad="silero")
    env.run("groq", vad="silero")
    assert env.asr_calls == 2
    assert env.translate_calls == ["groq", "groq"]


def test_changed_audio_reruns_asr(env):
    env.run("groq")
    (env.job_dir / "audio.wav").write_bytes(b"RIFF other audio")
    env.run("groq")
    assert env.asr_calls == 2


def test_rerun_asr_with_identical_output_keeps_translation(env):
    env.run("groq")
    (env.job_dir / "audio.wav").write_bytes(b"RIFF other audio")   # ASR reruns …
    env.run("groq")
    assert env.asr_calls == 2
    assert env.translate_calls == ["groq"]   # … but same text → translation still valid


def test_old_job_without_sig_is_not_reused(env):
    env.run("groq")
    sig_path(env.job_dir / "asr.json").unlink()
    sig_path(env.job_dir / "translations" / "groq.json").unlink()
    env.run("groq")
    assert env.asr_calls == 2
    assert env.translate_calls == ["groq", "groq"]


def test_sig_roundtrip(tmp_path):
    art = tmp_path / "asr.json"
    art.write_text("[]")
    write_sig(art, {"a": 1, "b": None})
    assert sig_path(art).name == "asr.sig.json"
    assert sig_matches(art, {"b": None, "a": 1})
    assert not sig_matches(art, {"a": 2, "b": None})
    art.unlink()
    assert not sig_matches(art, {"a": 1, "b": None})


def test_switching_translators_back_reuses_each_translation(env):
    _, tj = env.run("groq")
    env.run("local")
    _, tj = env.run("groq")                          # back to Groq: no new request
    assert env.translate_calls == ["groq", "local"]
    assert "Groq-0." in tj.read_text(encoding="utf-8")   # translate.json is Groq's again
    env.run("local")
    assert env.translate_calls == ["groq", "local"]
    assert env.asr_calls == 1


def test_stored_translation_ignored_after_asr_text_changes(env):
    env.run("groq")
    env.vad_model = None                             # different segmentation / text
    env.run("groq")
    assert env.translate_calls == ["groq", "groq"]


def test_old_asr_signature_is_upgraded_to_the_source_audio(tmp_path):
    """asr.json signed with the vocal stem's digest (before sep_overlap) stays reusable
    after the stems are re-made at another quality — no new ASR, no new translation."""
    import json
    from dubber.core.pipeline import _upgrade_asr_sig
    from dubber.core.utils.job_cache import file_digest, sig_path, write_sig
    audio, vocals, asr = tmp_path / "audio.wav", tmp_path / "audio_vocals_16k.wav", tmp_path / "asr.json"
    audio.write_bytes(b"source"); vocals.write_bytes(b"stem-0.25"); asr.write_text("[]")
    write_sig(asr, {"audio": file_digest(vocals), "use_vocals": True, "vad": "fsmn"})
    _upgrade_asr_sig(asr, audio, vocals)
    assert json.loads(sig_path(asr).read_text())["audio"] == file_digest(audio)


def test_stale_asr_signature_is_not_upgraded(tmp_path):
    import json
    from dubber.core.pipeline import _upgrade_asr_sig
    from dubber.core.utils.job_cache import sig_path, write_sig
    audio, vocals, asr = tmp_path / "audio.wav", tmp_path / "audio_vocals_16k.wav", tmp_path / "asr.json"
    audio.write_bytes(b"source"); vocals.write_bytes(b"stem"); asr.write_text("[]")
    write_sig(asr, {"audio": "made-from-something-else", "use_vocals": True})
    _upgrade_asr_sig(asr, audio, vocals)
    assert json.loads(sig_path(asr).read_text())["audio"] == "made-from-something-else"
