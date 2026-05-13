"""Per-streamer log of which VODs have been processed.

A log lives at <log_dir>/<handle>.json and tracks every VOD attempted for
that streamer, the tool version that processed it, and the streamer's
running Spotify playlist (so future runs can skip done VODs and append
new tracks to the same playlist).
"""
import json
import os
import re
import tempfile
from datetime import datetime
from urllib.parse import urlparse

from . import __version__


# Streamer handles are restricted to characters safe across filesystems,
# URLs, and Spotify playlist names. Anything else is rejected — both to
# prevent path traversal (../, /, \) and to keep log filenames predictable.
_HANDLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def is_valid_handle(handle: str | None) -> bool:
    return bool(handle and _HANDLE_RE.match(handle))


def _netloc_matches(netloc: str, *suffixes: str) -> bool:
    """Return True only on exact match or a real subdomain — never substring.
    Rejects e.g. vodvod.top.attacker.com or attacker-vodvod.top."""
    host = netloc.split(":", 1)[0].lower()  # strip any port
    for s in suffixes:
        if host == s or host.endswith("." + s):
            return True
    return False


def extract_handle(url: str) -> str | None:
    """Pull the streamer handle from a vodvod.top or kick.com URL.

    Returns None if the URL isn't a recognized platform or the handle
    contains characters outside [A-Za-z0-9_-]."""
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    if _netloc_matches(parsed.netloc, "vodvod.top"):
        last = parsed.path.rstrip("/").rsplit("/", 1)[-1]
        candidate = last.lstrip("@").strip()
    elif _netloc_matches(parsed.netloc, "kick.com"):
        parts = [p for p in parsed.path.split("/") if p]
        if not parts:
            return None
        candidate = parts[0].lstrip("@").strip()
    else:
        return None
    return candidate if is_valid_handle(candidate) else None


def log_path(log_dir: str, handle: str) -> str:
    if not is_valid_handle(handle):
        raise ValueError(
            f"Invalid streamer handle {handle!r}: only letters, digits, '_' and '-' "
            f"are allowed (max 64 chars, must start with a letter or digit)."
        )
    return os.path.join(log_dir, f"{handle}.json")


def load(log_dir: str, handle: str) -> dict:
    path = log_path(log_dir, handle)
    if not os.path.isfile(path):
        return _empty(handle)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return _empty(handle)
    data.setdefault("handle", handle)
    data.setdefault("tool_version", __version__)
    data.setdefault("playlist_id", None)
    data.setdefault("playlist_url", None)
    data.setdefault("vods", {})
    return data


def save(log_dir: str, handle: str, data: dict) -> None:
    """Atomic write: serialize to a tmp file in the same directory, then
    os.replace. Crash-safe — a Ctrl-C mid-write can't corrupt the log."""
    os.makedirs(log_dir, exist_ok=True)
    path = log_path(log_dir, handle)
    # NamedTemporaryFile in the target dir so os.replace can be atomic across
    # the same filesystem.
    fd, tmp_path = tempfile.mkstemp(prefix=f".{handle}.", suffix=".json.tmp", dir=log_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
    except Exception:
        # Best-effort cleanup of the partial tmp.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def is_processed(data: dict, vod_key: str) -> bool:
    return vod_key in data.get("vods", {})


def record_vod(
    data: dict,
    vod_key: str,
    *,
    display_title: str,
    m3u8_url: str,
    song_count: int,
    tracks_added: int,
) -> None:
    data["vods"][vod_key] = {
        "display_title": display_title,
        "m3u8_url": m3u8_url,
        "processed_at": datetime.now().isoformat(timespec="seconds"),
        "tool_version": __version__,
        "song_count": song_count,
        "tracks_added": tracks_added,
    }


def set_playlist(data: dict, playlist_id: str, playlist_url: str) -> None:
    data["playlist_id"] = playlist_id
    data["playlist_url"] = playlist_url


def _empty(handle: str) -> dict:
    return {
        "handle": handle,
        "tool_version": __version__,
        "playlist_id": None,
        "playlist_url": None,
        "vods": {},
    }
