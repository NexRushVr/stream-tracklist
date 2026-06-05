@echo off
rem stream-tracklist - first-run setup. Creates .venv and installs the
rem full runtime requirements (spotipy, shazamio, yt-dlp, flask, pywebview).
rem Double-click this once. Then launch stream-tracklist.exe (or gui.bat).
setlocal
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo Python 3.10+ wasn't found on PATH.
  echo Install it from https://www.python.org/downloads/ ^(tick "Add Python to PATH"^)
  echo and run this installer again.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating .venv ^(one-time setup^)...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo venv creation failed.  See messages above.
    pause
    exit /b 1
  )
)

set "VENVPY=%~dp0.venv\Scripts\python.exe"

echo Upgrading pip / wheel...
"%VENVPY%" -m pip install --upgrade pip wheel >nul

echo Installing runtime requirements ^(takes a minute the first time^)...
"%VENVPY%" -m pip install -r requirements.txt
if errorlevel 1 (
  echo Install failed.  See messages above.
  pause
  exit /b 1
)

echo.
echo Done.  You can now:
echo   - double-click stream-tracklist.exe ^(if you downloaded a build^)
echo   - or double-click gui.bat to launch from source
echo.
echo You'll also want a .env file with your Spotify Client ID / Secret -
echo see .env.example for the format.  See README.md if you need a walkthrough.
echo.
pause
endlocal
