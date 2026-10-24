"""VideoRenderer: final video assembly using FFmpeg."""
from __future__ import annotations

import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

from dubber.core.utils.ffmpeg_locator import no_window

log = logging.getLogger(__name__)


def _esc(path: Path) -> str:
    """Escape path for use inside FFmpeg filter strings (forward slashes, colon)."""
    s = str(path).replace("\\", "/").replace(":", "\\:")
    return s


_ENABLE_MAX_TERMS = 50   # FFmpeg's expression parser fails above 100 terms
_ENABLE_MERGE_GAP = 0.3  # seconds — bridge tiny gaps between consecutive subtitles


def _enable_groups(intervals: list[tuple[float, float]]) -> list[str]:
    """Merge overlapping/near-touching (start, end) intervals, then return
    `between(t,s,e)+…` expressions of at most _ENABLE_MAX_TERMS terms each."""
    merged: list[list[float]] = []
    for s, e in sorted(intervals):
        if merged and s - merged[-1][1] <= _ENABLE_MERGE_GAP:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    terms = [f"between(t,{s:.3f},{e:.3f})" for s, e in merged]
    return [
        "+".join(terms[i : i + _ENABLE_MAX_TERMS])
        for i in range(0, len(terms), _ENABLE_MAX_TERMS)
    ]


def _detect_hw_encoder(ffmpeg: Path) -> str:
    """Detect best available H.264 encoder on this machine."""
    result = subprocess.run(
        [str(ffmpeg), "-encoders", "-v", "quiet"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", **no_window(),
    )
    encoders = result.stdout

    if sys.platform == "darwin" and "h264_videotoolbox" in encoders:
        candidate = "h264_videotoolbox"
    elif "h264_nvenc" in encoders:
        candidate = "h264_nvenc"
    else:
        return "libx264"

    # Verify the candidate actually works with a quick probe
    test = subprocess.run(
        [str(ffmpeg), "-f", "lavfi", "-i", "nullsrc=s=16x16:d=0.1",
         "-c:v", candidate, "-f", "null", "-"],
        capture_output=True, **no_window(),
    )
    return candidate if test.returncode == 0 else "libx264"


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+)\.(\d+)")
_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+)\.(\d+)")


def _hms_to_sec(h: str, m: str, s: str, cs: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(cs) / 100


def _run_ffmpeg_with_progress(
    cmd: list,
    on_progress: Optional[Callable[[float], None]],
) -> str | None:
    """Run FFmpeg, parse stderr for progress, call on_progress(0.0–1.0).

    Returns accumulated stderr on success, None on non-zero exit.
    """
    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", **no_window())
    total_sec: float = 0.0
    stderr_buf: list[str] = []

    try:
        for line in proc.stderr:
            stderr_buf.append(line)
            if not total_sec:
                m = _DURATION_RE.search(line)
                if m:
                    total_sec = _hms_to_sec(*m.groups())
            if total_sec and on_progress:
                m = _TIME_RE.search(line)
                if m:
                    current = _hms_to_sec(*m.groups())
                    # Cap at 0.99: only send 1.0 after proc.wait() confirms file is finalized
                    # (FFmpeg runs faststart rewrite after encoding — no time= output during that)
                    on_progress(min(current / total_sec, 0.99))
    except BaseException:
        proc.kill()   # e.g. the job was cancelled from on_progress
        proc.wait()
        raise

    proc.wait()
    if proc.returncode != 0:
        tail = "".join(stderr_buf[-40:])
        raise RuntimeError(f"FFmpeg render failed:\n{tail}")
    if on_progress:
        on_progress(1.0)
    return "".join(stderr_buf)


class VideoRenderer:
    def __init__(self, ffmpeg: Path) -> None:
        self._ffmpeg = ffmpeg

    def mux_dub(
        self,
        video_path: Path,
        dub_wav: Path,
        output_path: Path,
        orig_volume: float = 0.15,
    ) -> Path:
        """M4: mix dub.wav with original audio (reduced), stream-copy video.

        orig_volume: original audio level (0.0–1.0). Default 0.15 = 15% —
        keeps background music and original speech audible but quiet under
        the Vietnamese TTS track.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        af = (
            f"[0:a]volume={orig_volume}[orig];"
            f"[orig][1:a]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"
        )
        cmd = [
            str(self._ffmpeg), "-y",
            "-i", str(video_path),
            "-i", str(dub_wav),
            "-filter_complex", af,
            "-map", "0:v:0",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            str(output_path),
        ]
        log.info(
            "Muxing dubbed video (orig=%.0f%%) → %s",
            orig_volume * 100, output_path.name,
        )
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **no_window())
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg mux failed:\n{result.stderr[-2000:]}")
        size_mb = output_path.stat().st_size / 1024 / 1024
        log.info("Dubbed video: %s  (%.1f MB)", output_path.name, size_mb)
        return output_path

    def render_final(
        self,
        video_path: Path,
        dub_wav: "Path | None",
        ass_path: Path,
        output_path: Path,
        orig_volume: float = 0.15,
        vi_volume: float = 1.0,
        background_wav: "Path | None" = None,
        blur_band: "list | None" = None,
        target_video_kbps: int = 0,
        fonts_dir: Path | None = None,
        on_progress: Optional[Callable[[float], None]] = None,
        scale: "str | None" = None,
        fps: "int | None" = None,
        crf: int = 23,
        encoder_preset: str = "medium",
        audio_bitrate: str = "192k",
        logo: "tuple | None" = None,
        flip: bool = False,
        music: "tuple | None" = None,
    ) -> Path:
        """M5: burn subtitles + blur band into video, mix audio, re-encode.

        Args:
            video_path: Original input video.
            dub_wav: Vietnamese TTS audio track (None: subtitles only, no Vietnamese voice).
            ass_path: ASS subtitle file to burn in.
            output_path: Final output path.
            orig_volume: Original audio level (0.0–1.0).
            blur_band: (y, h) — full-width constant blur strip at the bottom.
                       Covers original Chinese burned-in subs and acts as background
                       for Vietnamese subtitle text. Always on for entire duration.
            target_video_kbps: Encode video at this bitrate (0 = use CRF 28 fallback).
            fonts_dir: Directory with bundled fonts for ASS renderer.
            logo: (image, x, y, w, h, opacity 0–1) — channel logo, under the subtitles.
            flip: mirror the picture horizontally first — blur / logo / subtitle positions
                  are picked on the mirrored preview, so they apply after it.
            music: (file, volume 0–1, video seconds) — the user's own music, looped to the
                   video's length and faded out at the end, mixed with the other tracks.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        encoder = _detect_hw_encoder(self._ffmpeg)
        log.info("Video encoder: %s", encoder)

        # ── Inputs: 0 video, then whichever of dub / background / logo are used ──
        use_bg = bool(background_wav) and orig_volume > 0
        inputs = ["-i", str(video_path)]
        dub_idx = bg_idx = music_idx = logo_idx = None
        n = 1
        if dub_wav:
            inputs += ["-i", str(dub_wav)]
            dub_idx, n = n, n + 1
        if use_bg:
            inputs += ["-i", str(background_wav)]
            bg_idx, n = n, n + 1
        if music:
            inputs += ["-stream_loop", "-1", "-i", str(music[0])]   # loops; trimmed below
            music_idx, n = n, n + 1
        if logo:
            inputs += ["-i", str(logo[0])]
            logo_idx = n

        # ── Build filter_complex ──────────────────────────────────────────────
        ass_filter = f"ass='{_esc(ass_path)}'"
        if fonts_dir and fonts_dir.is_dir():
            ass_filter = f"ass='{_esc(ass_path)}':fontsdir='{_esc(fonts_dir)}'"

        vf_parts: list[str] = []
        current = "[0:v]"
        if flip:
            vf_parts.append("[0:v]hflip[mirrored]")
            current = "[mirrored]"

        # Blur boxes: each entry is (start_sec, end_sec, x, y, w, h).
        # ONE crop/blur over the union rectangle, shown while any subtitle is on screen.
        # A split/crop/blur/overlay chain per subtitle makes FFmpeg 7+ crawl or deadlock
        # (34 stages hung on a 2-min clip). The enable expression is capped too: FFmpeg
        # rejects >100 terms ("Cannot allocate memory"), so touching intervals are merged
        # and the rest spread over a few overlays of the same blurred stream.
        # Layer order: original → blur box (active during subs) → ASS text on top.
        if blur_band:
            bx = min(b[2] for b in blur_band)
            by = min(b[3] for b in blur_band)
            bw = max(b[2] + b[4] for b in blur_band) - bx
            bh = max(b[3] + b[5] for b in blur_band) - by
            groups = _enable_groups([(s, e) for s, e, *_ in blur_band])
            # Radius must be < min(bw,bh)/4 for chroma plane (YUV420 is half-res)
            blur_r = max(3, min(15, bw // 4, bh // 4))
            n = len(groups)
            vf_parts.append(f"{current}split[sa][sb]")
            blur_outs = "".join(f"[sblur{i}]" for i in range(n))
            vf_parts.append(
                f"[sa]crop={bw}:{bh}:{bx}:{by},boxblur={blur_r}:3"
                + (f",split={n}" if n > 1 else "") + blur_outs
            )
            base = "[sb]"
            for i, enable in enumerate(groups):
                out = "[af]" if i == n - 1 else f"[ov{i}]"
                vf_parts.append(f"{base}[sblur{i}]overlay={bx}:{by}:enable='{enable}'{out}")
                base = out
            current = "[af]"

        if logo:
            _, lx, ly, lw, lh, opacity = logo
            vf_parts.append(f"[{logo_idx}:v]scale={lw}:{lh},format=rgba,"
                            f"colorchannelmixer=aa={opacity:.3f}[logo]")
            vf_parts.append(f"{current}[logo]overlay={lx}:{ly}[with_logo]")
            current = "[with_logo]"

        _has_post = bool(scale or fps)
        _pre_post = "[after_sub]" if _has_post else "[vout]"
        vf_parts.append(f"{current}{ass_filter}{_pre_post}")
        if scale:
            _pre_fps = "[after_scale]" if fps else "[vout]"
            vf_parts.append(f"[after_sub]scale={scale}:flags=lanczos{_pre_fps}")
        if fps:
            _fps_in = "[after_scale]" if scale else "[after_sub]"
            vf_parts.append(f"{_fps_in}fps=fps={fps}[vout]")
        vf = ";".join(vf_parts)

        # Audio: background (separated stems, or the original track) + the Vietnamese
        # dub, whichever are on; nothing on → a silent video. loudnorm resamples to 192 kHz,
        # which AAC can't take: back to 48 kHz.
        tracks = []   # (label, filter chain)
        if use_bg:
            tracks.append(("bg_a", f"[{bg_idx}:a]volume={orig_volume}"))
        elif orig_volume > 0:
            tracks.append(("bg_a", f"[0:a]volume={orig_volume}"))
        if dub_wav:
            tracks.append(("dub_a", f"[{dub_idx}:a]volume={vi_volume}"))
        if music:
            from dubber.core.audio.music import FADE_OUT_S
            _, m_vol, seconds = music
            fade_at = max(0.0, seconds - FADE_OUT_S)
            tracks.append(("music_a", f"[{music_idx}:a]volume={m_vol},atrim=0:{seconds:.3f},"
                                      f"asetpts=PTS-STARTPTS,afade=t=out:st={fade_at:.3f}"
                                      f":d={FADE_OUT_S}"))
        norm = "loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000[aout]"
        if len(tracks) > 1:
            af = (";".join(f"{chain}[{label}]" for label, chain in tracks) + ";"
                  + "".join(f"[{label}]" for label, _ in tracks)
                  + f"amix=inputs={len(tracks)}:duration=first:dropout_transition=0:normalize=0,"
                  + norm)
        elif tracks:
            af = f"{tracks[0][1]}," + norm
        else:
            af = None

        # ── Build encoder args using CRF-based quality ────────────────────────
        if encoder == "h264_videotoolbox":
            # videotoolbox quality scale: 100=best, 0=worst; map from CRF (lower=better)
            q = max(1, min(100, (51 - crf) * 2))
            enc_args = ["-c:v", "h264_videotoolbox", "-q:v", str(q)]
        elif encoder == "h264_nvenc":
            _nvenc_preset = {"ultrafast": "p1", "fast": "p3", "medium": "p4", "slow": "p7"}.get(encoder_preset, "p4")
            enc_args = ["-c:v", "h264_nvenc", "-preset", _nvenc_preset, "-cq", str(crf)]
        else:  # libx264
            enc_args = ["-c:v", "libx264", "-crf", str(crf), "-preset", encoder_preset]

        audio_args = (["-map", "[aout]", "-c:a", "aac", "-b:a", audio_bitrate] if af
                      else ["-an"])
        cmd = [
            str(self._ffmpeg), "-y",
            *inputs,
            "-filter_complex", vf + (";" + af if af else ""),
            "-map", "[vout]",
            *enc_args,
            *audio_args,
            "-movflags", "+faststart",
            str(output_path),
        ]
        log.info("Rendering final video → %s", output_path.name)
        stderr_data = _run_ffmpeg_with_progress(cmd, on_progress)
        if stderr_data is None:
            raise RuntimeError("FFmpeg render failed (process error)")
        size_mb = output_path.stat().st_size / 1024 / 1024
        log.info("Output: %s  (%.1f MB)", output_path.name, size_mb)
        return output_path

    def passthrough(self, input_path: Path, output_path: Path) -> Path:
        """M0: stream-copy the input video without re-encoding."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            str(self._ffmpeg),
            "-y",
            "-i", str(input_path),
            "-c", "copy",
            "-movflags", "+faststart",
            str(output_path),
        ]
        log.info("Copying video → %s", output_path.name)
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **no_window())
        if result.returncode != 0:
            raise RuntimeError(
                f"FFmpeg video copy failed:\n{result.stderr[-2000:]}"
            )
        size_mb = output_path.stat().st_size / 1024 / 1024
        log.info("Output: %s  (%.1f MB)", output_path.name, size_mb)
        return output_path
