"""Integration-style test for M0 pipeline using mocked subprocess."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from dubber.core.pipeline import Pipeline

PROBE_JSON = json.dumps({
    "streams": [
        {"codec_type": "video", "width": 1280, "height": 720, "r_frame_rate": "25/1"},
        {"codec_type": "audio"},
    ],
    "format": {"duration": "30.0"},
})


@pytest.fixture
def pipeline(fake_ffmpeg, fake_ffprobe, tmp_path):
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    return Pipeline(ffmpeg=fake_ffmpeg, ffprobe=fake_ffprobe, job_dir=job_dir)


def test_run_m0_calls_ffmpeg_twice(pipeline, tmp_path):
    """M0 pipeline must call ffprobe (probe) + ffmpeg (extract) + ffmpeg (copy)."""
    video = tmp_path / "input.mp4"
    video.touch()
    output = tmp_path / "output.mp4"

    call_count = [0]

    def fake_run(cmd, **kwargs):
        call_count[0] += 1
        cmd_str = " ".join(str(c) for c in cmd)

        if "ffprobe" in cmd_str:
            return MagicMock(stdout=PROBE_JSON, returncode=0)

        # ffmpeg extract: create audio.wav
        if "-vn" in cmd:
            wav = next((Path(c) for c in cmd if str(c).endswith(".wav")), None)
            if wav:
                wav.parent.mkdir(parents=True, exist_ok=True)
                wav.write_bytes(b"\x00" * 512)
            return MagicMock(returncode=0, stderr="")

        # ffmpeg copy: create output
        if "-c" in cmd and "copy" in cmd:
            out = Path(cmd[-1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"\x00" * 1024)
            return MagicMock(returncode=0, stderr="")

        return MagicMock(returncode=0, stderr="", stdout="")

    with patch("subprocess.run", side_effect=fake_run):
        pipeline.run_m0(video, output)

    assert call_count[0] == 3  # ffprobe + ffmpeg extract + ffmpeg copy
    assert output.exists()
    assert (pipeline.job_dir / "audio.wav").exists()


def test_run_m0_output_path(pipeline, tmp_path):
    video = tmp_path / "my_video.mp4"
    video.touch()
    output = tmp_path / "my_video_vi.mp4"

    def fake_run(cmd, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd)
        if "ffprobe" in cmd_str:
            return MagicMock(stdout=PROBE_JSON, returncode=0)
        out_path = Path(cmd[-1])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"\x00" * 512)
        return MagicMock(returncode=0, stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        pipeline.run_m0(video, output)

    assert output.name == "my_video_vi.mp4"
