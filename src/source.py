import json
import os
import urllib.request
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlparse


class MediaType(Enum):
    LOCAL_MP4 = "local_mp4"
    M3U8 = "m3u8"


@dataclass
class VodInfo:
    m3u8_url: str
    file_name: str      # safe for use as output filename stem
    display_title: str  # original stream title for display/playlist name


def _netloc_matches(netloc: str, *suffixes: str) -> bool:
    """Exact-or-subdomain host match. Rejects substring tricks like
    'vodvod.top.attacker.com' or 'evilvodvod.top'."""
    host = netloc.split(":", 1)[0].lower()  # strip any port
    for s in suffixes:
        if host == s or host.endswith("." + s):
            return True
    return False


def _is_safe_m3u8_url(url: str) -> bool:
    """Permit only https:// (or http:// for the rare plain-HTTP test stream)
    URLs with a non-empty host. Blocks file://, data://, javascript:, and
    similar protocols that ffmpeg would otherwise follow."""
    try:
        p = urlparse(url)
    except Exception:
        return False
    return p.scheme in ("http", "https") and bool(p.netloc)


def resolve(source: str) -> tuple[str, str, MediaType]:
    """Return (resolved_url, source_name, media_type) for any supported input."""
    parsed = urlparse(source)

    # Single-letter schemes are Windows drive letters ("C:\path\file.mp4"), not URLs.
    is_url = parsed.scheme not in ("", "file") and len(parsed.scheme) > 1

    if not is_url:
        path = source.replace("/", os.sep)
        if path.lower().endswith(".mp4") and os.path.isfile(path):
            name = os.path.splitext(os.path.basename(path))[0]
            return path, name, MediaType.LOCAL_MP4
        raise ValueError(f"File not found or not an MP4: {source}")

    if _netloc_matches(parsed.netloc, "vodvod.top"):
        vods = _fetch_vodvod_vods(source)
        if not vods:
            raise RuntimeError("No VODs found for that channel")
        v = vods[0]
        return v.m3u8_url, v.display_title, MediaType.M3U8

    if _netloc_matches(parsed.netloc, "kick.com"):
        vods = _fetch_kick_vods(source)
        if not vods:
            raise RuntimeError("No VODs found for that Kick channel")
        v = vods[0]
        return v.m3u8_url, v.display_title, MediaType.M3U8

    if ".m3u8" in parsed.path or "/hls/" in parsed.path:
        if not _is_safe_m3u8_url(source):
            raise ValueError(f"Refusing unsafe m3u8 URL scheme: {source}")
        stem = parsed.path.rstrip("/").rsplit("/", 1)[-1].replace(".m3u8", "")
        name = f"{parsed.netloc}_{stem}".replace(".", "_")
        return source, name, MediaType.M3U8

    raise ValueError(
        f"Unrecognized source: {source}\n"
        "Supported: local .mp4 path, .m3u8 URL, vodvod.top URL, or kick.com URL"
    )


def list_vods(url: str) -> list[VodInfo]:
    """Return all VODs for a vodvod.top or kick.com channel URL, newest first."""
    parsed = urlparse(url)
    if _netloc_matches(parsed.netloc, "vodvod.top"):
        return _fetch_vodvod_vods(url)
    if _netloc_matches(parsed.netloc, "kick.com"):
        return _fetch_kick_vods(url)
    raise ValueError("--all only works with vodvod.top or kick.com channel URLs")


# Cap responses from third-party APIs at 5MB. Real responses are well under
# 100KB; a 5MB ceiling stops a hostile / MITMed endpoint from streaming
# gigabytes into json.loads.
_MAX_API_RESPONSE = 5 * 1024 * 1024


def _http_get_json(req: urllib.request.Request) -> object:
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = resp.read(_MAX_API_RESPONSE + 1)
    if len(body) > _MAX_API_RESPONSE:
        raise RuntimeError(f"API response exceeded {_MAX_API_RESPONSE} bytes")
    return json.loads(body)


def _fetch_vodvod_vods(url: str) -> list[VodInfo]:
    """Call vodvod.top API and return all VODs as VodInfo objects."""
    parsed = urlparse(url)
    channel = parsed.path.rstrip("/").rsplit("/", 1)[-1]  # e.g. "@eevi"
    handle = channel.lstrip("@")

    api_url = f"https://api.vodvod.top/channels/{channel}"
    req = urllib.request.Request(
        api_url,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
    )
    try:
        raw = _http_get_json(req)
    except Exception as exc:
        raise RuntimeError(
            f"Could not reach vodvod.top API for {channel}: {exc}\n"
            "Tip: pass the .m3u8 URL directly instead."
        ) from exc

    if not raw:
        raise RuntimeError(f"No VODs found for {channel}")

    vods = []
    for entry in raw:
        link = entry.get("Link", "")
        if not link:
            continue
        m3u8_url = f"https://api.vodvod.top{link}"
        if not _is_safe_m3u8_url(m3u8_url):
            # vodvod is the trusted API; this is belt-and-braces against a
            # malformed Link field that produces a non-http URL.
            continue
        meta = entry.get("Metadata", {})
        title = meta.get("TitleAtStart", "")
        start_time = meta.get("StartTime", "")
        date = start_time[:10] if start_time else "unknown"  # "2026-04-23"
        file_name = f"{handle}_{date}"
        vods.append(VodInfo(
            m3u8_url=m3u8_url,
            file_name=file_name,
            display_title=title or file_name,
        ))

    return vods


def _fetch_kick_vods(url: str) -> list[VodInfo]:
    """Call Kick's public channel videos API and return completed VODs as VodInfo objects.

    Kick is fronted by Cloudflare; we use Firefox-like headers and a Referer to
    avoid getting bot-challenged. Live streams (is_live=True) are skipped.
    """
    parsed = urlparse(url)
    # Accept both kick.com/<handle> and kick.com/<handle>/videos
    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        raise ValueError("Could not parse handle from Kick URL")
    handle = parts[0].lstrip("@").strip()

    api_url = f"https://kick.com/api/v2/channels/{handle}/videos"
    req = urllib.request.Request(
        api_url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "identity",
            "Referer": f"https://kick.com/{handle}/videos",
            "Origin": "https://kick.com",
        },
    )
    try:
        raw = _http_get_json(req)
    except Exception as exc:
        raise RuntimeError(
            f"Could not reach Kick API for {handle}: {exc}\n"
            "Tip: Cloudflare may be challenging us. Try again, or pass the .m3u8 URL directly."
        ) from exc

    if not raw:
        raise RuntimeError(f"No VODs found for Kick channel {handle}")

    vods = []
    for entry in raw:
        if entry.get("is_live"):
            continue
        m3u8 = entry.get("source")
        if not m3u8 or not _is_safe_m3u8_url(m3u8):
            # Kick is fronted by a CDN we don't control; refuse non-http(s)
            # source URLs (file://, data://, etc.) before they reach ffmpeg.
            continue
        title = entry.get("session_title") or ""
        start_time = entry.get("start_time", "")
        date = start_time[:10] if start_time else "unknown"
        file_name = f"{handle}_{date}"
        vods.append(VodInfo(
            m3u8_url=m3u8,
            file_name=file_name,
            display_title=title or file_name,
        ))

    return vods
