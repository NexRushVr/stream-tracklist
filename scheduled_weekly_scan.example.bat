@echo off
REM Example: scan several streamers on a schedule (Windows Task Scheduler).
REM 1. Edit REPO_DIR to point at your local clone.
REM 2. Edit the STREAMERS line: space-separated handles. Prefix Kick handles with "kick:".
REM 3. Schedule this .bat in Task Scheduler at whatever cadence you like.

set REPO_DIR=C:\path\to\stream-tracklist
set STREAMERS=eevi kick:abehamm

cd /d "%REPO_DIR%"
echo. >> scheduled_scan.log
echo === Run started: %DATE% %TIME% === >> scheduled_scan.log
python stream_songs.py --streamer-mode --streamers %STREAMERS% >> scheduled_scan.log 2>&1
echo === Run finished: %DATE% %TIME% (exit %ERRORLEVEL%) === >> scheduled_scan.log
