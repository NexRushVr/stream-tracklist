# Security

This is a small personal-scale CLI tool, not a hosted service. It has no
network-facing components of its own; the attack surface is essentially
"what third-party Python deps + ffmpeg do on your machine, plus your
own Spotify OAuth tokens cached on disk."

## Reporting a vulnerability

If you find something genuinely security-relevant (e.g. a way the vodvod /
Kick scrapers could be coerced into running attacker-controlled commands,
a path-traversal in the output writer, or a dependency chain compromise),
please **open a private security advisory** via GitHub's "Report a
vulnerability" button on the Security tab rather than filing a public
issue.

For ordinary bug reports, use the regular issue tracker.

## Scope notes

- `.env` (Spotify client ID / secret) and `.spotify_token_cache` (OAuth
  refresh token) live in the repo root and are git-ignored. Anyone with
  read access to your home directory can lift them — don't sync this
  folder to a shared drive.
- VOD URLs, m3u8 URLs, channel handles, and local file paths are passed
  to `ffmpeg`. Treat any URL you feed this tool as code — it will be
  opened and downloaded by ffmpeg.
- Output filenames are derived from the source name via
  `re.sub(r"[^\w]", "_", ...)` so a hostile stream title cannot create
  a file outside `--output-dir`.
- Spotify scopes requested: `playlist-modify-public` and
  `playlist-modify-private`. The tool never reads your library, follows,
  or follow lists.
