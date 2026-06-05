<#
  Package the stream-tracklist desktop GUI into a single clickable
  Windows app: dist\stream-tracklist.exe

  Run from the repo root:
      powershell -ExecutionPolicy Bypass -File build_gui.ps1

  The exe is a thin shell. It does NOT bundle spotipy / shazamio / yt-dlp /
  flask -- it runs the project's existing .venv python against
  stream_songs.py --serve, so it stays small (~30 MB) and respects whatever
  version of the runtime stack the user has installed in their .venv.

  Drop dist\stream-tracklist.exe next to .venv and stream_songs.py and
  double-click it.  (Still requires install.bat to have been run.)

  Builds --onefile.  For debugging a bundling problem, swap --onefile for
  --onedir below to get a readable dist\stream-tracklist\ folder with an
  _internal layout.
#>

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "No .venv found. Run install.bat first." -ForegroundColor Red
    exit 1
}

Write-Host "Installing PyInstaller (build-only)..." -ForegroundColor Cyan
& $py -m pip install --quiet "pyinstaller>=6.0"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Building stream-tracklist.exe..." -ForegroundColor Cyan
# --onefile: a single clickable exe.  For debugging a bundling issue, swap
# --onefile for --onedir (faster start, emits dist\stream-tracklist\ with
# a readable _internal/ folder).
& $py -m PyInstaller `
    --noconfirm `
    --clean `
    --noconsole `
    --onefile `
    --name stream-tracklist `
    --paths gui `
    --add-data "gui\web;web" `
    --collect-all webview `
    --collect-all clr_loader `
    --exclude-module pytest `
    --exclude-module _pytest `
    "gui\app.py"

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "Built: dist\stream-tracklist.exe" -ForegroundColor Green
    Write-Host "Copy dist\stream-tracklist.exe to the repo root (next to .venv" -ForegroundColor Green
    Write-Host "and stream_songs.py) and double-click it." -ForegroundColor Green
} else {
    Write-Host "PyInstaller failed (exit $LASTEXITCODE).  Scroll up for details." -ForegroundColor Red
    exit $LASTEXITCODE
}
