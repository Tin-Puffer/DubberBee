"""Pipeline orchestrator — runs each step and caches results to job_dir."""
from __future__ import annotations

import logging
from pathlib import Path

from dubber.core.video.fonts import fonts_dir, scaled_font_px

from dubber.core.audio.extract import AudioExtractor
from dubber.core.utils.job_cache import file_digest, sig_matches, write_sig
from dubber.core.video.render import VideoRenderer

log = logging.getLogger(__name__)

# Bump when ASR segmentation changes in a way that should invalidate cached asr.json
ASR_VERSION = 1


class Pipeline:
    def __init__(self, ffmpeg: Path, ffprobe: Path, job_dir: Path) -> None:
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.job_dir = job_dir
        # Optional checkpoint (GUI cancel): raises JobCancelled; called inside long loops
        self.cancel_check = None
        # GUI hooks: manual_translate(zh_txt, count, can_reuse, error) -> Path | "reuse";
        # on_rate_limit(info) -> "continue" | "local" (Groq rate limit)
        self.manual_translate = None
        self.on_rate_limit = None
        self._extractor = AudioExtractor(ffmpeg=ffmpeg, ffprobe=ffprobe)
        self._renderer = VideoRenderer(ffmpeg=ffmpeg)

    def run_m0(self, input_path: Path, output_path: Path) -> None:
        """M0: probe → extract audio → pass-through video."""
        log.info("=== M0 pipeline start ===")

        info = self._extractor.probe(input_path)

        audio_wav = self.job_dir / "audio.wav"
        self._extractor.extract_audio(input_path, audio_wav)

        self._renderer.passthrough(input_path, output_path)

        log.info(
            "=== M0 done  %.1fs  %dx%d  audio→%s  video→%s ===",
            info.duration, info.width, info.height,
            audio_wav.name, output_path.name,
        )

    def run_m1(
        self,
        input_path: Path,
        split_mode: str = "timestamps",
        asr_on_vocals: bool = True,
        on_progress: "dict | None" = None,
        vad: str = "fsmn",
    ) -> Path:
        """M1: probe → extract audio → (separate vocals) → ASR → write asr.json.

        split_mode: "timestamps" (cut sentences on per-token times) or "legacy".
        vad: "fsmn" (FSMN-VAD, Silero fallback) or "silero".
        asr_on_vocals: run ASR on the MDX-Net vocal stem (music removed); falls back to
            the original audio when MDX-Net isn't installed.
        on_progress: optional {"separate": f(pct), "asr": f(pct)} callbacks.

        Returns the path to the asr.json file written inside job_dir.
        """
        from dubber.core.asr.model_manager import (
            find_fsmn_vad_dir, find_sherpa_model_dir, find_silero_vad,
        )
        from dubber.core.asr.sensevoice_sherpa import SherpaOnnxASR
        from dubber.core.asr.postprocess import write_asr_json

        log.info("=== M1 pipeline start ===")

        info = self._extractor.probe(input_path)
        log.info("Video: %.1fs  %dx%d", info.duration, info.width, info.height)

        audio_wav = self.job_dir / "audio.wav"
        if not audio_wav.exists():
            self._extractor.extract_audio(input_path, audio_wav)
        else:
            log.info("Reusing cached audio: %s", audio_wav)

        model_dir = find_sherpa_model_dir()
        vad_model = find_silero_vad()
        fsmn_dir = find_fsmn_vad_dir() if vad == "fsmn" else None
        # Which VAD will run (FSMN falls back to Silero, Silero to none)
        vad_name = "fsmn" if fsmn_dir else ("silero" if vad_model else "none")
        log.info("ASR model: %s  VAD: %s", model_dir.name, vad_name)

        _cbs = on_progress or {}
        asr_wav = audio_wav
        if asr_on_vocals:
            asr_wav = self._vocals_for_asr(input_path, audio_wav, _cbs.get("separate"))

        asr_json = self.job_dir / "asr.json"
        sig = {
            "asr_version": ASR_VERSION,
            "audio": file_digest(asr_wav),
            "vad": vad_name,
            "split_mode": split_mode,
            "use_vocals": asr_wav != audio_wav,
            "sherpa_model_name": model_dir.name,
            "vad_model_name": {"fsmn": fsmn_dir and fsmn_dir.name,
                               "silero": vad_model and vad_model.name}.get(vad_name),
        }
        if sig_matches(asr_json, sig):
            log.info("Reusing cached asr.json (same audio + ASR settings)")
            return asr_json

        if _cbs.get("asr"):
            _cbs["asr"](0.0)
        engine = SherpaOnnxASR(model_dir=model_dir, vad_model=vad_model, split_mode=split_mode,
                               vad=vad, fsmn_dir=fsmn_dir)
        engine.cancel_check = self.cancel_check
        segments = engine.transcribe(asr_wav)
        if engine.vad_used != vad_name:   # FSMN failed to load → Silero
            sig["vad"] = engine.vad_used
            sig["vad_model_name"] = vad_model.name if engine.vad_used == "silero" else None

        write_asr_json(segments, asr_json)
        write_sig(asr_json, sig)

        log.info("=== M1 done  %d segments → %s ===", len(segments), asr_json)
        return asr_json

    def _vocals_for_asr(self, input_path: Path, audio_wav: Path, on_separate=None) -> Path:
        """16 kHz mono vocal stem for ASR, or *audio_wav* when that isn't possible.

        Stems are cached in job_dir/separated, so run_m5 reuses them for the mix.
        Only MDX-Net stems are used — the FFmpeg fallback's band-passed "vocals" make ASR
        worse than the original audio.
        """
        import subprocess
        import wave

        from dubber.core.audio.separate import (
            mdxnet_available, separate_tracks, separation_method,
        )
        from dubber.core.utils.ffmpeg_locator import no_window

        if not mdxnet_available():
            log.info("MDX-Net not installed — ASR runs on the original audio")
            return audio_wav

        stereo_wav = self.job_dir / "audio_stereo.wav"
        sep_dir = self.job_dir / "separated"
        from dubber.core.utils.cancel import JobCancelled
        try:
            self._extractor.extract_audio_stereo(input_path, stereo_wav)
            vocals, _ = separate_tracks(stereo_wav, sep_dir, progress_cb=on_separate)
        except JobCancelled:
            raise
        except Exception as exc:
            log.warning("Vocal separation failed (%s) — ASR runs on the original audio", exc)
            return audio_wav
        if separation_method(sep_dir) != "mdxnet":
            return audio_wav

        vocals_16k = self.job_dir / "audio_vocals_16k.wav"
        result = subprocess.run(
            [str(self.ffmpeg), "-y", "-i", str(vocals), "-ac", "1", "-ar", "16000",
             "-c:a", "pcm_s16le", str(vocals_16k)],
            capture_output=True, text=True, **no_window(),
        )
        if result.returncode != 0:
            log.warning("Vocal stem conversion failed — ASR runs on the original audio")
            return audio_wav

        def _dur(p: Path) -> float:
            with wave.open(str(p), "rb") as wf:
                return wf.getnframes() / wf.getframerate()

        # Token times are only valid on the original timeline
        drift = abs(_dur(vocals_16k) - _dur(audio_wav))
        if drift > 0.05:
            log.warning("Vocal stem is %.3fs off the original — ASR runs on the original", drift)
            return audio_wav
        log.info("ASR input: vocal stem (music removed)")
        return vocals_16k

    def run_m3(
        self,
        input_path: Path,
        voice: str | None = None,
        translator: str = "auto",
        asr_on_vocals: bool = True,
        on_progress: "dict | None" = None,
        vad: str = "fsmn",
        tts_engine: str = "piper",
    ) -> tuple[Path, Path, Path]:
        """M3: run_m2 → TTS → TimingFitter → mix dub.wav.

        tts_engine: "piper" (voice = model stem) or "vieneu" (voice = "preset:<name>").

        Returns (asr_json, translate_json, dub_wav).
        """
        import asyncio
        import json

        from dubber.core.audio.fit import TimingFitter
        from dubber.core.audio.mixer import build_dub_wav
        from dubber.core.tts.factory import make_tts_engine

        asr_json, translate_json = self.run_m2(
            input_path, translator=translator, asr_on_vocals=asr_on_vocals, on_progress=on_progress,
            vad=vad,
        )

        translate_data = json.loads(translate_json.read_text(encoding="utf-8"))
        video_info = self._extractor.probe(input_path)

        tts_dir = self.job_dir / "tts"
        speed_dir = self.job_dir / "tts_speed"

        engine = make_tts_engine(tts_engine, voice, tts_dir)
        on_tts = (on_progress or {}).get("tts")
        if hasattr(engine, "on_progress"):
            def _tts_progress(done, total):
                if self.cancel_check:
                    self.cancel_check()
                if on_tts:
                    on_tts(done / total)
            engine.on_progress = _tts_progress

        items = [(d["id"], d["vi_text"]) for d in translate_data if d.get("vi_text")]
        log.info("=== M3 TTS start  %d segments  engine=%s voice=%s ===",
                 len(items), tts_engine, voice or "default")

        tts_results = asyncio.run(engine.synthesize_all(items))
        log.info("TTS done: %d results", len(tts_results))

        fitter = TimingFitter(
            tts_engine=engine,
            ffmpeg=self.ffmpeg,
            speed_dir=speed_dir,
        )
        fitted = fitter.fit(tts_results, translate_data, video_info.duration)

        _log_timing_stats(fitted)

        dub_wav = self.job_dir / "dub.wav"
        build_dub_wav(fitted, video_info.duration, dub_wav)
        _write_fit_json(fitted, self.job_dir / "fit.json")

        log.info("=== M3 done → %s ===", dub_wav)
        return asr_json, translate_json, dub_wav

    def run_m5(
        self,
        input_path: Path,
        orig_volume: float = 0.20,
        vi_volume: float = 1.0,
        music_volume: "float | None" = None,
        cn_voice_volume: "float | None" = None,
        blur_region: tuple | None = None,
        blur_mode: str = "auto",
        blur_rect: "tuple | None" = None,
        sub_pos: "tuple | None" = None,
        sub_font: str = "Arial",
        sub_size: int = 48,
        translator: str = "auto",
        show_subtitles: bool = True,
        voice: str | None = None,
        keep_music: bool = True,
        keep_cn_voice: bool = True,
        on_progress: "dict | None" = None,
        scale: "str | None" = None,
        fps: "int | None" = None,
        crf: int = 23,
        encoder_preset: str = "medium",
        audio_bitrate: str = "192k",
        asr_on_vocals: bool = True,
        vad: str = "fsmn",
        tts_engine: str = "piper",
    ) -> tuple[Path, Path, Path, Path, Path]:
        """M5: run_m4 → subtitles (.ass) → render final output.mp4.

        Returns (asr_json, translate_json, dub_wav, subs_ass, output_mp4).
        """
        from dubber.core.video.subtitles import build_ass

        asr_json, translate_json, dub_wav, dubbed_mp4 = self.run_m4(
            input_path, translator=translator, voice=voice,
            asr_on_vocals=asr_on_vocals, on_progress=on_progress, vad=vad,
            tts_engine=tts_engine,
        )

        # Load fitted results from dub metadata (re-run fit for subtitle timing)
        import json
        from dubber.core.audio.fit import FittingResult
        translate_data = json.loads(translate_json.read_text(encoding="utf-8"))

        # Build subtitle timing from translate_data + dub.wav segments
        # Use translate_data timestamps directly (same as what fitter used)
        from dubber.core.audio.mixer import _read_wav_float32
        video_info = self._extractor.probe(input_path)

        # Subtitles follow where the Vietnamese voice really is in dub.wav (fit.json),
        # not the Chinese ASR timing — they differ when lines were pushed / sped up.
        tts_dir = self.job_dir / "tts"
        fitted = _load_fit_json(self.job_dir / "fit.json", translate_data)
        if fitted is None:
            log.warning("fit.json missing or stale — subtitles fall back to ASR timing")
            fitted = _rebuild_fitted(translate_data, tts_dir)

        # ── Subtitle layout ────────────────────────────────────────────────────
        w, h = video_info.width, video_info.height

        from dubber.core.video.subtitles import SubtitleStyle
        # margin_v: pixels from bottom edge. 2% keeps sub sát đáy like reference.
        margin_v = max(10, int(h * 0.02))
        sub_center = _norm_point_to_px(sub_pos, w, h)
        sub_style = SubtitleStyle(font_name=sub_font, font_size=sub_size, margin_v=margin_v, pos=sub_center)

        subs_ass = self.job_dir / "subs.ass"
        if show_subtitles:
            build_ass(fitted, w, h, subs_ass, style=sub_style)
            log.info("Subtitles: %s  (%d lines)", subs_ass.name, len(fitted))
        else:
            # Write empty ASS so render_final still has a valid path
            from dubber.core.video.subtitles import _styles_section, _events_header
            subs_ass.write_text(
                "[Script Info]\nScriptType: v4.00+\n\n"
                + _styles_section(sub_style, w, h)
                + "\n[Events]\n"
                + _events_header()
                + "\n",
                encoding="utf-8",
            )
            log.info("Subtitles: disabled")

        # Blur: off / auto (box behind each Vietnamese subtitle) / custom fixed area
        font_size_pt = sub_size
        if blur_region is False:
            blur_mode = "off"
        custom_px = _norm_rect_to_px(blur_rect, w, h) if blur_mode == "custom" else None
        if blur_mode == "off":
            active_blur_band = None
        elif custom_px:
            # Fixed area for the whole video (covers burned-in Chinese subs at any time)
            active_blur_band = [(0.0, video_info.duration + 1.0, *custom_px)]
        elif show_subtitles:
            active_blur_band = _compute_sub_blurs(
                subs_ass, w, h, margin_v, font_size_pt, center=sub_center,
            )
        else:
            active_blur_band = None

        # ── Background audio track ────────────────────────────────────────────
        background_wav: Path | None = None
        effective_volume = 0.0

        # Per-track volumes (fall back to orig_volume for CLI compat)
        _music_vol = music_volume if music_volume is not None else orig_volume
        _voice_vol = cn_voice_volume if cn_voice_volume is not None else orig_volume

        _cbs = on_progress or {}
        on_sep = _cbs.get("separate")

        if not keep_music and not keep_cn_voice:
            effective_volume = 0.0

        elif keep_music and keep_cn_voice:
            if abs(_music_vol - _voice_vol) < 0.01:
                # Same volume: use original audio stream directly (no separation)
                effective_volume = _music_vol if _music_vol > 0 else 0.20
            else:
                # Different volumes: separate both, mix at individual levels
                from dubber.core.audio.separate import separate_tracks
                stereo_wav = self.job_dir / "audio_stereo.wav"
                self._extractor.extract_audio_stereo(input_path, stereo_wav)
                vocals_wav, instrumental_wav = separate_tracks(
                    stereo_wav, self.job_dir / "separated", progress_cb=on_sep
                )
                background_wav = _mix_two_tracks(
                    instrumental_wav, vocals_wav, _music_vol, _voice_vol,
                    self.ffmpeg, self.job_dir,
                )
                effective_volume = 1.0  # pre-scaled in background_wav

        elif keep_music:  # only music, not voice
            from dubber.core.audio.separate import separate_tracks
            stereo_wav = self.job_dir / "audio_stereo.wav"
            self._extractor.extract_audio_stereo(input_path, stereo_wav)
            _, instrumental_wav = separate_tracks(
                stereo_wav, self.job_dir / "separated", progress_cb=on_sep
            )
            background_wav = instrumental_wav
            effective_volume = _music_vol if _music_vol > 0 else 0.20

        else:  # only voice, not music
            from dubber.core.audio.separate import separate_tracks
            stereo_wav = self.job_dir / "audio_stereo.wav"
            self._extractor.extract_audio_stereo(input_path, stereo_wav)
            vocals_wav, _ = separate_tracks(
                stereo_wav, self.job_dir / "separated", progress_cb=on_sep
            )
            background_wav = vocals_wav
            effective_volume = _voice_vol if _voice_vol > 0 else 0.20

        output_mp4 = self.job_dir / "output.mp4"
        log.info("=== M5 render start ===")
        self._renderer.render_final(
            video_path=input_path,
            dub_wav=dub_wav,
            ass_path=subs_ass,
            output_path=output_mp4,
            orig_volume=effective_volume,
            vi_volume=vi_volume,
            background_wav=background_wav,
            blur_band=active_blur_band,
            target_video_kbps=video_info.video_bitrate_kbps,
            on_progress=_cbs.get("render"),
            scale=scale,
            fps=fps,
            crf=crf,
            encoder_preset=encoder_preset,
            audio_bitrate=audio_bitrate,
            fonts_dir=fonts_dir(),
        )
        log.info("=== M5 done → %s ===", output_mp4)
        return asr_json, translate_json, dub_wav, subs_ass, output_mp4

    def run_m4(
        self,
        input_path: Path,
        translator: str = "auto",
        voice: str | None = None,
        asr_on_vocals: bool = True,
        on_progress: "dict | None" = None,
        vad: str = "fsmn",
        tts_engine: str = "piper",
    ) -> tuple[Path, Path, Path, Path]:
        """M4: run_m3 → mux dub.wav + video → dubbed.mp4.

        Returns (asr_json, translate_json, dub_wav, dubbed_mp4).
        """
        asr_json, translate_json, dub_wav = self.run_m3(
            input_path, translator=translator, voice=voice,
            asr_on_vocals=asr_on_vocals, on_progress=on_progress, vad=vad,
            tts_engine=tts_engine,
        )

        dubbed_mp4 = self.job_dir / "dubbed.mp4"
        log.info("=== M4 mux start ===")
        self._renderer.mux_dub(input_path, dub_wav, dubbed_mp4)
        log.info("=== M4 done → %s ===", dubbed_mp4)
        return asr_json, translate_json, dub_wav, dubbed_mp4

    def run_m2(
        self,
        input_path: Path,
        translator: str = "auto",
        asr_on_vocals: bool = True,
        on_progress: "dict | None" = None,
        vad: str = "fsmn",
    ) -> tuple[Path, Path]:
        """M2: run_m1 → translate → write translate.json.

        Args:
            translator: "groq", "gemini", or "auto" (prefer Groq if key present).

        Returns (asr_json, translate_json).
        """
        import json
        import os
        from dubber.core.translate.base import make_segments, write_translate_json
        from dubber.core.asr.base import ASRSegment

        asr_json = self.run_m1(input_path, asr_on_vocals=asr_on_vocals, on_progress=on_progress,
                               vad=vad)

        import shutil

        # One cached translation per translator (translations/<translator>.json), so
        # switching groq → local → groq on the same video + trim never re-translates.
        # The job dir itself is only kept for the same source file and trim range.
        translate_json = self.job_dir / "translate.json"
        stored = self.job_dir / "translations" / f"{translator}.json"
        sig = {"asr": file_digest(asr_json), "translator": translator}
        cached = _translate_json_is_valid(stored) and sig_matches(stored, sig)
        if cached and translator != "manual":   # manual: the user decides (may upload a fix)
            log.info("Reusing cached translation (same ASR + translator %s)", translator)
            shutil.copyfile(stored, translate_json)
            write_sig(translate_json, sig)
            return asr_json, translate_json

        raw = json.loads(asr_json.read_text(encoding="utf-8"))
        asr_segments = [ASRSegment(start=r["start"], end=r["end"], text=r["src_text"] if "src_text" in r else r["text"]) for r in raw]

        if translator == "manual":
            results = self._manual_translation(asr_segments, cached)
            if results is None:   # "reuse the previous manual translation"
                shutil.copyfile(stored, translate_json)
                write_sig(translate_json, sig)
                return asr_json, translate_json
        else:
            translate_segs = make_segments(asr_segments)
            backend = _resolve_translator(translator)
            backend.cancel_check = self.cancel_check
            if hasattr(backend, "on_rate_limit"):
                backend.on_rate_limit = self.on_rate_limit
            log.info("=== M2 translate start  %d segments  backend=%s ===",
                     len(asr_segments), backend.__class__.__name__)
            results = backend.translate(translate_segs)

        write_translate_json(asr_segments, results, translate_json)
        write_sig(translate_json, sig)
        stored.parent.mkdir(exist_ok=True)
        shutil.copyfile(translate_json, stored)
        write_sig(stored, sig)
        log.info("=== M2 done → %s ===", translate_json)
        return asr_json, translate_json


    def _manual_translation(self, asr_segments: list, can_reuse: bool):
        """Export the Chinese script and wait for the user's Vietnamese file.

        Returns TranslateResults, or None when the user reuses the stored translation.
        """
        from dubber.core.translate.base import TranslateResult
        from dubber.core.translate.manual import parse_vietnamese, write_script_txt

        if self.manual_translate is None:
            raise RuntimeError("Dịch thủ công chỉ dùng được trong giao diện (GUI)")
        script = write_script_txt(asr_segments, self.job_dir / "script_zh.txt")
        log.info("=== M2 manual: %d câu → %s, chờ file tiếng Việt ===", len(asr_segments), script)
        error = None
        while True:
            answer = self.manual_translate(script, len(asr_segments), can_reuse, error)
            if answer == "reuse" and can_reuse:
                return None
            try:
                lines = parse_vietnamese(Path(answer), len(asr_segments))
            except (OSError, ValueError) as exc:
                error = str(exc)   # ask again, showing what was wrong
                continue
            return [TranslateResult(id=i, vi=t) for i, t in enumerate(lines)]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_fit_json(fitted: list, path: Path) -> None:
    """Persist where each Vietnamese line actually sits in dub.wav (for subtitle timing)."""
    import json
    path.write_text(json.dumps([
        {"id": f.id, "vi_text": f.vi_text, "final_start": round(f.final_start, 3),
         "final_end": round(f.final_end, 3), "speed": round(f.speed, 3), "status": f.status}
        for f in fitted
    ], ensure_ascii=False, indent=1), encoding="utf-8")


def _load_fit_json(path: Path, translate_data: list) -> "list | None":
    """FittingResults from fit.json, or None if absent / not matching translate.json."""
    import json
    from dubber.core.audio.fit import FittingResult
    if not path.exists():
        return None
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    expected = {d["id"]: d["vi_text"] for d in translate_data if d.get("vi_text")}
    if {r["id"]: r["vi_text"] for r in rows} != expected:
        return None  # translation changed since the dub was built
    return [
        FittingResult(id=r["id"], vi_text=r["vi_text"], tts_path=Path("."),
                      tts_duration=r["final_end"] - r["final_start"],
                      final_start=r["final_start"], final_end=r["final_end"],
                      speed=r["speed"], status=r["status"])
        for r in rows
    ]


def _rebuild_fitted(translate_data: list, tts_dir: Path) -> list:
    """Reconstruct minimal FittingResult list for subtitle timing.

    Uses original ASR timestamps (start/end) since most segments fit without
    adjustment. The subtitle display follows the original speech timing.
    """
    import wave
    from dubber.core.audio.fit import FittingResult

    results = []
    for d in translate_data:
        if not d.get("vi_text"):
            continue
        # Find cached TTS file for this segment
        tts_path = None
        for f in tts_dir.glob("*.wav"):
            pass  # best-effort: use start/end times directly
        start = float(d["start"])
        end = float(d["end"])
        results.append(FittingResult(
            id=d["id"],
            vi_text=d["vi_text"],
            tts_path=Path("."),
            tts_duration=end - start,
            final_start=start,
            final_end=end,
            speed=1.0,
            status="fits",
        ))
    return results


def _resolve_translator(backend: str):
    """Return a Translator instance."""
    if backend == "auto":
        from dubber.core.translate.local_translator import AutoTranslator
        return AutoTranslator()
    elif backend == "groq":
        from dubber.core.translate.groq_translator import GroqTranslator
        return GroqTranslator()
    elif backend == "local":
        from dubber.core.translate.local_translator import LocalTranslator
        return LocalTranslator()
    elif backend == "nllb":
        from dubber.core.translate.local_translator import NLLBTranslator
        return NLLBTranslator()
    elif backend == "gemini":
        from dubber.core.translate.gemini import GeminiTranslator
        return GeminiTranslator()
    else:
        raise ValueError(f"Unknown translator backend: {backend!r}.")


def _translate_json_is_valid(path: Path) -> bool:
    """True if translate.json exists and has at least one non-empty vi_text."""
    import json
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return isinstance(data, list) and any(d.get("vi_text") for d in data)
    except Exception:
        return False


def _mix_two_tracks(
    track1: Path,
    track2: Path,
    vol1: float,
    vol2: float,
    ffmpeg: Path,
    job_dir: Path,
) -> Path:
    """Mix two audio files at given volumes into background_mixed.wav."""
    import subprocess
    from dubber.core.utils.ffmpeg_locator import no_window
    out = job_dir / "background_mixed.wav"
    af = (
        f"[0:a]volume={vol1}[a0];[1:a]volume={vol2}[a1];"
        f"[a0][a1]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"
    )
    cmd = [
        str(ffmpeg), "-y",
        "-i", str(track1), "-i", str(track2),
        "-filter_complex", af,
        "-map", "[aout]",
        str(out),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, **no_window())
    if result.returncode != 0:
        raise RuntimeError(f"Audio track mixing failed:\n{result.stderr[-2000:]}")
    return out


def _norm_point_to_px(pt: "tuple | None", w: int, h: int) -> "tuple[int, int] | None":
    """(0–1, 0–1) → pixel point, clamped inside the frame."""
    if not pt:
        return None
    x, y = pt
    return (min(max(0, round(x * w)), w - 1), min(max(0, round(y * h)), h - 1))


def _norm_rect_to_px(rect: "tuple | None", w: int, h: int) -> "tuple[int, int, int, int] | None":
    """(x, y, w, h) in 0–1 → even-aligned pixel rect inside the frame (None if too small).

    Even values keep crop/overlay aligned with YUV 4:2:0 chroma.
    """
    if not rect:
        return None
    rx, ry, rw, rh = rect
    x = min(max(0, round(rx * w)), w - 2) & ~1
    y = min(max(0, round(ry * h)), h - 2) & ~1
    bw = min(round(rw * w), w - x) & ~1
    bh = min(round(rh * h), h - y) & ~1
    if bw < 8 or bh < 8:
        return None
    return (x, y, bw, bh)


def _compute_sub_blurs(
    ass_path: Path,
    video_w: int,
    video_h: int,
    margin_v: int,
    font_size_pt: int,
    pad_h: int = 4,
    pad_top: int = 6,
    pad_bot: int = 8,
    center: "tuple[int, int] | None" = None,
) -> list:
    """Return list of (start_sec, end_sec, x, y, w, h) blur boxes — one per subtitle.

    center: subtitle text center in px (custom position); None → bottom-center.
    """
    import re

    scaled_font = scaled_font_px(font_size_pt, video_h)
    char_w = scaled_font * 0.62      # approximate character advance for Vietnamese
    line_h = scaled_font * 1.40

    def _to_sec(t: str) -> float:
        h, m, rest = t.split(":")
        s, cs = rest.split(".")
        return int(h) * 3600 + int(m) * 60 + int(s) + int(cs) / 100

    boxes = []
    for line in ass_path.read_text(encoding="utf-8-sig").splitlines():
        if not line.startswith("Dialogue:"):
            continue
        parts = line.split(",", 9)
        if len(parts) < 10:
            continue
        try:
            start_t = _to_sec(parts[1].strip())
            end_t   = _to_sec(parts[2].strip())
        except ValueError:
            continue
        text = parts[9]

        # Count lines and estimate longest visible line length (strip ASS override tags)
        clean = re.sub(r'\{[^}]*\}', '', text)
        lines = clean.replace(r'\N', '\n').replace(r'\n', '\n').split('\n')
        num_lines = max(1, len(lines))
        longest   = max((len(l) for l in lines), default=1)

        box_w = min(video_w, int(longest * char_w) + 2 * pad_h)
        box_h = int(num_lines * line_h) + pad_top + pad_bot
        if center:
            box_x = min(max(0, center[0] - box_w // 2), video_w - box_w)
            box_y = min(max(0, center[1] - box_h // 2), max(0, video_h - box_h))
        else:
            box_x = max(0, (video_w - box_w) // 2)
            box_y = max(0, video_h - margin_v + pad_bot - box_h)

        boxes.append((start_t, end_t, box_x, box_y, box_w, box_h))

    return boxes


def _parse_sub_windows(ass_path: Path) -> list:
    """Parse subs.ass and return list of (start_sec, end_sec) for each Dialogue line."""
    import re
    windows = []
    time_re = re.compile(r"^Dialogue:.*?(\d+:\d{2}:\d{2}\.\d{2}),(\d+:\d{2}:\d{2}\.\d{2}),")

    def _to_sec(t: str) -> float:
        h, m, rest = t.split(":")
        s, cs = rest.split(".")
        return int(h) * 3600 + int(m) * 60 + int(s) + int(cs) / 100

    try:
        for line in ass_path.read_text(encoding="utf-8-sig").splitlines():
            m = time_re.match(line)
            if m:
                windows.append((_to_sec(m.group(1)), _to_sec(m.group(2))))
    except Exception:
        pass
    return windows


def _log_timing_stats(fitted: list) -> None:
    from collections import Counter
    counts = Counter(f.status for f in fitted)
    log.info(
        "Timing stats — fits:%d overflow:%d sped_up:%d shortened:%d pushed:%d",
        counts.get("fits", 0), counts.get("overflow", 0),
        counts.get("sped_up", 0), counts.get("shortened", 0),
        counts.get("pushed", 0),
    )
    header = f"{'ID':>3}  {'Status':<10}  {'TTS dur':>8}  {'Slot dur':>8}  {'Speed':>6}"
    log.info(header)
    for f in fitted:
        log.info(
            "%3d  %-10s  %8.2fs  %8.2fs  %6.2fx",
            f.id, f.status, f.tts_duration,
            f.final_end - f.final_start, f.speed,
        )
