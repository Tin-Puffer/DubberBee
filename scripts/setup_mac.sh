#!/usr/bin/env bash
# ============================================================================
#  Set up a fresh Mac for Dubber development:
#    Homebrew deps → .venv → Python deps → models → tests
#  Run:  bash scripts/setup_mac.sh            (add --all for every model: 16 voices +
#                                              VieNeu-TTS, ~2 GB; or --all-voices / --vieneu)
#  Then: source .venv/bin/activate && python -m dubber
#        bash scripts/build_exe_mac.sh        (build Dubber.app)
# ============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=== [1/5] Homebrew packages ==="
if ! command -v brew >/dev/null; then
    echo "✗ Homebrew not found — install it first: https://brew.sh"
    exit 1
fi
# ffmpeg-full (not plain `ffmpeg`): needs libass to burn subtitles
brew install python@3.12 ffmpeg-full

PY312="$(brew --prefix python@3.12)/bin/python3.12"

echo "=== [2/5] Virtual environment ==="
[[ -d .venv ]] || "$PY312" -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip

echo "=== [3/5] Python packages (GUI, ASR, TTS, Groq, offline translation, VieNeu-TTS) ==="
.venv/bin/python -m pip install -e ".[dev,offline,vieneu]"

echo "=== [4/5] Models ==="
.venv/bin/python spike/download_models.py "$@"

echo "=== [5/5] Tests ==="
.venv/bin/python -m pytest -q

echo ""
echo "=== Done ==="
if ! grep -q GROQ_API_KEY ~/.zshrc 2>/dev/null; then
    echo "  Groq API key (online translation, free at console.groq.com):"
    echo "    echo 'export GROQ_API_KEY=\"gsk_...\"' >> ~/.zshrc && source ~/.zshrc"
fi
echo "  Run:    source .venv/bin/activate && python -m dubber"
echo "  Build:  bash scripts/build_exe_mac.sh"
