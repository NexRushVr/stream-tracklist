@echo off
rem stream-tracklist - launch the desktop GUI from source (no exe needed).
rem Same window the bundled stream-tracklist.exe opens; use this if you're
rem running from a git checkout instead of a downloaded release.
setlocal
cd /d "%~dp0"

set "VENVPY=%~dp0.venv\Scripts\python.exe"
set "VENVPYW=%~dp0.venv\Scripts\pythonw.exe"

if not exist "%VENVPY%" (
  echo Setup hasn't been run yet.
  echo Double-click install.bat first, then come back here.
  pause
  exit /b 1
)

rem Verify pywebview is in the venv (it's in requirements.txt; this just
rem catches partial installs gracefully).
"%VENVPY%" -c "import webview" 1>nul 2>nul
if errorlevel 1 (
  echo Installing the GUI dependency ^(pywebview^)...
  "%VENVPY%" -m pip install pywebview
  if errorlevel 1 (
    echo Failed to install pywebview.  See messages above.
    pause
    exit /b 1
  )
)

rem pythonw = no console window. Falls back to python.exe if pythonw absent.
if exist "%VENVPYW%" (
  start "stream-tracklist" "%VENVPYW%" "%~dp0gui\app.py"
) else (
  start "stream-tracklist" "%VENVPY%" "%~dp0gui\app.py"
)
endlocal
