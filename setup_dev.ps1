# setup_dev.ps1 - Cài môi trường phát triển cho dự án dubber (Windows, máy mới)
# Chạy: powershell -ExecutionPolicy Bypass -File setup_dev.ps1
#       (thêm -AllVoices để tải đủ 16 giọng, -All để tải mọi model: 16 giọng + VieNeu ~2 GB)
# Sau đó: .\.venv\Scripts\Activate.ps1 ; python -m dubber
#         .\scripts\build_exe.ps1   (build Dubber.exe)

param([switch]$AllVoices, [switch]$All)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

function Refresh-Path {
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("PATH","User")
}

Write-Host "=== Cài Python 3.12 ===" -ForegroundColor Cyan
winget install --id Python.Python.3.12 --source winget --accept-package-agreements --accept-source-agreements
Refresh-Path

Write-Host ""
Write-Host "=== Cài FFmpeg (full build — có libass để đốt phụ đề) ===" -ForegroundColor Cyan
winget install --id Gyan.FFmpeg --source winget --accept-package-agreements --accept-source-agreements
Refresh-Path

Write-Host ""
Write-Host "=== Kiểm tra cài đặt ===" -ForegroundColor Cyan
py -3.12 --version
ffmpeg -version 2>&1 | Select-Object -First 1
if (-not (ffmpeg -hide_banner -filters 2>&1 | Select-String " ass ")) {
    Write-Warning "FFmpeg thiếu filter 'ass' (libass) — đốt phụ đề sẽ lỗi. Dùng bản Gyan full build."
}

Write-Host ""
Write-Host "=== Tạo virtual environment ===" -ForegroundColor Cyan
if (-not (Test-Path .venv)) { py -3.12 -m venv .venv }
.\.venv\Scripts\python.exe -m pip install --upgrade pip

Write-Host ""
Write-Host "=== Cài package (GUI, ASR, TTS, Groq, dịch offline, VieNeu-TTS) ===" -ForegroundColor Cyan
.\.venv\Scripts\python.exe -m pip install -e ".[dev,offline,vieneu]"

Write-Host ""
Write-Host "=== Tải models ===" -ForegroundColor Cyan
if ($All) {
    .\.venv\Scripts\python.exe spike\download_models.py --all
} elseif ($AllVoices) {
    .\.venv\Scripts\python.exe spike\download_models.py --all-voices
} else {
    .\.venv\Scripts\python.exe spike\download_models.py
}

Write-Host ""
Write-Host "=== Chạy tests ===" -ForegroundColor Cyan
.\.venv\Scripts\python.exe -m pytest tests/ -q

Write-Host ""
Write-Host "=== Done! ===" -ForegroundColor Green
if (-not $env:GROQ_API_KEY) {
    Write-Host "Groq API key (dịch online, miễn phí tại console.groq.com):"
    Write-Host '  setx GROQ_API_KEY "gsk_..."   (mở lại terminal sau khi chạy)'
}
Write-Host "Chạy app:  .\.venv\Scripts\Activate.ps1 ; python -m dubber"
Write-Host "Build exe: .\scripts\build_exe.ps1"
