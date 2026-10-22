"""M1 spike: so sánh sherpa-onnx vs FunASR llama.cpp trên example.mp4."""
from __future__ import annotations

import sys
import time
import logging
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dubber.core.utils.ffmpeg_locator import find_ffmpeg, find_ffprobe
from dubber.core.audio.extract import AudioExtractor
from dubber.core.asr.sensevoice_sherpa import SherpaOnnxASR
from dubber.core.asr.sensevoice_gguf import FunASRGGUFEngine
from spike.cer import cer
from spike.download_models import (
    SHERPA_MODEL_DIR, GGUF_DIR, MODELS_DIR,
    find_funasr_exe,
    download_sherpa_model,
    download_funasr_binary,
    download_gguf_models,
)

logging.basicConfig(level=logging.WARNING,
                    format="%(asctime)s [%(levelname)s] %(message)s")

# ─── Ground truth (đoạn văn gốc đã dùng để sinh TTS) ───────────────────────
GROUND_TRUTH = (
    "大家好欢迎来到今天的节目"
    "今天我们要讨论一个非常有趣的话题关于人工智能和未来科技的发展"
    "随着技术的不断进步我们的生活也在发生着巨大的变化"
    "智能手机自动驾驶汽车还有各种各样的智能设备已经成为了我们日常生活的一部分"
    "在未来人工智能将会在医疗教育交通等各个领域发挥更加重要的作用"
    "我们应该积极拥抱这些变化同时也要注意保护我们的隐私和安全"
    "谢谢大家的收听我们下次再见"
)


def extract_audio(video: Path) -> Path:
    wav = ROOT / "spike" / "models" / "example_16k.wav"
    if wav.exists():
        print(f"Audio already extracted: {wav.name}")
        return wav
    ffmpeg = find_ffmpeg()
    ffprobe = find_ffprobe()
    ex = AudioExtractor(ffmpeg=ffmpeg, ffprobe=ffprobe)
    return ex.extract_audio(video, wav)


def run_engine(engine, audio_path: Path, label: str) -> dict:
    print(f"\n{'─'*50}")
    print(f"Running: {label}")
    t0 = time.perf_counter()
    try:
        segments = engine.transcribe(audio_path)
        elapsed = time.perf_counter() - t0
        full_text = "".join(s.text for s in segments)
        error = cer(GROUND_TRUTH, full_text)
        audio_dur = segments[-1].end if segments and segments[-1].end > 0 else 40.0
        rtf = elapsed / audio_dur if audio_dur > 0 else 0
        return {
            "label": label,
            "ok": True,
            "elapsed_s": round(elapsed, 2),
            "rtf": round(rtf, 3),
            "cer_pct": round(error * 100, 1),
            "n_segments": len(segments),
            "text_preview": full_text[:120],
            "full_text": full_text,
        }
    except Exception as e:
        elapsed = time.perf_counter() - t0
        return {
            "label": label,
            "ok": False,
            "elapsed_s": round(elapsed, 2),
            "error": str(e),
        }


def print_table(results: list[dict], audio_dur: float):
    print("\n" + "═" * 70)
    print("M1 SPIKE — BẢNG SO SÁNH HAI RUNTIME SENSEVOICE")
    print(f"Audio: example.mp4  |  Thời lượng: {audio_dur:.1f}s")
    print("═" * 70)
    fmt = "{:<22} {:>8} {:>7} {:>8} {:>10}"
    print(fmt.format("Runtime", "Thời gian", "RTF", "CER (%)", "Đoạn"))
    print("─" * 70)
    for r in results:
        if r["ok"]:
            print(fmt.format(
                r["label"],
                f"{r['elapsed_s']}s",
                r["rtf"],
                f"{r['cer_pct']}%",
                str(r["n_segments"]),
            ))
        else:
            print(fmt.format(r["label"], "FAILED", "-", "-", "-"))
            print(f"  Lỗi: {r['error'][:100]}")
    print("─" * 70)
    print("RTF < 1.0 = nhanh hơn thời gian thực  |  CER thấp = chính xác hơn")

    print("\n── Transcript preview ──")
    for r in results:
        if r["ok"]:
            print(f"\n[{r['label']}]")
            print(r["text_preview"])

    print("\n── Ground truth (đoạn văn gốc, không dấu câu) ──")
    print(GROUND_TRUTH[:120])


def main():
    video = ROOT / "example.mp4"
    if not video.exists():
        print(f"ERROR: {video} not found")
        sys.exit(1)

    # ── Đảm bảo model đã tải ──────────────────────────────────────────────
    print("=== Kiểm tra / tải model ===")
    sherpa_dir = download_sherpa_model()

    funasr_exe = find_funasr_exe()
    if funasr_exe is None:
        download_funasr_binary()
        funasr_exe = find_funasr_exe()

    sv_gguf = GGUF_DIR / "sensevoice-small-q8.gguf"
    vad_gguf = GGUF_DIR / "fsmn-vad.gguf"
    if not sv_gguf.exists() or not vad_gguf.exists():
        sv_gguf, vad_gguf = download_gguf_models()

    # ── Trích audio ───────────────────────────────────────────────────────
    print("\n=== Trích audio 16 kHz ===")
    audio = extract_audio(video)
    import wave
    with wave.open(str(audio)) as wf:
        audio_dur = wf.getnframes() / wf.getframerate()
    print(f"Audio duration: {audio_dur:.1f}s")

    # ── Chạy hai engine ───────────────────────────────────────────────────
    results = []

    # Runtime A: sherpa-onnx
    print("\n=== Cài / kiểm tra sherpa-onnx ===")
    try:
        import sherpa_onnx
        print(f"sherpa-onnx version: {sherpa_onnx.__version__}")
    except ImportError:
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install",
                        "sherpa-onnx", "-q"], check=True)

    vad_onnx = MODELS_DIR / "silero_vad.onnx"
    engine_a = SherpaOnnxASR(
        model_dir=sherpa_dir,
        vad_model=vad_onnx if vad_onnx.exists() else None,
        language="zh",
        use_itn=True,
    )
    results.append(run_engine(engine_a, audio, "sherpa-onnx"))

    # Runtime B: FunASR llama.cpp
    if funasr_exe and sv_gguf.exists() and vad_gguf.exists():
        engine_b = FunASRGGUFEngine(
            binary=funasr_exe,
            model_gguf=sv_gguf,
            vad_gguf=vad_gguf,
        )
        results.append(run_engine(engine_b, audio, "funasr-gguf"))
    else:
        results.append({
            "label": "funasr-gguf",
            "ok": False,
            "elapsed_s": 0,
            "error": f"Binary or model not found. exe={funasr_exe}",
        })

    # ── In bảng kết quả ───────────────────────────────────────────────────
    print_table(results, audio_dur)


if __name__ == "__main__":
    main()
