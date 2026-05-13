from __future__ import annotations

import os
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
    _require_spotipy()
    global _search_client
    if _search_client is None:
        auth = SpotifyClientCredentials(
            client_id=os.environ["SPOTIFY_CLIENT_ID"],
            client_secret=os.environ["SPOTIFY_CLIENT_SECRET"],
        )
        _search_client = spotipy.Spotify(auth_manager=auth)
    return _search_client


def _get_oauth_client() -> spotipy.Spotify:
    _require_spotipy()
    global _oauth_client
    if _oauth_client is None:
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


def search_track(title: str, artist: str) -> tuple[str | None, str | None]:
    """Return (spotify_track_url, spotify_track_uri) or (None, None) on failure."""
    sp = _get_search_client()
    try:
        results = sp.search(q=f"track:{title} artist:{artist}", type="track", limit=1)
        items = results["tracks"]["items"]
        if items:
            track = items[0]
            return track["external_urls"]["spotify"], track["uri"]
    except Exception:
        pass

    # Broaden search if exact query fails
    try:
        results = sp.search(q=f"{artist} {title}", type="track", limit=1)
        items = results["tracks"]["items"]
        if items:
            track = items[0]
            return track["external_urls"]["spotify"], track["uri"]
    except Exception:
        pass

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


