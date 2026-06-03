@echo off
REM Double-clickable launcher for the stream-tracklist UI.
REM Copy this to stream-tracklist.bat (gitignored) and edit REPO_DIR + PY
REM if your install lives somewhere other than the defaults.

REM Path to this repo's checkout:
set REPO_DIR=%~dp0

REM Python interpreter that has the full requirements.txt installed.
REM Leaving this as just "python" works if it's first on PATH; otherwise
REM point it at the right .exe (e.g. a venv) so spotipy / shazamio / yt-dlp
REM resolve correctly.
set PY=python

cd /d "%REPO_DIR%"
"%PY%" stream_songs.py --serve
