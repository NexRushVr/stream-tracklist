@echo off
REM Example: scan a curated list of streamers daily (Windows Task Scheduler).
REM 1. Edit REPO_DIR to point at your local clone.
REM 2. Edit daily_streamers.txt to curate the handle list (one per line).
REM 3. Schedule this .bat in Task Scheduler (e.g. daily, late morning after the
REM    Spotify quota window resets). --streamer-mode only scans NEW VODs, so
REM    most days this is quick.
REM 4. The backfill pass below resolves any tracks the daily scan left as a
REM    /search/ fallback because the Spotify quota was hit — no audio re-scan.

set REPO_DIR=C:\path\to\stream-tracklist

cd /d "%REPO_DIR%"
echo. >> scheduled_scan.log
echo === Daily scan started: %DATE% %TIME% === >> scheduled_scan.log
python stream_songs.py --streamer-mode --streamers-file daily_streamers.txt >> scheduled_scan.log 2>&1
echo === Scan finished: %DATE% %TIME% (exit %ERRORLEVEL%) === >> scheduled_scan.log

REM Resolve any rows the scan couldn't (quota) — cheap, no audio scanning.
python stream_songs.py --backfill-spotify --streamers-file daily_streamers.txt >> scheduled_scan.log 2>&1
echo === Backfill finished: %DATE% %TIME% (exit %ERRORLEVEL%) === >> scheduled_scan.log
