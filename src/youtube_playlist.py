"""YouTube playlist -> (title, artist) extraction.

This is the metadata-only path: no audio fingerprinting, no ffmpeg. We ask
yt-dlp to enumerate a playlist (flat — it never downloads a byte of media),
then parse each video's title/uploader into a best-effort (title, artist)
pair for a Spotify search.

yt-dlp is invoked as an external CLI (like ffmpeg elsewhere in this project)
rather than imported, so this module imports cleanly in CI without the
yt-dlp package installed.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from urllib.parse import urlparse


class YouTubeError(RuntimeError):
    """yt-dlp missing, unreachable, or returned something unusable."""


@dataclass
class YouTubeTrack:
    raw_title: str   # the video title, untouched (for logs / debugging)
    title: str       # parsed song title
    artist: str      # parsed artist


# A flat playlist dump is small (a few KB/entry). Anything past this is either
# a pathological playlist or a hostile/MITMed response — refuse it rather than
# feed gigabytes into json.loads.
_MAX_JSON_BYTES = 64 * 1024 * 1024
_MAX_ENTRIES = 5000
_YTDLP_TIMEOUT = 120  # flat enumeration is fast; this is a generous ceiling


def _host_ok(netloc: str) -> bool:
    """Accept only real YouTube hosts — never a substring trick like
    youtube.com.evil.net or notyoutube.com."""
    host = netloc.split(":", 1)[0].lower()
    for s in ("youtube.com", "youtu.be", "youtube-nocookie.com"):
        if host == s or host.endswith("." + s):
            return True
    return False


def is_youtube_playlist_url(url: str) -> bool:
    """True for a URL we should route through the YouTube path. Requires a
    real YouTube host AND a list= param (or a youtu.be/...?list= form)."""
    try:
        p = urlparse(url)
    except Exception:
        return False
    if p.scheme not in ("http", "https") or not _host_ok(p.netloc):
        return False
    return "list=" in (p.query or "")


# Bracketed/parenthesized chunks that are production metadata, not part of the
# song's identity. Kept deliberately conservative: things that DO change the
# track on Spotify (Remix, Acoustic, Live, feat., VIP, Edit, Bootleg) are left
# in so the search can find the right version.
_JUNK_TAG_RE = re.compile(
    r"""\s*[\(\[]\s*
        (?:
            official(?:\s+(?:music\s+)?(?:video|audio|lyric\s*video))?
          | (?:full\s+)?(?:music\s+)?video
          | (?:official\s+)?lyrics?(?:\s+video)?
          | audio | visuali[sz]er | mv | m/v | hd | 4k | hq
          | explicit | clean | remaster(?:ed)?(?:\s+\d{4})?
          | monstercat(?:\s+release)?
          | ncs\s+release | free\s+(?:dl|download)
          | color\s+coded(?:\s+lyrics)? | sub(?:s|titulado|\s+espa[nñ]ol)?
          | now\s+available | out\s+now | premiere
        )
        \s*[\)\]]""",
    re.IGNORECASE | re.VERBOSE,
)

# "Artist - Title" separators, including the unicode dashes YouTube titles
# love. Order matters only in that we split on the FIRST occurrence.
_SEP_RE = re.compile(r"\s+[-–—|｜]\s+")

# Leading "01.", "1)", "12 -" track numbers (only stripped when clearly a
# numeric prefix, so it can't eat a real "2 - Title" artist named "2").
_TRACKNUM_RE = re.compile(r"^\s*\d{1,3}\s*[.)]\s+")

_QUOTES = "\"'“”‘’«»「」『』"


def _clean(s: str) -> str:
    prev = None
    while prev != s:
        prev = s
        s = _JUNK_TAG_RE.sub("", s).strip()
    s = s.strip().strip(_QUOTES).strip()
    return re.sub(r"\s{2,}", " ", s)


def _strip_channel_suffix(uploader: str) -> str:
    """Normalize a channel name into an artist guess: drop the auto-generated
    'X - Topic' suffix and the VEVO/Official cruft."""
    u = uploader.strip()
    if u.lower().endswith("- topic"):
        u = u[: -len("- topic")].strip()
    u = re.sub(r"\s*VEVO$", "", u, flags=re.IGNORECASE)
    u = re.sub(r"\s*-?\s*Official$", "", u, flags=re.IGNORECASE)
    return u.strip()


def split_artist_title(raw_title: str, uploader: str = "") -> tuple[str, str]:
    """Best-effort (title, artist) from a YouTube video title + channel.

    Strategy, in order of reliability:
      1. 'X - Topic' channel  -> YouTube Music auto-upload; channel IS the
         artist, the video title IS the song.
      2. 'Artist - Title' in the cleaned title -> split on the first dash.
      3. Fallback: whole cleaned title as the song, channel as the artist.
    """
    raw_title = (raw_title or "").strip()
    uploader = (uploader or "").strip()

    title_clean = _TRACKNUM_RE.sub("", raw_title)
    title_clean = _clean(title_clean)

    if uploader.lower().endswith("- topic"):
        artist = _strip_channel_suffix(uploader)
        if artist and title_clean:
            return title_clean, artist

    parts = _SEP_RE.split(title_clean, maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        artist, title = parts[0].strip(), parts[1].strip()
        return _clean(title), _clean(artist)

    return title_clean or raw_title, _strip_channel_suffix(uploader)


def _ytdlp_path() -> str:
    exe = shutil.which("yt-dlp")
    if not exe:
        raise YouTubeError(
            "yt-dlp not found on PATH. Install it: pip install yt-dlp "
            "(or see https://github.com/yt-dlp/yt-dlp#installation)."
        )
    return exe


def _run_ytdlp(url: str) -> dict:
    exe = _ytdlp_path()
    try:
        proc = subprocess.run(
            [exe, "--flat-playlist", "--no-warnings", "--ignore-errors",
             "--dump-single-json", "--", url],
            capture_output=True, timeout=_YTDLP_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise YouTubeError(f"yt-dlp timed out after {_YTDLP_TIMEOUT}s") from exc
    except OSError as exc:
        raise YouTubeError(f"Could not run yt-dlp: {exc}") from exc

    if len(proc.stdout) > _MAX_JSON_BYTES:
        raise YouTubeError(
            f"yt-dlp output exceeded {_MAX_JSON_BYTES} bytes — refusing to parse."
        )
    if proc.returncode != 0 and not proc.stdout.strip():
        err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        tail = err[-1] if err else f"exit code {proc.returncode}"
        raise YouTubeError(f"yt-dlp failed: {tail}")

    try:
        return json.loads(proc.stdout)
    except (ValueError, json.JSONDecodeError) as exc:
        raise YouTubeError("yt-dlp returned output that wasn't valid JSON.") from exc


def fetch_playlist(url: str) -> tuple[str, list[YouTubeTrack]]:
    """Return (playlist_title, [YouTubeTrack, ...]) for a YouTube playlist URL.

    Entries with no usable title are skipped. Private/deleted videos that
    yt-dlp can't resolve are skipped rather than aborting the whole run.
    """
    if not is_youtube_playlist_url(url):
        raise YouTubeError(
            f"Not a YouTube playlist URL (need a youtube.com/... ?list= link): {url}"
        )

    data = _run_ytdlp(url)
    playlist_title = (data.get("title") or "YouTube Playlist").strip()
    raw_entries = data.get("entries") or []
    if len(raw_entries) > _MAX_ENTRIES:
        raise YouTubeError(
            f"Playlist has {len(raw_entries)} entries (cap is {_MAX_ENTRIES})."
        )

    tracks: list[YouTubeTrack] = []
    for e in raw_entries:
        if not isinstance(e, dict):
            continue
        rt = (e.get("title") or "").strip()
        # yt-dlp marks unavailable videos with placeholder titles.
        if not rt or rt.lower() in ("[private video]", "[deleted video]",
                                    "[unavailable video]"):
            continue
        uploader = (e.get("uploader") or e.get("channel")
                    or e.get("uploader_id") or "").strip()
        title, artist = split_artist_title(rt, uploader)
        if not title:
            continue
        tracks.append(YouTubeTrack(raw_title=rt, title=title, artist=artist))

    return playlist_title, tracks
