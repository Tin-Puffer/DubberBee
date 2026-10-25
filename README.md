# Dubber Bee

<img src="dubber/assets/logo/logo_256.png" alt="Dubber Bee logo" width="96" align="right">

Desktop app (PySide6) + CLI for dubbing Chinese videos to Vietnamese (Python package `dubber`):
ASR (SenseVoice + FSMN-VAD) → translation (Groq / MarianMT / NLLB 1.3B, optional review with
copy / paste to an AI chatbot) → TTS (Piper or VieNeu-TTS with voice cloning) → FFmpeg render,
plus a ready-to-paste TikTok prompt (`<video>_vi.txt`) next to every exported video.

- **Hướng dẫn sử dụng (người dùng):** [HUONG_DAN_SU_DUNG.md](HUONG_DAN_SU_DUNG.md)
- **Tài liệu kỹ thuật:** [TECHNICAL.md](TECHNICAL.md)

## Requirements

- Python 3.12
- FFmpeg **with libass** (subtitle burn-in): macOS `brew install ffmpeg-full`,
  Windows Gyan full build

## Install

Clone, then one command (deps → `.venv` → packages → models → tests). `--all` / `-All`
downloads every model (16 Piper voices, VieNeu-TTS, separation; ~2 GB) from the
[`models-v1` release](https://github.com/Tin-Puffer/DubberBee-releases/releases/tag/models-v1)
of the public releases repo (built apps: [DubberBee-releases](https://github.com/Tin-Puffer/DubberBee-releases/releases/latest)):

```bash
git clone https://github.com/Tin-Puffer/DubberBee.git && cd DubberBee
bash scripts/setup_mac.sh --all                                   # macOS (needs Homebrew)
powershell -ExecutionPolicy Bypass -File setup_dev.ps1 -All       # Windows
source .venv/bin/activate && python -m dubber                     # run (GUI)
bash scripts/build_exe_mac.sh                                     # build Dubber.app (shown as "Dubber Bee")
```

By hand:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"                       # GUI, ASR (+FSMN-VAD), Piper TTS, Groq, subtitles (Pillow, pyvi)
pip install -e ".[dev,offline,vieneu]"        # + offline translation, music separation, VieNeu-TTS
python spike/download_models.py               # add --vieneu to fetch VieNeu now (else on first use)
```

Core packages include `Pillow` (measures subtitle text with the real font, so every subtitle
fits on one line and the automatic blur box hugs it) and `pyvi` (Vietnamese word segmentation,
so a subtitle line is never cut inside a word); both are bundled by `dubber.spec`. An existing
venv picks them up with `pip install -e ".[dev,offline,vieneu]"` again.

Extras: `separation` (audio-separator / MDX-Net: cleaner background mix and ASR on
the voice only), `offline` (torch + transformers for MarianMT / NLLB 1.3B, includes
`separation`), `vieneu` (VieNeu-TTS v3 Turbo; pulls gradio + librosa as package deps).

## Models

`python spike/download_models.py` saves to `spike/models/` (the app also looks in
the bundle and in the user data dir):

| Model | Size | Used for |
|---|---|---|
| `sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17` | ~229 MB | ASR |
| `fsmn-vad/` (ModelScope `iic/speech_fsmn_vad_zh-cn-16k-common-onnx`) | ~0.5 MB | VAD (default) |
| `silero_vad.onnx` | ~0.6 MB | VAD (fallback) |
| `piper/vi_VN-thanh_nien_tu_tin-medium.onnx` (+15 with `--all-voices`) | ~60 MB each | Piper TTS |
| `vieneu/` (`--vieneu`, or downloaded on first use to the user data dir) | ~610 MB | VieNeu-TTS |
| HuggingFace `facebook/nllb-200-distilled-1.3B` (first use) | ~5.5 GB | Offline translation |

Cloned VieNeu voices are stored in the user data dir (`voices/vieneu/`), outside the
app, so they survive updates.

## Run

```bash
python -m dubber                         # GUI (default)
python -m dubber input.mp4               # CLI: Groq + Piper default voice, full render
python -m dubber input.mp4 --tts vieneu --voice "preset:<name>"
python -m dubber --help                  # --translator, --vad, --tts, --voice, -v
```

Groq API key: in the GUI, **⚙ Cài đặt → Groq API key → 🔑 Nhập API key…** (stored in the OS
keychain, never in a file; **Kiểm tra** sends one 1-token request). The CLI / translator only
read `os.environ`, so for the CLI without a saved key:

```bash
export GROQ_API_KEY="gsk_..."            # macOS / Linux
$env:GROQ_API_KEY = "gsk_..."            # Windows PowerShell
```

The CLI renders into the video's cache dir and prints five paths — `asr.json`,
`translate.json`, `dub.wav`, `subs.ass`, `output.mp4` — e.g.:

```
C:\Users\<name>\AppData\Local\dubber\dubber\jobs\48c476fb7588\asr.json
```

**asr.json** — timed Chinese speech segments:

```json
[{"start": 0.166, "end": 2.672, "text": "大家好，欢迎来到今天的节目。"}, ...]
```

**translate.json** — each segment with Vietnamese translation:

```json
[
  {"id": 0, "start": 0.166, "end": 2.672,
   "src_text": "大家好，欢迎来到今天的节目。",
   "vi_text": "Xin chào mọi người, chào mừng đến với chương trình hôm nay."},
  ...
]
```

**dub.wav** — tất cả đoạn TTS ghép lên silent track theo timestamp đã căn chỉnh (24 kHz mono PCM).

Add `-v` for debug logging:

```bash
dubber input.mp4 -v
```

## Run tests

```bash
python -m pytest
```

## Project structure

```
dubber/
  __main__.py          CLI entry point (--voice flag)
  core/
    pipeline.py        Milestone pipeline orchestrator (run_m0..run_m3)
    audio/
      extract.py       FFmpeg audio extraction (16 kHz mono WAV)
      fit.py           TimingFitter 5-step algorithm
      mixer.py         dub.wav assembler (silent track + TTS paste)
    video/render.py    FFmpeg video pass-through
    asr/
      base.py          ASREngine abstract interface
      sensevoice_sherpa.py  SenseVoice via sherpa-onnx + Silero VAD
      postprocess.py   Segment post-processing (merge/split/drop)
      model_manager.py Model path resolver
    tts/
      base.py          TTSEngine ABC, TTSResult, tts_cache_key
      edge.py          EdgeTTSEngine (async semaphore, retry, cache, MP3→WAV)
    translate/
      base.py          Translator ABC, make_segments, CHARS_PER_SEC=14
      gemini.py        Gemini translator (batch, context, fallback)
    utils/
      ffmpeg_locator.py  Locate ffmpeg/ffprobe
      paths.py           Job directory (SHA1-keyed user data dir)
spike/
  m1_spike.py          Runtime comparison (sherpa-onnx vs funasr-gguf)
  download_models.py   Model downloader
  models/              Downloaded models (not committed)
tests/                 Unit tests (98 total)
```

## Milestones

| Milestone | Status | Description |
|-----------|--------|-------------|
| M0 | Done | Extract audio, pass-through video |
| M1 | Done | Chinese ASR → `asr.json` |
| M2 | Done | Translate Chinese → Vietnamese → `translate.json` |
| M3 | Done | TTS (edge-tts), TimingFitter 5-step, dub.wav |
| M4–M8 | Done | Music separation, render, GUI, export options, packaging |
| M9 | Done | Token-time sentence split, FSMN-VAD, ASR on vocals, NLLB 1.3B, VieNeu-TTS + voice library, option notes |
