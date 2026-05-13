# Changelog

All notable changes to this project will be documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [SemVer](https://semver.org/).

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
