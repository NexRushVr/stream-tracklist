# Changelog

All notable changes to this project will be documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [SemVer](https://semver.org/).

## [Unreleased]

### Added
- **Durable, incremental recognition artifact + resume.** Each VOD scan now
  writes a `<name>_matches.jsonl` as every slot completes — *before* any Spotify
  call. A crash, Ctrl-C, or Spotify quota stall mid-scan no longer throws away
  hours of recognition work. On a re-run, an interrupted scan resumes from the
  last sampled timestamp, and a completed scan re-resolves from the artifact
  without re-fingerprinting any audio. New `src/matches.py` (append/read/
  done-marker, tolerant of a truncated final line) with unit tests.
- `--fresh` — ignore an existing `_matches.jsonl` and rescan from the start
  (default is to resume/re-resolve).
- `--streamers-file PATH` — read streamer handles from a file (one per line,
  `#` comments and blanks ignored, `kick:` prefix supported). Merged with any
  `--streamers`, and also scopes `--backfill-spotify`. For a curated daily-run
  list. Ships with `daily_streamers.txt` (a vetted VRChat-DJ + music list) and
  `scheduled_daily_scan.example.bat`.
- **Spotify search cache** — at startup the existing `*_songs.csv` files in
  `--output-dir` are read into an in-memory `(title, artist) → result` cache.
  Tracks a DJ replays across sets cost zero Spotify calls on re-runs. Primed
  positives (real `/track/` links) and negatives (prior `/search/` fallbacks)
  both load; negatives can be skipped (see `--backfill-spotify`).
- **ISRC capture + exact lookup** — Shazam's `track.isrc` is now captured into
  `RecognitionResult`, written as a new `ISRC` CSV column, and used as the
  first Spotify query (`isrc:<code>`). Deterministic resolution that catches
  remixes/bootlegs fuzzy `track:/artist:` search misses, and removes the
  second broadened call on a hit.
- `--backfill-spotify [HANDLE ...]` — recovery path that retries Spotify
  resolution for unresolved (`/search/` fallback) rows in existing CSVs,
  rewrites them in place (using the `ISRC` column when present), and
  dedup-appends newly-resolved tracks to each handle's playlist. No audio
  scanning. Pass handles to scope it, or no args to backfill every CSV in
  `--output-dir`. The intended follow-up after a run that hit the daily quota.
- `--from-youtube URL` — convert a YouTube playlist into a Spotify playlist
  with no audio scanning. yt-dlp enumerates the playlist flat (no media
  download); each video title is parsed into `(artist, title)` and matches
  are dedup-appended into a playlist named after the YouTube playlist (or
  `--playlist-name`). Handles `"X - Topic"` YouTube Music channels and
  `Artist - Title (Official Video)` titles; preserves track-distinguishing
  tags (`Remix`, `feat.`, `Acoustic`, `Live`) for the Spotify search.
- `src/youtube_playlist.py` with a pure, unit-tested title parser and a
  yt-dlp CLI wrapper (invoked as an external tool like ffmpeg, so the
  module imports cleanly without yt-dlp installed).
- 30 unit tests for URL gating, the title parser, and `fetch_playlist`
  (yt-dlp mocked — suite stays offline).

### Changed
- `requirements.txt` adds `yt-dlp` (only used by `--from-youtube`).
- **Spotify 429s degrade instead of hanging.** The search client is now built
  with `retries=0` so spotipy no longer sleeps on a 429 `Retry-After` — a daily
  quota limit's `Retry-After` can be ~19h, which previously hung an entire
  batch silently. On a 429 the run sets a process-wide flag, prints a notice,
  skips all further lookups (unresolved tracks fall back to a `/search/` URL),
  and finishes; `--backfill-spotify` fills them in after the quota resets.
- CSV output gains an `ISRC` column (sixth). Older five-column CSVs are still
  read fine; `--backfill-spotify` preserves whatever schema each file already
  has when rewriting.

### Fixed
- Slot recognitions obtained on a retry after the first extraction attempt
  errored were silently discarded: `slot_failed` was set on attempt 0 and never
  cleared, so a slot that recovered on a later offset still hit the `[skip]`
  path. Slots are now treated as failed only when *no* attempt produced a clip.
- The shared Shazam aiohttp session is now closed at the end of non-streamer
  multi-VOD runs too (previously only `--streamer-mode` closed it), silencing
  "Unclosed client session" / leaked-connector warnings.

## [1.0.0] - 2026-05-13

Initial public release. A local CLI that Shazams audio clips out of MP4 files,
m3u8 streams, vodvod.top channels, and Kick.com VODs, then optionally builds
a Spotify playlist from the identified tracks — no cloud APIs, no per-clip
cost beyond your own ShazamIO and Spotify rate limits.

### Added
- Source resolvers for local `.mp4`, raw `.m3u8` URLs, `vodvod.top` channels
  (auto-picks the latest VOD or all VODs with `--all`), and `kick.com`
  channels (uses the public videos API).
- ffmpeg-based sampling: seeks with `-ss` before `-i` so M3U8 sources only
  download the segments around each sample timestamp, not the full stream.
- ShazamIO recognition with one retry on transient failure and case-insensitive
  (title, artist) deduplication that keeps the earliest timestamp.
- Spotify integration via spotipy: track lookup (with broader fallback query)
  plus public-or-private playlist creation, find-existing-by-name, and
  dedup-append on re-runs.
- `--streamer-mode` — per-streamer JSON log in `logs/<handle>.json` records
  every VOD processed (tool version, song count, tracks added, original m3u8)
  so future runs skip already-processed VODs and append only new tracks to
  one rolling per-streamer playlist.
- `--streamers HANDLE [HANDLE ...]` — multi-streamer mode in a single
  invocation. Mix vodvod and Kick handles with the `kick:` prefix.
- `--rebuild HANDLE [HANDLE ...]` — recovery path that re-populates a
  playlist from existing `*_songs.csv` files without re-scanning audio.
- `--list-streamers` / `--show-streamer HANDLE` — inspect saved logs.
- `--mega-playlist NAME` — combine every VOD on a channel into one playlist.
- `--dry-run` — preview sample timestamps and exit without invoking Shazam.
- TXT + CSV output per VOD, with timestamp / title / artist / Spotify /
  YouTube columns. Spotify falls back to a search-page URL when no direct
  track match is found.
- Windows Task Scheduler example: [scheduled_weekly_scan.example.bat](scheduled_weekly_scan.example.bat).
- GitHub Actions workflow: tests on push / PR / daily cron, Python
  3.10 / 3.11 / 3.12 matrix on ubuntu-latest.
- 31 unit tests covering output formatting + CSV/TXT round-trips, dedup
  ordering, source resolution, and streamer-log JSON persistence
  (including UTF-8 safety and corrupt-file recovery). External boundaries
  (Shazam, Spotify, vodvod/Kick APIs, ffmpeg) are not exercised in the
  test suite.

### Fixed
- `resolve()` previously rejected absolute Windows paths because `urlparse`
  treats a drive letter (e.g. `C:`) as a one-character URL scheme. Single-letter
  schemes are now treated as drive letters, not URLs.
