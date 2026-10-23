# ============================================================================
#  Build Dubber.exe with PyInstaller (Windows 10/11 x64)
#  Run from the project root:  powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
#
#  Prereqs: setup_dev.ps1 has been run (venv + deps + models + FFmpeg full build)
#  Output:  dist\Dubber\Dubber.exe   and   dist\Dubber-windows.zip
#
#  Bundled (runs offline, nothing else to install): FFmpeg (with libass), ASR,
#  FSMN/Silero VAD, piper voices, VieNeu-TTS, UVR MDX-Net, torch + transformers.
#  FFmpeg is taken from $env:FFMPEG_DIR, else the winget Gyan.FFmpeg package, else PATH.
#  Works in Windows PowerShell 5.1 and PowerShell 7.
# ============================================================================

$ErrorActionPreference = 'Stop'
$project_root = (Get-Item $PSScriptRoot).Parent.FullName
Set-Location $project_root
$py = Join-Path $project_root '.venv\Scripts\python.exe'

function Has-Ass([string]$ffmpeg) {
    $filters = & $ffmpeg -hide_banner -filters 2>$null | Out-String
    return $filters -match '\sass\s'
}
function Dir-MB([string]$path) {
    return [math]::Round((Get-ChildItem $path -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 0)
}

Write-Host ""
Write-Host "=== Dubber Windows build ===" -ForegroundColor Cyan
Write-Host "Project root: $project_root"
if (-not (Test-Path $py)) { throw ".venv not found - run: powershell -ExecutionPolicy Bypass -File setup_dev.ps1" }

# -- 1. PyInstaller + any dependency added since setup (e.g. Pillow, pyvi) ------
Write-Host "[1/5] Installing PyInstaller + missing dependencies..." -ForegroundColor Yellow
& $py -m pip install --quiet "pyinstaller>=6.0"
if ($LASTEXITCODE -ne 0) { throw "pip install pyinstaller failed" }
& $py -m pip install --quiet -e ".[offline,vieneu]"
if ($LASTEXITCODE -ne 0) { throw "pip install dependencies failed" }

# -- 2. resources\: FFmpeg -----------------------------------------------------
Write-Host "[2/5] Preparing resources\..." -ForegroundColor Yellow
New-Item -ItemType Directory -Force "resources\models\piper" | Out-Null

$ffmpeg_src = $null
$candidates = @()
if ($env:FFMPEG_DIR) { $candidates += $env:FFMPEG_DIR }
$winget_pkgs = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
if (Test-Path $winget_pkgs) {
    $candidates += Get-ChildItem $winget_pkgs -Directory -Filter 'Gyan.FFmpeg*' |
        ForEach-Object { Get-ChildItem $_.FullName -Directory -Recurse -Filter bin } |
        ForEach-Object { $_.FullName }
}
$on_path = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue
if ($on_path) { $candidates += (Split-Path $on_path.Source) }
foreach ($d in $candidates) {
    $exe = Join-Path $d 'ffmpeg.exe'
    if ((Test-Path $exe) -and (Test-Path (Join-Path $d 'ffprobe.exe')) -and (Has-Ass $exe)) {
        $ffmpeg_src = $d; break
    }
}
if (-not $ffmpeg_src) {
    throw "No FFmpeg with libass (filter 'ass') found - run: winget install Gyan.FFmpeg  (or set FFMPEG_DIR)"
}
foreach ($bin in 'ffmpeg.exe', 'ffprobe.exe') {
    Copy-Item (Join-Path $ffmpeg_src $bin) "resources\" -Force
}
# Shared (non-static) FFmpeg builds keep their DLLs next to ffmpeg.exe
Get-ChildItem $ffmpeg_src -Filter *.dll -ErrorAction SilentlyContinue | Copy-Item -Destination "resources\" -Force
Write-Host "      FFmpeg: $ffmpeg_src"

# -- 3. resources\models: ASR, VAD (FSMN + Silero), voices, VieNeu, UVR -------
$sherpa = 'sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17'
if (-not (Test-Path "spike\models\$sherpa\tokens.txt") -or -not (Test-Path "spike\models\fsmn-vad\config.yaml")) {
    Write-Host "      Models missing -> downloading"
    & $py spike\download_models.py
    if ($LASTEXITCODE -ne 0) { throw "download_models.py failed" }
}
# Only what the app loads: model.int8.onnx + tokens.txt (skip test_wavs, fp32 model, ...)
New-Item -ItemType Directory -Force "resources\models\$sherpa" | Out-Null
foreach ($f in 'model.int8.onnx', 'tokens.txt') {
    Copy-Item "spike\models\$sherpa\$f" "resources\models\$sherpa\" -Force
}
Copy-Item "spike\models\silero_vad.onnx" "resources\models\" -Force
New-Item -ItemType Directory -Force "resources\models\fsmn-vad" | Out-Null
foreach ($f in 'model_quant.onnx', 'config.yaml', 'am.mvn') {
    Copy-Item "spike\models\fsmn-vad\$f" "resources\models\fsmn-vad\" -Force
}
Copy-Item "spike\models\piper\*.onnx", "spike\models\piper\*.onnx.json" "resources\models\piper\" -Force
Write-Host "      Voices: $((Get-ChildItem 'resources\models\piper' -Filter *.onnx).Count)"

# VieNeu-TTS models (~610 MB): user-data / spike copy, downloaded if missing
# (last line only: the downloads may print progress first)
$vieneu_dir = & $py -c "from dubber.core.asr.model_manager import find_vieneu_model_dir as f, download_vieneu_models as d; print(f() or d())" | Select-Object -Last 1
if ($LASTEXITCODE -ne 0 -or -not $vieneu_dir -or -not (Test-Path $vieneu_dir)) { throw "VieNeu models unavailable" }
Remove-Item "resources\models\vieneu" -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item $vieneu_dir "resources\models\vieneu" -Recurse
Get-ChildItem "resources\models\vieneu" -Directory -Recurse -Force -Filter ".cache" | Remove-Item -Recurse -Force
Write-Host "      VieNeu: $(Dir-MB 'resources\models\vieneu') MB"

# UVR MDX-Net model + audio-separator's model lists, so separation works offline
$uvr_dir = & $py scripts\fetch_uvr_model.py | Select-Object -Last 1
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $uvr_dir)) { throw "UVR model unavailable" }
Remove-Item "resources\models\uvr" -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "resources\models\uvr" | Out-Null
Copy-Item "$uvr_dir\*.onnx", "$uvr_dir\*.json" "resources\models\uvr\" -Force
Write-Host "      UVR: $((Get-ChildItem 'resources\models\uvr').Name -join ' ')"
Write-Host "      resources\ total: $(Dir-MB 'resources') MB"

# -- 4. PyInstaller ------------------------------------------------------------
Write-Host "[3/5] Running PyInstaller (a few minutes)..." -ForegroundColor Yellow
& $py -m PyInstaller dubber.spec --noconfirm
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (exit code $LASTEXITCODE)" }

# -- 5. Verify + zip -----------------------------------------------------------
Write-Host "[4/5] Verifying output..." -ForegroundColor Yellow
$app = "dist\Dubber"
if (-not (Test-Path "$app\Dubber.exe")) { throw "Build failed - $app\Dubber.exe not found" }
$bundled_ff = Get-ChildItem $app -Recurse -Filter ffmpeg.exe | Select-Object -First 1
if (-not $bundled_ff -or -not (Has-Ass $bundled_ff.FullName)) { throw "Bundled ffmpeg.exe missing or cannot run" }

Write-Host "[5/5] Zipping..." -ForegroundColor Yellow
$zip = "dist\Dubber-windows.zip"
Remove-Item $zip -Force -ErrorAction SilentlyContinue
# Windows' own bsdtar writes zip64 (Compress-Archive breaks on > 2 GB). Call it by full
# path: Git for Windows puts GNU tar on PATH, which cannot write zip.
$bsdtar = Join-Path $env:SystemRoot 'System32\tar.exe'
& $bsdtar -a -c -f $zip -C dist Dubber
if ($LASTEXITCODE -ne 0) {
    Write-Warning "      tar failed - falling back to .NET ZipFile"
    Remove-Item $zip -Force -ErrorAction SilentlyContinue
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::CreateFromDirectory((Resolve-Path $app).Path,
        (Join-Path $project_root $zip), [System.IO.Compression.CompressionLevel]::Optimal, $true)
}
if (-not (Test-Path $zip)) { throw "zip failed" }

Write-Host ""
Write-Host "=== Build complete ===" -ForegroundColor Green
Write-Host "  App:  $app\Dubber.exe  ($(Dir-MB $app) MB)"
Write-Host "  Zip:  $zip  ($([math]::Round((Get-Item $zip).Length / 1MB, 0)) MB)"
Write-Host "  Run:  .\$app\Dubber.exe"
Write-Host "  Unzip to a short path (e.g. C:\Dubber) - deep folders can exceed Windows' 260-char path limit."
