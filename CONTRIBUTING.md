# Contributing

Thanks for your interest. This is a small CLI tool maintained in spare time, so
keep PRs focused and contained. Bug fixes, scraper-resilience patches (Kick /
vodvod API drift), and Spotify-edge-case fixes are the easiest contributions
to merge.

## Quick start

```bash
git clone https://github.com/NexRushVr/stream-tracklist.git
cd stream-tracklist
python -m venv .venv
. .venv/Scripts/Activate.ps1   # or: source .venv/bin/activate

pip install -r requirements-dev.txt
pytest -q                       # ~30 tests, <1 s, no network / ffmpeg needed
```

The tests cover the pure-logic units (formatting, dedup, source resolution,
streamer-log JSON round-trips). Running the tool end-to-end against a real
VOD additionally needs `ffmpeg` on `PATH` and (optional) Spotify credentials
in `.env`.

## Before opening a PR

1. **`pytest` is green** locally. The CI matrix is Python 3.10 / 3.11 / 3.12
   on ubuntu-latest — if you use 3.13-only syntax, CI will fail.
2. **No personal data** in commits: real channel handles, VOD URLs containing
   IDs, your `.env`, `.spotify_token_cache`, `.cache`, `logs/`, or any
   `*_songs.csv` / `*_songs.txt` output. The `.gitignore` covers all of this,
   but double-check `git diff` before pushing.
3. **Tests for new behavior.** If you add a new source resolver, a new output
   format, or a new flag with non-trivial logic, add a unit test in `tests/`.
4. **Don't bump dependency floors** in `requirements.txt` / `pyproject.toml`
   unless you genuinely need a newer API. The existing floors are
   intentionally conservative.

## Scope: what belongs in this repo

- Source resolvers for VOD platforms (currently vodvod.top, Kick, m3u8, MP4).
- Audio sampling logic and ffmpeg invocation.
- Recognition wrappers (currently ShazamIO).
- Spotify lookup + playlist plumbing.
- Tests for all of the above.

## Scope: what doesn't

- Hosting / SaaS wrappers — keep this a local CLI.
- Direct uploaders / mirrors to non-Spotify music services (Apple Music,
  YouTube Music, Tidal, etc.). PRs welcome if you ship a clean abstraction
  alongside the existing Spotify path, but minimal one-off integrations
  are out of scope.
- Anything that scrapes or republishes streamer content without their
  consent. Music identification of your own VODs or VODs of streamers
  who have publicly posted them is fine; redistributing the audio is not.

## If you fork this

The repo URL (`github.com/NexRushVr/stream-tracklist`) appears in a few places:
`pyproject.toml` `[project.urls]`, the badges + clone command in
`README.md`, and this file. If you republish under your own name, swap
them all in one pass — `git grep NexRushVr` will list every hit.

## Reporting issues

Open an issue with:

- What you ran (`python stream_songs.py ...` exact command).
- What you expected.
- What happened — including the relevant lines from your terminal output.
- Python version, OS, ffmpeg version (`ffmpeg -version | head -1`), and the
  output of `pip freeze | grep -iE 'shazamio|spotipy|aiohttp'`.

Scraper-broken-by-API-change issues are welcome; please include a sample URL
or handle that fails so the fix can be tested.
