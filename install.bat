@echo off
setlocal
cd /d "%~dp0"
title Veera installer
echo === Veera installer ===

where py >nul 2>nul && (set PY=py -3) || (where python >nul 2>nul && (set PY=python) || (
  echo Python 3.10+ was not found. Install it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^), then run this again.
  pause & exit /b 1))

echo Creating virtual environment...
%PY% -m venv .venv || (echo Could not create the virtual environment. & pause & exit /b 1)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt || (echo Package install failed - see the error above. & pause & exit /b 1)

if not exist .env copy .env.example .env >nul

if not exist models\vosk-model-small-en-us-0.15 (
  echo Downloading the small offline speech model ^(~40 MB, optional^)...
  powershell -NoProfile -Command "try { New-Item -ItemType Directory -Force models | Out-Null; Invoke-WebRequest https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip -OutFile models\vosk.zip; Expand-Archive models\vosk.zip models -Force; Remove-Item models\vosk.zip } catch { Write-Host 'Skipped offline model (not required).' }"
)

echo.
echo Done. Optional: open .env and add your GEMINI_API_KEY for summaries.
echo Start Veera by double-clicking run.bat, then say "Hey Veera".
pause
