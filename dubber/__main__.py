"""CLI entry point: python -m dubber input.mp4  (or just: dubber input.mp4)"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path


def _setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main(argv: list[str] | None = None) -> int:
    # Frozen Windows build: a child process spawned by multiprocessing (torch / librosa
    # workers) re-runs Dubber.exe — this makes it run the worker instead of a second GUI.
    import multiprocessing
    multiprocessing.freeze_support()
    # Windowed Windows build has no console: sys.stdout/stderr are None and any library
    # progress bar (tqdm in audio-separator, HF downloads) would crash on .write()
    import os
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

    parser = argparse.ArgumentParser(
        prog="dubber",
        description="Dub a Chinese video to Vietnamese",
    )
    parser.add_argument("input", type=Path, nargs="?", help="Input video file (e.g. input.mp4)")
    parser.add_argument("--gui", action="store_true", help="Launch GUI (default when no input given)")
    parser.add_argument(
        "--translator",
        choices=["groq", "gemini"],
        default="groq",
        help="Translation backend (default: groq)",
    )
    parser.add_argument(
        "--vad",
        choices=["fsmn", "silero"],
        default="fsmn",
        help="Voice activity detector for ASR (default: fsmn, Silero as fallback)",
    )
    parser.add_argument(
        "--tts",
        choices=["piper", "vieneu"],
        default="piper",
        help="Vietnamese reader (default: piper)",
    )
    parser.add_argument(
        "--voice",
        default=None,
        help='piper: model stem; vieneu: "preset:<name>" or "clone:<id>" (default voice if omitted)',
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    args = parser.parse_args(argv)

    # Launch GUI when no input file provided (or --gui flag)
    if args.gui or args.input is None:
        from dubber.app.gui import run_gui
        run_gui()
        return 0

    _setup_logging(args.verbose)
    from dubber.core.utils.api_keys import apply_saved_keys
    apply_saved_keys()   # Groq key saved from the GUI (OS credential store)

    input_path = args.input.resolve()  # type: ignore[union-attr]
    if not input_path.exists():
        print(f"[ERROR] Input file not found: {input_path}", file=sys.stderr)
        return 1

    from dubber.core.utils.ffmpeg_locator import find_ffmpeg, find_ffprobe
    try:
        ffmpeg = find_ffmpeg()
        ffprobe = find_ffprobe()
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    from dubber.core.utils.paths import prepare_job_dir, prune_jobs
    job_dir = prepare_job_dir(input_path)   # reused only while the source file is unchanged
    prune_jobs()   # old / oversized caches of other videos (this one is the newest: kept)

    from dubber.core.pipeline import Pipeline
    pipeline = Pipeline(ffmpeg=ffmpeg, ffprobe=ffprobe, job_dir=job_dir)

    run_kw = {"vad": args.vad, "tts_engine": args.tts, "voice": args.voice}
    try:
        from rich.progress import (
            Progress, SpinnerColumn, BarColumn,
            TextColumn, TimeElapsedColumn, MofNCompleteColumn,
        )
        _run_with_rich(pipeline, input_path, args.translator, args.verbose, run_kw)
    except ImportError:
        _run_plain(pipeline, input_path, args.translator, run_kw)

    return 0


# ── Rich progress display ─────────────────────────────────────────────────────

_STEPS = [
    ("ASR",       "ASR  (speech → text)"),
    ("Translate", "Translate (Groq)    "),
    ("TTS",       "TTS  (text → speech)"),
    ("Mux",       "Mux  (audio mix)    "),
    ("Render",    "Render (encode)     "),
]


def _run_with_rich(pipeline, input_path: Path, translator: str, verbose: bool,
                   run_kw: dict) -> None:
    from rich.progress import (
        Progress, SpinnerColumn, BarColumn,
        TextColumn, TimeElapsedColumn, TaskProgressColumn,
    )
    import io
    from rich.console import Console

    # Force UTF-8 output — Windows console defaults to cp1252
    utf8_stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    console = Console(file=utf8_stdout, highlight=False)
    console.print(f"\ndubber -- {input_path.name}\n")

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description:<28}"),
        BarColumn(bar_width=30),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    ) as progress:

        # One overall task per pipeline step
        tasks = {
            name: progress.add_task(label, total=100, start=False)
            for name, label in _STEPS
        }

        def start(name: str) -> None:
            progress.start_task(tasks[name])
            progress.update(tasks[name], completed=0)

        def done(name: str) -> None:
            progress.update(tasks[name], completed=100)

        _render_started = [False]
        def on_render_pct(pct: float) -> None:
            if not _render_started[0]:
                _render_started[0] = True
                progress.start_task(tasks["Render"])
            progress.update(tasks["Render"], completed=int(pct * 100))

        # ── monkey-patch pipeline steps to emit progress ──────────────────
        orig_m1 = pipeline.run_m1
        orig_m2 = pipeline.run_m2
        orig_m3 = pipeline.run_m3
        orig_m4 = pipeline.run_m4

        def _m1(path, **kw):
            start("ASR")
            result = orig_m1(path, **kw)
            done("ASR")
            return result

        def _m2(path, **kw):
            start("Translate")
            result = orig_m2(path, **kw)
            done("Translate")
            return result

        def _m3(path, **kw):
            # intercept TTS segment progress
            orig_engine_factory = None
            result_holder = [None]

            start("TTS")
            result = orig_m3(path, **kw)
            done("TTS")
            return result

        def _m4(path, **kw):
            start("Mux")
            result = orig_m4(path, **kw)
            done("Mux")
            return result

        pipeline.run_m1 = _m1
        pipeline.run_m2 = _m2
        pipeline.run_m3 = _m3
        pipeline.run_m4 = _m4

        try:
            asr_json, translate_json, dub_wav, subs_ass, output_mp4 = pipeline.run_m5(
                input_path,
                translator=translator,
                **run_kw,
                on_progress={"render": on_render_pct},
            )
        except Exception as exc:
            console.print(f"\n[bold red]Pipeline thất bại:[/] {exc}")
            if verbose:
                raise
            sys.exit(1)

    size_mb = output_mp4.stat().st_size / 1024 / 1024
    console.print(f"\nDone!  Output: {output_mp4}  ({size_mb:.1f} MB)\n")

    print(str(asr_json))
    print(str(translate_json))
    print(str(dub_wav))
    print(str(subs_ass))
    print(str(output_mp4))


# ── Plain fallback (no rich) ──────────────────────────────────────────────────

def _run_plain(pipeline, input_path: Path, translator: str, run_kw: dict) -> None:
    STEPS = ["ASR", "Translate", "TTS", "Mux", "Render"]
    t0 = time.time()

    def tick(step: str, pct: int = 100) -> None:
        elapsed = time.time() - t0
        bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
        print(f"\r[{elapsed:5.1f}s] {step:<12} [{bar}] {pct:3d}%", end="", flush=True)
        if pct == 100:
            print()

    def on_render(pct: float) -> None:
        tick("Render", int(pct * 100))

    orig_m1, orig_m2, orig_m3, orig_m4 = (
        pipeline.run_m1, pipeline.run_m2, pipeline.run_m3, pipeline.run_m4
    )

    def _m1(p, **kw): tick("ASR", 0); r = orig_m1(p, **kw); tick("ASR"); return r
    def _m2(p, **kw): tick("Translate", 0); r = orig_m2(p, **kw); tick("Translate"); return r
    def _m3(p, **kw): tick("TTS", 0); r = orig_m3(p, **kw); tick("TTS"); return r
    def _m4(p, **kw): tick("Mux", 0); r = orig_m4(p, **kw); tick("Mux"); return r

    pipeline.run_m1 = _m1
    pipeline.run_m2 = _m2
    pipeline.run_m3 = _m3
    pipeline.run_m4 = _m4

    try:
        asr_json, translate_json, dub_wav, subs_ass, output_mp4 = pipeline.run_m5(
            input_path, translator=translator, on_progress={"render": on_render}, **run_kw
        )
    except Exception as exc:
        print(f"\n[ERROR] Pipeline thất bại: {exc}", file=sys.stderr)
        sys.exit(1)

    elapsed = time.time() - t0
    size_mb = output_mp4.stat().st_size / 1024 / 1024
    print(f"\nXong! {elapsed:.0f}s  →  {output_mp4}  ({size_mb:.1f} MB)")

    print(str(asr_json))
    print(str(translate_json))
    print(str(dub_wav))
    print(str(subs_ass))
    print(str(output_mp4))


if __name__ == "__main__":
    sys.exit(main())
