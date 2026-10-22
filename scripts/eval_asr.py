"""Evaluate ASR sentence splitting on a video/wav — no GUI.

    python scripts/eval_asr.py VIDEO [--vad silero|fsmn] [--split legacy|timestamps]
                                     [--vocals on|off] [--out eval/<label>]

Writes <out>/<stem>/asr.json + <stem>.srt (open next to the video to eyeball the cuts)
and prints: sentence count, mean/min/max length, sentences not ending in 。！？,
sentences > 8 s, and the ASR real-time factor.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dubber.core.utils.ffmpeg_locator import find_ffmpeg, find_ffprobe, no_window  # noqa: E402

SENTENCE_END = tuple("。！？?!…")
LONG_S = 8.0


def _to_16k_mono(src: Path, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [str(find_ffmpeg()), "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", str(dst)],
        capture_output=True, check=True, **no_window(),
    )
    return dst


def _wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as wf:
        return wf.getnframes() / wf.getframerate()


def _asr_input(src: Path, work: Path, vocals: bool) -> tuple[Path, str]:
    """Return (16 kHz mono wav for ASR, description of what it is)."""
    audio = _to_16k_mono(src, work / "audio.wav")
    if not vocals:
        return audio, "original"
    from dubber.core.audio.extract import AudioExtractor
    from dubber.core.audio.separate import separate_tracks
    stereo = AudioExtractor(find_ffmpeg(), find_ffprobe()).extract_audio_stereo(
        src, work / "audio_stereo.wav")
    vocals_wav, _ = separate_tracks(stereo, work / "separated")
    if (work / "separated" / ".ffmpeg_fallback").exists():
        return audio, "original (MDX-Net unavailable)"
    return _to_16k_mono(vocals_wav, work / "audio_vocals_16k.wav"), "vocals (MDX-Net)"


def _make_engine(vad: str, split: str):
    from dubber.core.asr.model_manager import (
        find_fsmn_vad_dir, find_sherpa_model_dir, find_silero_vad,
    )
    from dubber.core.asr.sensevoice_sherpa import SherpaOnnxASR
    fsmn_dir = find_fsmn_vad_dir()
    if vad == "fsmn" and fsmn_dir is None:
        sys.exit("FSMN-VAD model not found — run: python spike/download_models.py")
    return SherpaOnnxASR(model_dir=find_sherpa_model_dir(), vad_model=find_silero_vad(),
                         split_mode=split, vad=vad, fsmn_dir=fsmn_dir)


def _srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _write_srt(segments, path: Path) -> None:
    lines = []
    for i, s in enumerate(segments, 1):
        lines += [str(i), f"{_srt_time(s.start)} --> {_srt_time(s.end)}", s.text, ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def stats(segments, audio_s: float, asr_s: float) -> dict:
    durs = [s.end - s.start for s in segments] or [0.0]
    return {
        "sentences": len(segments),
        "mean_s": round(sum(durs) / len(durs), 2),
        "min_s": round(min(durs), 2),
        "max_s": round(max(durs), 2),
        "no_end_punct": sum(not s.text.rstrip().endswith(SENTENCE_END) for s in segments),
        "over_8s": sum(d > LONG_S for d in durs),
        "rtf": round(asr_s / audio_s, 3) if audio_s else None,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("inputs", type=Path, nargs="+", help="video or audio files")
    p.add_argument("--vad", choices=["silero", "fsmn"], default="silero")
    p.add_argument("--split", choices=["legacy", "timestamps"], default="legacy")
    p.add_argument("--vocals", choices=["on", "off"], default="off")
    p.add_argument("--out", type=Path, default=None, help="default: eval/<vad>-<split>-vocals<on|off>")
    args = p.parse_args(argv)

    from dubber.core.asr.postprocess import write_asr_json

    label = f"{args.vad}-{args.split}-vocals{args.vocals}"
    out_root = args.out or Path("eval") / label
    report = {}
    for src in args.inputs:
        engine = _make_engine(args.vad, args.split)   # fresh per file: VAD state carries over
        work = out_root / src.stem
        wav, source = _asr_input(src, work, args.vocals == "on")
        audio_s = _wav_duration(wav)
        t0 = time.perf_counter()
        segments = engine.transcribe(wav)
        asr_s = time.perf_counter() - t0
        write_asr_json(segments, work / "asr.json")
        _write_srt(segments, work / f"{src.stem}.srt")
        st = stats(segments, audio_s, asr_s)
        st["asr_input"] = source
        report[src.name] = st
        print(f"[{label}] {src.name}  ({audio_s:.1f}s, {source})")
        print("   " + "  ".join(f"{k}={v}" for k, v in st.items() if k != "asr_input"))
    (out_root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
