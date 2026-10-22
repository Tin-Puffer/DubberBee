# ============================================================================
#  Build Dubber.exe with PyInstaller
#  Run from the project root:  .\scripts\build_exe.ps1
#
#  Output:  dist\Dubber\Dubber.exe   (~700 MB folder)
#  What's NOT bundled: torch, transformers, audio_separator (too large)
#    → Audio separation falls back to FFmpeg center-channel cancellation
#    → Offline translation disabled; Groq API required for translation
# ============================================================================

$ErrorActionPreference = 'Stop'
$project_root = (Get-Item $PSScriptRoot).Parent.FullName
Set-Location $project_root

Write-Host ""
Write-Host "=== Dubber Build Script ===" -ForegroundColor Cyan
Write-Host "Project root: $project_root"
Write-Host ""

# ── 1. Install / upgrade PyInstaller ─────────────────────────────────────────
Write-Host "[1/5] Installing PyInstaller..." -ForegroundColor Yellow
.\.venv\Scripts\pip.exe install "pyinstaller>=6.0" --quiet
Write-Host "      PyInstaller ready."

# ── 2. Prepare resources/ directory ──────────────────────────────────────────
Write-Host "[2/5] Preparing resources/..." -ForegroundColor Yellow

New-Item -ItemType Directory -Force "resources\models\piper" | Out-Null
New-Item -ItemType Directory -Force "resources\models\uvr"   | Out-Null

# --- Copy FFmpeg (look in WinGet path, then PATH) ---
$ffmpeg_winget = "C:\Users\VOST\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin"
$ffmpeg_src = if (Test-Path $ffmpeg_winget) { $ffmpeg_winget } else {
    $found = (Get-Command ffmpeg -ErrorAction SilentlyContinue)?.Source
    if ($found) { Split-Path $found } else { $null }
}

if ($ffmpeg_src) {
    foreach ($bin in @('ffmpeg.exe', 'ffprobe.exe')) {
        $src = Join-Path $ffmpeg_src $bin
        if (Test-Path $src) {
            Copy-Item $src "resources\" -Force
            $sz = [math]::Round((Get-Item $src).Length / 1MB, 1)
            Write-Host "      Copied $bin ($sz MB)"
        } else {
            Write-Warning "      $bin not found at $src"
        }
    }
} else {
    Write-Warning "      FFmpeg not found — Dubber.exe will require ffmpeg in PATH"
}

# --- Copy ASR model (sherpa-onnx SenseVoice) ---
$sherpa_src = "spike\models\sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"
$sherpa_dst = "resources\models\sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"
if (Test-Path $sherpa_src) {
    if (-not (Test-Path $sherpa_dst)) {
        Copy-Item $sherpa_src $sherpa_dst -Recurse
        $sz = [math]::Round((Get-ChildItem $sherpa_dst -Recurse | Measure-Object Length -Sum).Sum / 1MB, 0)
        Write-Host "      Copied sherpa model ($sz MB)"
    } else {
        Write-Host "      sherpa model already in resources/ (skip)"
    }
} else {
    Write-Warning "      Sherpa model not found — run spike/download_models.py first"
}

# --- Copy Silero VAD ---
if (Test-Path "spike\models\silero_vad.onnx") {
    Copy-Item "spike\models\silero_vad.onnx" "resources\models\" -Force
    Write-Host "      Copied silero_vad.onnx"
}

# --- Copy FSMN-VAD (default VAD; Silero is the fallback) ---
if (Test-Path "spike\models\fsmn-vad\config.yaml") {
    New-Item -ItemType Directory -Force "resources\models\fsmn-vad" | Out-Null
    foreach ($f in "model_quant.onnx", "config.yaml", "am.mvn") {
        Copy-Item "spike\models\fsmn-vad\$f" "resources\models\fsmn-vad\" -Force
    }
    Write-Host "      FSMN-VAD: OK"
} else {
    Write-Warning "      FSMN-VAD not found — run spike/download_models.py (app falls back to Silero)"
}

# --- Copy piper voice models ---
if (Test-Path "spike\models\piper") {
    $copied = 0
    foreach ($f in Get-ChildItem "spike\models\piper" -File) {
        Copy-Item $f.FullName "resources\models\piper\" -Force
        $copied++
    }
    Write-Host "      Copied $copied piper model files"
} else {
    Write-Warning "      Piper models not found in spike\models\piper"
}

# --- Copy VieNeu-TTS models (~610 MB; downloaded if missing) ---
$vieneu_dir = & .\.venv\Scripts\python.exe -c "from dubber.core.asr.model_manager import find_vieneu_model_dir as f, download_vieneu_models as d; print(f() or d())"
if ($LASTEXITCODE -eq 0 -and $vieneu_dir -and (Test-Path $vieneu_dir)) {
    Remove-Item "resources\models\vieneu" -Recurse -Force -ErrorAction SilentlyContinue
    Copy-Item $vieneu_dir "resources\models\vieneu" -Recurse
    Get-ChildItem "resources\models\vieneu" -Directory -Recurse -Filter ".cache" |
        Remove-Item -Recurse -Force
    Write-Host "      VieNeu: OK"
} else {
    Write-Warning "      VieNeu models unavailable — the bundled app will download them on first use"
}

# --- Copy UVR MDX-Net model (if already downloaded) ---
$uvr_src_candidates = @(
    "D:\tmp\audio-separator-models\UVR-MDX-NET-Inst_HQ_5.onnx",
    "$env:USERPROFILE\.cache\dubber\dubber\uvr-models\UVR-MDX-NET-Inst_HQ_5.onnx"
)
$uvr_copied = $false
foreach ($uvr_src in $uvr_src_candidates) {
    if (Test-Path $uvr_src) {
        Copy-Item $uvr_src "resources\models\uvr\" -Force
        # Copy supporting JSON files
        $uvr_dir = Split-Path $uvr_src
        foreach ($json in Get-ChildItem $uvr_dir -Filter "*.json") {
            Copy-Item $json.FullName "resources\models\uvr\" -Force
        }
        $sz = [math]::Round((Get-Item $uvr_src).Length / 1MB, 1)
        Write-Host "      Copied UVR model ($sz MB)"
        $uvr_copied = $true
        break
    }
}
if (-not $uvr_copied) {
    Write-Host "      UVR model not cached locally (will download on first use)"
}

# ── 3. Show resource summary ──────────────────────────────────────────────────
$res_size = [math]::Round((Get-ChildItem "resources" -Recurse | Measure-Object Length -Sum).Sum / 1MB, 0)
Write-Host "      resources/ total: $res_size MB"

# ── 4. Run PyInstaller ────────────────────────────────────────────────────────
Write-Host "[3/5] Running PyInstaller (this takes 1-3 minutes)..." -ForegroundColor Yellow
.\.venv\Scripts\pyinstaller.exe dubber.spec --noconfirm
if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller failed (exit code $LASTEXITCODE)"
    exit 1
}

# ── 5. Verify output ──────────────────────────────────────────────────────────
Write-Host "[4/5] Verifying output..." -ForegroundColor Yellow
if (Test-Path "dist\Dubber\Dubber.exe") {
    $dist_size = [math]::Round((Get-ChildItem "dist\Dubber" -Recurse | Measure-Object Length -Sum).Sum / 1MB, 0)
    Write-Host ""
    Write-Host "=== Build complete! ===" -ForegroundColor Green
    Write-Host "  Executable:  dist\Dubber\Dubber.exe"
    Write-Host "  Total size:  $dist_size MB"
    Write-Host ""
    Write-Host "  To run:      .\dist\Dubber\Dubber.exe"
    Write-Host "  To zip:      Compress-Archive dist\Dubber -DestinationPath dist\Dubber-win64.zip"
    Write-Host ""
    Write-Host "  Note: torch/transformers not bundled." -ForegroundColor DarkYellow
    Write-Host "        Audio separation uses FFmpeg fallback." -ForegroundColor DarkYellow
    Write-Host "        Offline translation requires Groq API key." -ForegroundColor DarkYellow
} else {
    Write-Error "Build failed — dist\Dubber\Dubber.exe not found"
    exit 1
}
