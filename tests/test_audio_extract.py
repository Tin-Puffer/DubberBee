"""Tests for dubber.core.audio.extract.AudioExtractor."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dubber.core.audio.extract import AudioExtractor
from dubber.core.models import VideoInfo

PROBE_JSON = json.dumps({
    "streams": [
        {
            "codec_type": "video",
            "width": 1920,
            "height": 1080,
            "r_frame_rate": "30/1",
        },
        {
            "codec_type": "audio",
        },
    ],
    "format": {"duration": "120.5"},
})

PROBE_NO_AUDIO_JSON = json.dumps({
    "streams": [
        {
            "codec_type": "video",
            "width": 1280,
            "height": 720,
            "r_frame_rate": "25/1",
        },
    ],
    "format": {"duration": "60.0"},
})


@pytest.fixture
def extractor(fake_ffmpeg, fake_ffprobe):
    return AudioExtractor(ffmpeg=fake_ffmpeg, ffprobe=fake_ffprobe)


class TestProbe:
    def test_returns_video_info(self, extractor, tmp_path):
        video = tmp_path / "test.mp4"
        video.touch()

        mock_result = MagicMock(stdout=PROBE_JSON, returncode=0)
        with patch("subprocess.run", return_value=mock_result):
            info = extractor.probe(video)

        assert isinstance(info, VideoInfo)
        assert info.duration == pytest.approx(120.5)
        assert info.width == 1920
        assert info.height == 1080
        assert info.fps == pytest.approx(30.0)
        assert info.has_audio is True
        assert info.path == video

    def test_raises_on_no_audio(self, extractor, tmp_path):
        video = tmp_path / "silent.mp4"
        video.touch()

        mock_result = MagicMock(stdout=PROBE_NO_AUDIO_JSON, returncode=0)
        with patch("subprocess.run", return_value=mock_result):
            with pytest.raises(ValueError, match="No audio stream"):
                extractor.probe(video)

    def test_fps_calculated_correctly(self, extractor, tmp_path):
        video = tmp_path / "film.mp4"
        video.touch()

        data = json.dumps({
            "streams": [
                {"codec_type": "video", "width": 1920, "height": 1080, "r_frame_rate": "24000/1001"},
                {"codec_type": "audio"},
            ],
            "format": {"duration": "90.0"},
        })
        mock_result = MagicMock(stdout=data, returncode=0)
        with patch("subprocess.run", return_value=mock_result):
            info = extractor.probe(video)

        assert info.fps == pytest.approx(24000 / 1001, rel=1e-4)


class TestExtractAudio:
    def test_returns_output_path(self, extractor, tmp_path):
        video = tmp_path / "input.mp4"
        video.touch()
        output_wav = tmp_path / "audio.wav"

        def fake_run(cmd, **kwargs):
            output_wav.write_bytes(b"\x00" * 2048)
            return MagicMock(returncode=0, stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            result = extractor.extract_audio(video, output_wav)

        assert result == output_wav
        assert output_wav.exists()

    def test_creates_parent_dirs(self, extractor, tmp_path):
        video = tmp_path / "input.mp4"
        video.touch()
        nested = tmp_path / "job" / "subdir" / "audio.wav"

        def fake_run(cmd, **kwargs):
            nested.write_bytes(b"\x00" * 512)
            return MagicMock(returncode=0, stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            extractor.extract_audio(video, nested)

        assert nested.parent.exists()

    def test_raises_on_ffmpeg_error(self, extractor, tmp_path):
        video = tmp_path / "input.mp4"
        video.touch()
        output_wav = tmp_path / "audio.wav"

        mock_result = MagicMock(returncode=1, stderr="Error: codec not found")
        with patch("subprocess.run", return_value=mock_result):
            with pytest.raises(RuntimeError, match="FFmpeg audio extraction failed"):
                extractor.extract_audio(video, output_wav)

    def test_ffmpeg_command_contains_required_flags(self, extractor, tmp_path):
        video = tmp_path / "input.mp4"
        video.touch()
        output_wav = tmp_path / "audio.wav"
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            output_wav.write_bytes(b"\x00" * 512)
            return MagicMock(returncode=0, stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            extractor.extract_audio(video, output_wav)

        cmd = captured["cmd"]
        assert "-ac" in cmd and "1" in cmd      # mono
        assert "-ar" in cmd and "16000" in cmd  # 16 kHz
        assert "pcm_s16le" in cmd               # PCM WAV
        assert "-vn" in cmd                     # no video


def test_failed_stereo_extraction_leaves_no_cached_file(tmp_path):
    """A half-written audio_stereo.wav must never be reused as a cache."""
    from dubber.core.audio.extract import AudioExtractor

    def failing_ffmpeg(cmd, **kw):          # writes part of its output, then fails
        Path(cmd[-1]).write_text("partial")
        return MagicMock(returncode=1, stderr="boom")
    out = tmp_path / "audio_stereo.wav"
    with patch("subprocess.run", side_effect=failing_ffmpeg), pytest.raises(RuntimeError):
        AudioExtractor(ffmpeg=Path("ffmpeg"), ffprobe=Path("ffprobe")).extract_audio_stereo(
            tmp_path / "v.mp4", out)
    assert not out.exists()
