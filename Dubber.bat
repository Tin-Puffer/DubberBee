@echo off
set PATH=%PATH%;C:\Users\VOST\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin
cd /d D:\claudeAgent
if exist "%~dp0keys.bat" call "%~dp0keys.bat"
.venv\Scripts\python.exe -m dubber
