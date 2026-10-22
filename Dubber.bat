@echo off
rem Run Dubber from source (after setup_dev.ps1). Double-click or run from any folder.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo .venv not found - run: powershell -ExecutionPolicy Bypass -File setup_dev.ps1
    pause
    exit /b 1
)
rem FFmpeg from winget (Gyan.FFmpeg) is found through PATH; the Groq key is read from
rem the environment (setx GROQ_API_KEY ...) or saved in the app.
if exist "%~dp0keys.bat" call "%~dp0keys.bat"
".venv\Scripts\python.exe" -m dubber %*
