# stream-tracklist

[![tests](https://github.com/NexRushVr/stream-tracklist/actions/workflows/tests.yml/badge.svg)](https://github.com/NexRushVr/stream-tracklist/actions/workflows/tests.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Turn any Twitch / Kick / m3u8 / MP4 stream into a Spotify playlist of every song that played.

**What you get:** a tracklist (TXT + CSV with timestamps, titles, artists, Spotify and YouTube links) for any VOD, plus an optional Spotify playlist auto-built from the identified tracks. In `--streamer-mode`, one rolling playlist per streamer that grows every week.

**Who it's for:** fans who want the setlist from their favorite streamer's last VOD, clippers who need music IDs before posting, streamers who want to publish their own VOD soundtrack.

**Cost:** free (MIT). No cloud APIs — ShazamIO is a free unofficial Shazam client, Spotify use is free, and the only thing running is your local `ffmpeg` and Python.

**This is not:** a live-stream identifier, a Spotify-to-Twitch bot, a Discord plugin, or a hosted service. It scans a recorded VOD and produces files locally — what you do with them is up to you.

## Quickstart

Need ffmpeg + Python 3.10+ on your PATH. Then:

```bash
git clone https://github.com/NexRushVr/stream-tracklist.git
cd stream-tracklist
pip install -r requirements.txt

# Scan the latest Kick VOD for any channel (no Spotify account needed — outputs CSV/TXT)
python stream_songs.py "https://kick.com/abehamm"
```

Want a Spotify playlist too? Add your Spotify app credentials to `.env` (see [Spotify Setup](#spotify-setup)) and pass `--create-playlist`. **Don't need Spotify?** Skip that whole section — the tool falls back to Spotify search links and YouTube links in the CSV/TXT output and works fine without any account.

## Install

### 1. Python 3.10+

Check yours: `python --version`. On Windows, [install from python.org](https://www.python.org/downloads/) and tick **Add Python to PATH** during setup (don't use the Microsoft Store install — `pip` is awkward there).

### 2. ffmpeg

Per-OS one-liners:

```bash
# Windows (winget)
winget install Gyan.FFmpeg

# macOS (Homebrew)
brew install ffmpeg

# Debian / Ubuntu
sudo apt install ffmpeg
```

Verify with `ffmpeg -version`. If "command not recognized," ffmpeg isn't on your PATH yet — restart the terminal, or for the Windows zip install copy `ffmpeg.exe` into a folder that's already on PATH (e.g. `C:\Windows\System32`).

### 3. The repo

```bash
git clone https://github.com/NexRushVr/stream-tracklist.git
cd stream-tracklist
pip install -r requirements.txt
```

No git? Click **Code → Download ZIP** on the GitHub page and unzip.

### 4. (Optional) Spotify credentials

Only needed to auto-build a Spotify playlist or to get direct Spotify track URLs in the CSV. See [Spotify Setup](#spotify-setup) below. Without these you still get a tracklist file with Spotify search links.

## Usage

```bash
python stream_songs.py [OPTIONS] SOURCE
```

`SOURCE` can be:
- A local `.mp4` file path
- A direct `.m3u8` URL
- A `vodvod.top` channel URL — auto-picks the latest VOD
- A `kick.com/<handle>` URL — uses Kick's public videos API

### Top 3 commands

```bash
# 1. Scan a Kick streamer's latest VOD, build a Spotify playlist
python stream_songs.py --create-playlist "https://kick.com/abehamm"

# 2. Scan every VOD for a list of streamers, one rolling playlist per streamer
python stream_songs.py --streamer-mode --streamers eevi kick:abehamm

# 3. Rebuild a playlist from existing CSV output without re-scanning audio
python stream_songs.py --rebuild eevi
```

<details>
<summary><strong>All flags</strong> (click to expand)</summary>

| Flag | Default | Description |
|---|---|---|
| `--interval INT` | `120` | Seconds between audio samples |
| `--clip-duration INT` | `20` | Length of each audio clip in seconds |
| `--output-dir PATH` | `.` | Directory for output files |
| `--output-name TEXT` | derived | Base filename for TXT/CSV output |
| `--max-duration INT` | `86400` | Fallback cap when duration can't be probed |
| `--create-playlist` | off | Create a Spotify playlist from identified tracks |
| `--mega-playlist NAME` | — | One combined playlist for every VOD on a channel (`--all` mode) |
| `--private-playlist` | off | Make playlists private (default: public/shareable) |
| `--playlist-name TEXT` | source name | Override the playlist name (single-VOD mode only) |
| `--streamer-mode` | off | Per-streamer log + rolling playlist named after the handle |
| `--streamers HANDLE [...]` | — | Multi-streamer mode (implies `--all`). Prefix with `kick:` for Kick. |
| `--rescan` | off | In streamer mode, re-process VODs already in the log |
| `--rebuild HANDLE [...]` | — | Rebuild playlist(s) from existing CSV files; no audio scanning |
| `--log-dir PATH` | `logs` | Where per-streamer JSON logs are kept |
| `--list-streamers` | — | List streamers with logs and exit |
| `--show-streamer HANDLE` | — | Print the VOD log for a streamer and exit |
| `--retries INT` | `4` | Extra offsets per slot on no-match |
| `--verbose` | off | Print each match as it arrives |
| `--dry-run` | off | Print sample timestamps then exit |
| `--version` | — | Print version and exit |

</details>

### Example output

```
Resolving source: https://kick.com/abehamm
Source name : Friday Night Music Stream
Media type  : m3u8
Spotify     : enabled
Duration    : 03:59:03

Sampling 80 timestamps every 180s (10s clips)...
[10/80] 00:27:00  [match] "Song Title" by Artist A
[12/80] 00:33:00  [match] "Another Track" by Artist B
[16/80] 00:45:00  [match] "Third One" by Artist C
...
[80/80] 03:57:00  [match] "Closing Song" by Artist D

Looking up 21 track(s) on Spotify... 21 found.

Creating Spotify playlist: "Friday Night Music Stream" (21 tracks)...
Playlist created: https://open.spotify.com/playlist/XXXXXXXXXXXXXXXXXXXXXXX

Output written to:
  .\abehamm_2026_05_13_songs.txt
  .\abehamm_2026_05_13_songs.csv
```

Two files land in `--output-dir`: a human-readable `.txt` and a `.csv` (timestamp, title, artist, Spotify link, YouTube link).

### Streamer mode — one rolling playlist per streamer

```bash
# First run: scans every VOD, builds the "eevi" playlist, logs each VOD to logs/eevi.json
python stream_songs.py --streamer-mode --all "https://vodvod.top/channels/@eevi"

# Next week: skips VODs already in the log, appends only new tracks (deduped) to the same playlist
python stream_songs.py --streamer-mode --all "https://vodvod.top/channels/@eevi"

# Force re-scan of everything (e.g. after improving recognition settings)
python stream_songs.py --streamer-mode --all --rescan "https://vodvod.top/channels/@eevi"
```

Each log entry records the tool version, song count, tracks appended, and original m3u8 URL — so you can replay or audit later. The playlist's description links back to the streamer's Twitch or Kick page.

### Multiple streamers in one shot

```bash
# One vodvod handle + one Kick handle in a single run
python stream_songs.py --streamer-mode --streamers eevi kick:abehamm
```

Handles can be written with or without the leading `@`. The tool processes them sequentially (one streamer at a time so Shazam/Spotify rate limits stay sane) and prints a per-streamer summary at the end.

### Scheduling (Windows)

A Task Scheduler template lives at [scheduled_weekly_scan.example.bat](scheduled_weekly_scan.example.bat). To use it:

1. Copy it to `scheduled_weekly_scan.bat` (the non-example name is git-ignored so it stays local).
2. Edit `REPO_DIR` and `STREAMERS`.
3. **Run the tool interactively once first** to complete the Spotify OAuth flow — the cached token in `.spotify_token_cache` is what lets the scheduled run go headless.
4. In Task Scheduler → Create Task → check **Run whether user is logged on or not** (and supply your password). Point the action at the `.bat`.
5. If you have multiple Python versions installed, replace `python` in the `.bat` with `py -3.12` (or your version) so it picks the right interpreter.

### Rebuild a playlist from existing CSV output

```bash
python stream_songs.py --rebuild eevi abehamm
```

For each handle this globs `<output-dir>/<handle>*_songs.csv`, harvests Spotify URLs, dedupes, and appends to a playlist named after the handle. Useful as one-shot recovery if a playlist ended up empty; the regular `--streamer-mode` flow stays the source of truth going forward.

### Reading the logs

```bash
python stream_songs.py --list-streamers                # totals + playlist URL per streamer
python stream_songs.py --show-streamer eevi             # one streamer's VOD history
```

Raw logs are plain JSON at `logs/<handle>.json` if you'd rather `jq` them.

## Spotify Setup

Only needed for `--create-playlist`, `--streamer-mode`, `--rebuild`, and direct Spotify track URLs in the CSV. Skip entirely otherwise.

1. Go to [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard) → **Create app**. Name + description can be anything; website can be blank.
2. **Set the redirect URI to exactly `http://127.0.0.1:8888/callback`** — click **Add**, then **Save** at the bottom. **`localhost` will not work** — Spotify rejects it as of 2025; you must use `127.0.0.1`.
3. From the app's Settings page, copy **Client ID** and click "View client secret" to copy **Client secret**.
4. `cp .env.example .env` (Windows: `copy .env.example .env`), open `.env` in a text editor (on Windows: right-click → Open with → Notepad), and paste both values:

```
SPOTIFY_CLIENT_ID=...
SPOTIFY_CLIENT_SECRET=...
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
```

5. First playlist creation triggers a one-time browser OAuth login. **The browser will show "127.0.0.1 refused to connect" after you click Agree — that's normal and expected.** The tool reads the redirect URL out of the browser bar; you can close the tab once it's done. The token is cached in `.spotify_token_cache` so subsequent runs are automatic.

## Troubleshooting

- **`ffmpeg : command not found / not recognized`** — ffmpeg isn't on PATH. Reinstall via `winget` / `brew` / `apt` per [Install](#install), then close and reopen your terminal.
- **`python` opens the Microsoft Store** — you have the stub installed, not real Python. Install from [python.org](https://www.python.org/downloads/) with **Add Python to PATH** checked.
- **Spotify OAuth: browser shows "site can't be reached"** — that's intentional. The tool reads the auth code out of the URL bar. Just wait for the terminal to print "Playlist created."
- **Spotify rejects the redirect URI** — you typed `localhost`. Change it to `127.0.0.1` in your Spotify app settings.
- **`HTTP 403` on every ffmpeg sample** — the m3u8 token expired (vodvod and Kick VOD URLs are short-lived). Re-run the tool; it will fetch a fresh URL.
- **No matches at all** — music is probably buried under game/voice audio. Increase `--clip-duration` or sample more frequently with `--interval 60`. Match rate naturally drops when music is in the background.

## How It Works

1. **Source resolution** — for `vodvod.top` and Kick URLs, the relevant API returns the latest VOD's M3U8. Direct M3U8 and local MP4 are used as-is.
2. **Duration detection** — `ffprobe` reads the total length. Falls back to `--max-duration` for live or unprobeable streams.
3. **Audio extraction** — `ffmpeg` seeks to each timestamp with `-ss` before `-i` (fast seeking — for M3U8 this only downloads the segments around each sample, not the full stream) and extracts a short mono MP3 clip.
4. **Recognition** — ShazamIO sends the clip to Shazam's backend. Retries once on failure.
5. **Deduplication** — same (title, artist) at multiple timestamps keeps the earliest.
6. **Spotify lookup** — searches by `track:{title} artist:{artist}`. Falls back to a broader query if no exact match.
7. **Output** — writes TXT + CSV, then optionally creates / appends to a Spotify playlist via `/me/playlists`.

## Project Structure

```
stream-tracklist/
├── stream_songs.py      # CLI entry point
├── requirements.txt
├── .env.example
└── src/
    ├── source.py        # Resolves MP4 / M3U8 / vodvod.top / kick.com input
    ├── extractor.py     # ffmpeg audio extraction + ffprobe duration
    ├── recognizer.py    # ShazamIO wrapper + deduplication
    ├── spotify_client.py # Spotify search, playlist create / find / dedup-append
    ├── streamer_log.py  # Per-streamer JSON log
    └── output.py        # TXT/CSV writing, URL generation
```

## Sibling tool

Cutting clips from the same VODs? See [twitch-highlights](https://github.com/NexRushVr/twitch-highlights) — same author, same source resolvers, captioned shorts instead of playlists.

## Tests

```bash
pip install -r requirements-test.txt   # just pytest — no heavy audio stack
pytest
```

Covers the pure-logic units (formatting, dedup, source resolution, log JSON, handle validation, CSV-injection guard). External boundaries (Shazam, Spotify, vodvod/Kick APIs, ffmpeg) are mocked or omitted. CI runs the suite on Python 3.10 / 3.11 / 3.12 and on Windows for every push, PR, and once a day to catch upstream API drift.

## Notes

- **Match rate** depends on how clean the music is in the mix. Background music with game audio or voice over it won't always match — most clearly audible tracks will.
- **vodvod.top** is its own API (not yt-dlp compatible). If their API blocks the tool, grab the `.m3u8` URL from your browser's DevTools (Network tab, filter `m3u8`) and pass it directly.
- ShazamIO is a free unofficial Shazam client — no API key required.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Bug fixes for vodvod/Kick API drift and Spotify edge cases are especially welcome.

## Security

See [SECURITY.md](SECURITY.md) for the responsible-disclosure path.

## Credits

Built collaboratively with agentic AI assistance.

## License

MIT — see [LICENSE](LICENSE).
