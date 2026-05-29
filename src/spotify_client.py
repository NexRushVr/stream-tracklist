from __future__ import annotations

import csv
import glob
import os
import re
import time

try:
    import spotipy
    from spotipy.oauth2 import SpotifyClientCredentials, SpotifyOAuth
except ImportError:  # CI / tests can import this module without the spotipy stack
    spotipy = None  # type: ignore[assignment]
    SpotifyClientCredentials = None  # type: ignore[assignment]
    SpotifyOAuth = None  # type: ignore[assignment]


def _require_spotipy() -> None:
    if spotipy is None:
        raise RuntimeError(
            "spotipy is not installed. Run `pip install -r requirements.txt`."
        )

REPO_URL = "github.com/NexRushVr/stream-tracklist"
DEFAULT_DESCRIPTION = f"Built by stream-tracklist ({REPO_URL})."

# Spotify caps playlist descriptions at 300 chars. We keep ours well under.
DESCRIPTION_MAX = 280


def build_playlist_description(streamer_url: str | None = None) -> str:
    """Compose a playlist description that always credits the tool, optionally
    linking the streamer's source platform (Twitch / Kick)."""
    if streamer_url:
        text = f"Songs from {streamer_url}'s streams. Built by stream-tracklist ({REPO_URL})."
    else:
        text = DEFAULT_DESCRIPTION
    return text[:DESCRIPTION_MAX]

_search_client: spotipy.Spotify | None = None
_oauth_client: spotipy.Spotify | None = None


def _get_search_client() -> spotipy.Spotify:
    global _search_client
    if _search_client is None:
        # Only the *construction* path needs the library; an already-set client
        # (real or test-injected) is returned without requiring spotipy.
        _require_spotipy()
        auth = SpotifyClientCredentials(
            client_id=os.environ["SPOTIFY_CLIENT_ID"],
            client_secret=os.environ["SPOTIFY_CLIENT_SECRET"],
        )
        # retries=0 / status_retries=0 stops spotipy from honoring a 429
        # Retry-After by sleeping *inside* the call — Spotify's daily-quota
        # Retry-After can be ~19h, which would silently hang an entire batch.
        # We'd rather get the exception immediately and degrade gracefully.
        _search_client = spotipy.Spotify(
            auth_manager=auth, retries=0, status_retries=0, backoff_factor=0
        )
    return _search_client


def _get_oauth_client() -> spotipy.Spotify:
    global _oauth_client
    if _oauth_client is None:
        _require_spotipy()
        auth = SpotifyOAuth(
            client_id=os.environ["SPOTIFY_CLIENT_ID"],
            client_secret=os.environ["SPOTIFY_CLIENT_SECRET"],
            redirect_uri=os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback"),
            scope="playlist-modify-public playlist-modify-private playlist-read-private playlist-read-collaborative",
            open_browser=True,
            cache_path=".spotify_token_cache",
        )
        _oauth_client = spotipy.Spotify(auth_manager=auth)
    return _oauth_client


# Cache of prior (title, artist) -> (url, uri) lookups, primed from existing
# CSVs at startup. Burns a few thousand redundant Spotify search calls per run
# when DJs replay tracks across sets — which is what blew the daily quota.
_search_cache: dict[tuple[str, str], tuple[str | None, str | None]] = {}
_search_cache_loaded = False

_TRACK_URL_RE = re.compile(r"^https://open\.spotify\.com/track/([A-Za-z0-9]{22})$")


def _cache_key(title: str, artist: str) -> tuple[str, str]:
    return (title.strip().lower(), artist.strip().lower())


def prime_search_cache(output_dir: str = ".", include_negatives: bool = True) -> int:
    """Scan output_dir for *_songs.csv and populate the search cache. Real
    track URLs become positive hits; search-fallback URLs become negatives
    (unless include_negatives is False — backfill skips negatives so previously
    unresolved rows can be retried). Idempotent. Returns total cache size."""
    global _search_cache_loaded
    if _search_cache_loaded:
        return len(_search_cache)
    pattern = os.path.join(output_dir, "*_songs.csv")
    for path in glob.glob(pattern):
        try:
            with open(path, encoding="utf-8", newline="") as f:
                for row in csv.DictReader(f):
                    title = (row.get("Title") or "").lstrip("'")
                    artist = (row.get("Artist") or "").lstrip("'")
                    spotify = row.get("Spotify") or ""
                    if not title or not artist:
                        continue
                    key = _cache_key(title, artist)
                    if key in _search_cache:
                        continue
                    m = _TRACK_URL_RE.match(spotify)
                    if m:
                        _search_cache[key] = (spotify, f"spotify:track:{m.group(1)}")
                    elif "/search/" in spotify and include_negatives:
                        _search_cache[key] = (None, None)
        except (OSError, csv.Error):
            continue
    _search_cache_loaded = True
    return len(_search_cache)


# Set once Spotify returns a 429 (rate/daily-quota limit). The rest of the run
# then skips search entirely instead of issuing hundreds of doomed calls —
# unresolved tracks fall back to a search URL and can be filled in later with
# --backfill-spotify once the quota window resets.
_quota_exhausted = False


def quota_exhausted() -> bool:
    return _quota_exhausted


def reset_quota_flag() -> None:
    """Clear the quota flag — used by tests and at the start of a backfill run."""
    global _quota_exhausted
    _quota_exhausted = False


def _is_rate_limit(exc: Exception) -> bool:
    status = getattr(exc, "http_status", None)
    if status == 429:
        return True
    text = str(exc).lower()
    return "429" in text or "rate/request limit" in text or "too many requests" in text


def search_track(
    title: str, artist: str, isrc: str = ""
) -> tuple[str | None, str | None]:
    """Return (spotify_track_url, spotify_track_uri) or (None, None) on failure.

    When `isrc` is supplied (from Shazam), an exact `isrc:` query is tried first —
    it's deterministic and resolves remixes/bootlegs that fuzzy title/artist
    search misses. Falls back to fuzzy queries otherwise.
    """
    global _quota_exhausted
    key = _cache_key(title, artist)
    if key in _search_cache:
        return _search_cache[key]
    if _quota_exhausted:
        return None, None

    sp = _get_search_client()

    queries = []
    if isrc:
        queries.append(f"isrc:{isrc}")
    queries.append(f"track:{title} artist:{artist}")
    queries.append(f"{artist} {title}")

    # Distinguish "Spotify answered, no items" (cache as a permanent negative)
    # from "the call errored" (transient — never poison the cache).
    confirmed_no_match = False
    for q in queries:
        try:
            results = sp.search(q=q, type="track", limit=1)
            items = results["tracks"]["items"]
            if items:
                track = items[0]
                result = (track["external_urls"]["spotify"], track["uri"])
                _search_cache[key] = result
                return result
            confirmed_no_match = True
        except Exception as exc:
            if _is_rate_limit(exc):
                if not _quota_exhausted:
                    print("  ⚠ Spotify rate/quota limit hit — skipping further "
                          "lookups this run (re-run with --backfill-spotify later)")
                _quota_exhausted = True
                return None, None
            continue

    if confirmed_no_match:
        _search_cache[key] = (None, None)
    return None, None


def credentials_available() -> bool:
    return bool(os.environ.get("SPOTIFY_CLIENT_ID") and os.environ.get("SPOTIFY_CLIENT_SECRET"))


def ensure_oauth_token_fresh(buffer_seconds: int = 600) -> None:
    """Refresh the OAuth access token if it expires within `buffer_seconds`.

    Why: spotipy auto-refreshes per-request, but long scans can leave the
    cached token expired between API calls. The check uses the cached
    `expires_at` directly — adapts to whatever lifetime Spotify hands out
    (the OAuth flow can return 1h or up to 24h depending on the auth path).

    Prints the current expiry status on every call so it's visible in logs
    that the check ran (not just when an actual refresh fires).
    """
    sp = _get_oauth_client()
    auth = sp.auth_manager
    if not isinstance(auth, SpotifyOAuth):
        return

    token_info = None
    if hasattr(auth, "cache_handler") and auth.cache_handler:
        try:
            token_info = auth.cache_handler.get_cached_token()
        except Exception:
            pass
    if token_info is None and hasattr(auth, "get_cached_token"):
        try:
            token_info = auth.get_cached_token()
        except Exception:
            pass
    if not token_info:
        print("  (Spotify OAuth: no cached token yet)")
        return

    expires_at = token_info.get("expires_at", 0)
    remaining = expires_at - int(time.time())
    mm, ss = divmod(max(remaining, 0), 60)

    if remaining >= buffer_seconds:
        print(f"  (Spotify OAuth: {mm}m{ss:02d}s remaining — no refresh needed)")
        return

    refresh_token = token_info.get("refresh_token")
    if not refresh_token:
        print(f"  (Spotify OAuth: {mm}m{ss:02d}s remaining — no refresh_token in cache, skipping)")
        return
    try:
        auth.refresh_access_token(refresh_token)
        print(f"  (Spotify OAuth: had {mm}m{ss:02d}s left — refreshed)")
    except Exception as exc:
        print(f"  (Spotify OAuth: refresh failed: {exc})")


def create_playlist(
    name: str,
    track_uris: list[str],
    public: bool = True,
    description: str | None = None,
) -> str:
    """Create a Spotify playlist and add tracks. Returns the playlist URL."""
    sp = _get_oauth_client()
    desc = description if description is not None else DEFAULT_DESCRIPTION

    playlist = sp._post(
        "me/playlists",
        payload={
            "name": name,
            "public": public,
            "description": desc[:DESCRIPTION_MAX],
        },
    )
    playlist_id = playlist["id"]

    for i in range(0, len(track_uris), 100):
        sp.playlist_add_items(playlist_id, track_uris[i:i + 100])

    return playlist["external_urls"]["spotify"]


def find_playlist_by_name(name: str) -> tuple[str | None, str | None]:
    """Search the current user's playlists for one matching `name` (case-insensitive).
    Returns (playlist_id, playlist_url) or (None, None)."""
    sp = _get_oauth_client()
    target = name.strip().lower()
    offset = 0
    while True:
        page = sp.current_user_playlists(limit=50, offset=offset)
        items = page.get("items") or []
        for pl in items:
            if (pl.get("name") or "").strip().lower() == target:
                return pl["id"], pl["external_urls"]["spotify"]
        if page.get("next"):
            offset += len(items)
        else:
            return None, None


def find_or_create_playlist(
    name: str,
    public: bool = True,
    description: str | None = None,
) -> tuple[str, str]:
    """Return (playlist_id, playlist_url) for a playlist with this name —
    re-using the user's existing one if present, else creating a fresh empty one.
    The description is refreshed on every call so re-runs keep it in sync."""
    sp = _get_oauth_client()
    desc = (description if description is not None else DEFAULT_DESCRIPTION)[:DESCRIPTION_MAX]
    pid, purl = find_playlist_by_name(name)
    if pid:
        try:
            sp.playlist_change_details(pid, description=desc)
        except Exception:
            pass
        return pid, purl
    playlist = sp._post(
        "me/playlists",
        payload={
            "name": name,
            "public": public,
            "description": desc,
        },
    )
    return playlist["id"], playlist["external_urls"]["spotify"]


def playlist_track_uris(playlist_id: str) -> set[str]:
    """Return the full set of track URIs already in a playlist."""
    sp = _get_oauth_client()
    uris: set[str] = set()
    offset = 0
    while True:
        page = sp.playlist_items(
            playlist_id, fields="items(track(uri)),next", limit=100, offset=offset
        )
        items = page.get("items") or []
        for it in items:
            track = it.get("track") or {}
            uri = track.get("uri")
            if uri:
                uris.add(uri)
        if page.get("next"):
            offset += len(items)
        else:
            return uris


def add_tracks_dedup(
    playlist_id: str,
    track_uris: list[str],
    existing: set[str] | None = None,
) -> int:
    """Add `track_uris` to the playlist, skipping any already present.
    Returns the number of new tracks added.

    If `existing` is provided, it is used as the truth and mutated in place
    with newly-added URIs — lets a caller in a multi-VOD loop fetch the
    current playlist contents once and reuse the set, instead of paying for
    a paginated fetch per VOD.
    """
    if not track_uris:
        return 0
    sp = _get_oauth_client()
    if existing is None:
        existing = playlist_track_uris(playlist_id)
    new = [u for u in dict.fromkeys(track_uris) if u not in existing]
    for i in range(0, len(new), 100):
        sp.playlist_add_items(playlist_id, new[i:i + 100])
    existing.update(new)
    return len(new)


# Spotify track IDs are exactly 22 base62 characters.
_SPOTIFY_ID_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


def track_url_to_uri(url: str) -> str | None:
    """Convert https://open.spotify.com/track/<id>[?...] to spotify:track:<id>.
    Returns None on malformed input (missing id, wrong length, bad chars)."""
    if not url:
        return None
    marker = "/track/"
    idx = url.find(marker)
    if idx < 0:
        return None
    track_id = url[idx + len(marker):].split("?", 1)[0].split("/", 1)[0].strip()
    if len(track_id) != 22 or any(c not in _SPOTIFY_ID_CHARS for c in track_id):
        return None
    return f"spotify:track:{track_id}"


