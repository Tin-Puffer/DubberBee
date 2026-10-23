#!/usr/bin/env bash
# ============================================================================
#  Build Dubber.app with PyInstaller (macOS, Apple Silicon or Intel)
#  Run from anywhere:  bash scripts/build_exe_mac.sh
#
#  Prereqs: scripts/setup_mac.sh has been run (venv + deps + models + ffmpeg-full)
#  Output:  dist/Dubber.app   (and dist/Dubber-macos.zip)
#
#  FFmpeg: Homebrew ffmpeg-full (has libass for subtitle burn-in) + its dylibs,
#  made self-contained with dylibbundler → the .app runs without Homebrew.
# ============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"

# Capture first: `ffmpeg | grep -q` + pipefail fails on SIGPIPE when grep exits early
has_ass() { [[ "$("$1" -hide_banner -filters 2>/dev/null)" == *" ass "* ]]; }

echo "=== Dubber macOS build ==="
echo "Project root: $ROOT"

[[ -x "$PY" ]] || { echo "✗ .venv not found — run: bash scripts/setup_mac.sh"; exit 1; }

# ── 1. PyInstaller + any dependency added since setup (e.g. Pillow, pyvi) ─────
echo "[1/5] Installing PyInstaller + missing dependencies..."
"$PY" -m pip install --quiet "pyinstaller>=6.0"
"$PY" -m pip install --quiet -e ".[offline,vieneu]"
"$PY" -c "import PIL" || { echo "✗ Pillow missing — subtitles need it"; exit 1; }

# ── 2. resources/: FFmpeg ─────────────────────────────────────────────────────
echo "[2/5] Preparing resources/..."
mkdir -p resources/models/piper

FFDIR=""
for d in /opt/homebrew/opt/ffmpeg-full/bin /usr/local/opt/ffmpeg-full/bin; do
    [[ -x "$d/ffmpeg" ]] && { FFDIR="$d"; break; }
done
if [[ -z "$FFDIR" ]]; then
    echo "✗ ffmpeg-full not found — run: brew install ffmpeg-full"
    exit 1
fi
if ! has_ass "$FFDIR/ffmpeg"; then
    echo "✗ $FFDIR/ffmpeg has no 'ass' filter (libass) — subtitles would fail"
    exit 1
fi
# Self-contained copy: binaries + every dylib they need, paths rewritten to
# @executable_path/../lib. Shipped as plain data — letting PyInstaller merge these
# dylibs into Frameworks/ mixes them with plain-ffmpeg dylibs of the same name.
command -v dylibbundler >/dev/null || brew install dylibbundler
rm -rf resources/ffmpeg-mac resources/ffmpeg resources/ffprobe
mkdir -p resources/ffmpeg-mac/bin
cp -f "$FFDIR/ffmpeg" "$FFDIR/ffprobe" resources/ffmpeg-mac/bin/
chmod u+w resources/ffmpeg-mac/bin/*
dylibbundler -od -b -cd -x resources/ffmpeg-mac/bin/ffmpeg -x resources/ffmpeg-mac/bin/ffprobe \
    -d resources/ffmpeg-mac/lib -p @executable_path/../lib/ -s "$FFDIR/../lib" >/dev/null
for f in resources/ffmpeg-mac/bin/* resources/ffmpeg-mac/lib/*; do codesign --force -s - "$f" 2>/dev/null; done
has_ass resources/ffmpeg-mac/bin/ffmpeg || { echo "✗ bundled ffmpeg does not run"; exit 1; }
echo "      FFmpeg: $FFDIR"

# ── 3. resources/models: ASR, VAD (FSMN + Silero), piper voices ──────────────
SHERPA=sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17
if [[ ! -f "spike/models/$SHERPA/tokens.txt" ]]; then
    echo "      Models missing → downloading"
    "$PY" spike/download_models.py
fi
# Only what the app loads: model.int8.onnx + tokens.txt (skip test_wavs, fp32 model, etc.)
mkdir -p "resources/models/$SHERPA"
cp -f "spike/models/$SHERPA/model.int8.onnx" "spike/models/$SHERPA/tokens.txt" "resources/models/$SHERPA/"
cp -f spike/models/silero_vad.onnx resources/models/
[[ -f spike/models/fsmn-vad/config.yaml ]] || "$PY" spike/download_models.py
mkdir -p resources/models/fsmn-vad
cp -f spike/models/fsmn-vad/model_quant.onnx spike/models/fsmn-vad/config.yaml \
      spike/models/fsmn-vad/am.mvn resources/models/fsmn-vad/
cp -f spike/models/piper/*.onnx spike/models/piper/*.onnx.json resources/models/piper/
echo "      Voices: $(ls resources/models/piper/*.onnx | wc -l | tr -d ' ')"

# VieNeu-TTS models (~610 MB): user-data / spike copy, downloaded if missing
VIENEU_DIR="$("$PY" -c 'from dubber.core.asr.model_manager import find_vieneu_model_dir as f, download_vieneu_models as d; print(f() or d())')"
rm -rf resources/models/vieneu
cp -R "$VIENEU_DIR" resources/models/vieneu
rm -rf resources/models/vieneu/*/.cache
echo "      VieNeu: $(du -sh resources/models/vieneu | cut -f1)"

# UVR MDX-Net model + audio-separator's model lists, so separation works offline
UVR_DIR="$("$PY" scripts/fetch_uvr_model.py)"
rm -rf resources/models/uvr && mkdir -p resources/models/uvr
cp -f "$UVR_DIR"/*.onnx "$UVR_DIR"/*.json resources/models/uvr/
echo "      UVR: $(ls resources/models/uvr | tr '\n' ' ')"
echo "      resources/ total: $(du -sh resources | cut -f1)"

# ── 4. PyInstaller ────────────────────────────────────────────────────────────
echo "[3/5] Running PyInstaller (a few minutes)..."
"$PY" -m PyInstaller dubber.spec --noconfirm

APP="dist/Dubber.app"
[[ -d "$APP" ]] || { echo "✗ Build failed — $APP not found"; exit 1; }

# ── 5. Verify bundled FFmpeg runs standalone + ad-hoc sign ───────────────────
echo "[4/5] Verifying bundle..."
BUNDLED_FF="$(find "$APP/Contents" -path "*ffmpeg-mac/bin/ffmpeg" | head -1)"
if [[ -z "$BUNDLED_FF" ]] || ! has_ass "$BUNDLED_FF"; then
    echo "✗ Bundled ffmpeg missing or cannot run"
    exit 1
fi
if [[ "$(otool -L "$BUNDLED_FF")" =~ /opt/homebrew|/usr/local/opt|/usr/local/Cellar ]]; then
    echo "  ⚠ bundled ffmpeg still links Homebrew dylibs — it will only run where Homebrew ffmpeg-full is installed"
fi
# pyvi's word-segmentation model (subtitle line cutting) — Apple Silicon only, see pyproject
if "$PY" -c "import pyvi" 2>/dev/null; then
    if [[ -z "$(find "$APP/Contents" -path "*pyvi/models/pyvi3.pkl" | head -1)" ]]; then
        echo "✗ pyvi model missing in bundle"
        exit 1
    fi
    echo "      pyvi model: bundled"
else
    echo "      pyvi: not installed (Intel Mac) — subtitles use the built-in word list"
fi
codesign --force --deep --sign - "$APP" 2>/dev/null || echo "  ⚠ ad-hoc codesign failed (app still runs locally)"

echo "[5/5] Zipping..."
rm -f dist/Dubber-macos.zip
ditto -c -k --keepParent "$APP" dist/Dubber-macos.zip

echo ""
echo "=== Build complete ==="
echo "  App:   $APP  ($(du -sh "$APP" | cut -f1))"
echo "  Zip:   dist/Dubber-macos.zip  ($(du -sh dist/Dubber-macos.zip | cut -f1))"
echo "  Run:   open $APP"
echo "  First launch on another Mac: right-click → Open (app is not notarized)"
