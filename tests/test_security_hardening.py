"""Tests for the hardening fixes added during the public-release prep.

Each test maps to a specific finding from the security / code-quality
agent reviews — keeping them in one file makes the link explicit.
"""
import csv
import json

import pytest

from src import output, source, spotify_client, streamer_log


# ---------- handle validation (path-traversal mitigation) -----------------

def test_handle_validator_accepts_normal_handles():
    assert streamer_log.is_valid_handle("eevi")
    assert streamer_log.is_valid_handle("abehamm")
    assert streamer_log.is_valid_handle("icemiyuki_")
    assert streamer_log.is_valid_handle("a")
    assert streamer_log.is_valid_handle("user-name")
    assert streamer_log.is_valid_handle("a1b2c3")


@pytest.mark.parametrize("bad", [
    "",
    None,
    "../etc/passwd",
    "..",
    "../",
    "evil/handle",
    "evil\\handle",
    "_leading_underscore",  # must start with [A-Za-z0-9]
    "-leading-dash",
    "user name",            # space
    "user.name",            # dot
    "a" * 65,               # too long
    "drop;tablestreams",    # punctuation
    "用户",                 # non-ASCII
])
def test_handle_validator_rejects_unsafe(bad):
    assert not streamer_log.is_valid_handle(bad)


def test_log_path_raises_on_unsafe_handle():
    with pytest.raises(ValueError):
        streamer_log.log_path("logs", "../escape")
    with pytest.raises(ValueError):
        streamer_log.log_path("logs", "")


def test_extract_handle_rejects_traversal_in_url():
    # The path component looks like a handle but contains traversal chars.
    assert streamer_log.extract_handle("https://vodvod.top/channels/@../..") is None
    assert streamer_log.extract_handle("https://kick.com/../../etc/passwd") is None


# ---------- exact-match netloc (substring SSRF mitigation) ----------------

def test_extract_handle_rejects_subdomain_imposters():
    assert streamer_log.extract_handle("https://vodvod.top.attacker.com/channels/@eevi") is None
    assert streamer_log.extract_handle("https://evilvodvod.top/channels/@eevi") is None
    assert streamer_log.extract_handle("https://kick.com.attacker.com/eevi") is None


def test_extract_handle_accepts_real_subdomain():
    # `m.kick.com` would be a real Kick subdomain — accept anything ending in
    # the suffix as long as it isn't a substring trick.
    assert streamer_log.extract_handle("https://m.kick.com/abehamm") == "abehamm"


# ---------- m3u8 URL scheme guard (ffmpeg protocol hardening) -------------

@pytest.mark.parametrize("url,ok", [
    ("https://cdn.example.com/path/file.m3u8", True),
    ("http://cdn.example.com/path/file.m3u8", True),
    ("file:///etc/passwd", False),
    ("data:text/plain;base64,ZXZpbA==", False),
    ("javascript:alert(1)", False),
    ("", False),
    ("ftp://cdn.example.com/path/file.m3u8", False),
])
def test_is_safe_m3u8_url(url, ok):
    assert source._is_safe_m3u8_url(url) is ok


def test_resolve_refuses_file_scheme_m3u8():
    # urlparse needs a host to consider it a URL at all; this synthesizes a
    # URL-shaped string that looks like an m3u8 to the path check.
    with pytest.raises(ValueError):
        source.resolve("javascript:alert(1)/foo/index.m3u8")


# ---------- CSV formula injection guard -----------------------------------

@pytest.mark.parametrize("title,artist", [
    ("=cmd|'/C calc'!A1", "Normal Artist"),
    ("+1234", "Normal Artist"),
    ("Normal", "-IMPORTXML(...)"),
    ("@SUM(A1)", "Normal"),
])
def test_write_csv_defangs_formula_starts(tmp_path, title, artist):
    entry = output.SongEntry(
        timestamp=0, title=title, artist=artist,
        spotify_url=None, spotify_uri=None,
        youtube_url="https://www.youtube.com/results?search_query=x",
    )
    path = tmp_path / "out.csv"
    output.write_csv([entry], str(path))
    with open(path, encoding="utf-8") as f:
        rows = list(csv.reader(f))
    # Title and Artist columns are at index 1 and 2.
    written_title, written_artist = rows[1][1], rows[1][2]
    assert not written_title.startswith(output._CSV_INJECTION_PREFIXES)
    assert not written_artist.startswith(output._CSV_INJECTION_PREFIXES)


def test_write_csv_leaves_normal_titles_alone(tmp_path):
    entry = output.SongEntry(
        timestamp=0, title="Hello World", artist="Some Artist",
        spotify_url=None, spotify_uri=None,
        youtube_url="https://www.youtube.com/results?search_query=x",
    )
    path = tmp_path / "out.csv"
    output.write_csv([entry], str(path))
    with open(path, encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert rows[1][1] == "Hello World"
    assert rows[1][2] == "Some Artist"


# ---------- atomic streamer-log save (no partial writes) ------------------

def test_save_is_atomic_against_crash(tmp_path, monkeypatch):
    """Simulate an exception mid-write and assert the original file is intact."""
    data = streamer_log.load(str(tmp_path), "eevi")
    streamer_log.set_playlist(data, "pid", "https://open.spotify.com/playlist/pid")
    streamer_log.save(str(tmp_path), "eevi", data)
    log_path = tmp_path / "eevi.json"
    original = log_path.read_text(encoding="utf-8")

    # Force json.dump to raise so the tmp file exists but the replace never
    # happens. The original log must be untouched.
    real_dump = json.dump

    def boom(*a, **kw):
        raise RuntimeError("disk full")
    monkeypatch.setattr("src.streamer_log.json.dump", boom)

    with pytest.raises(RuntimeError):
        streamer_log.save(str(tmp_path), "eevi", {"vods": {"new": {}}})

    assert log_path.read_text(encoding="utf-8") == original
    # And no orphan tmp file should remain in the log dir.
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "eevi.json"]
    assert leftovers == [], f"leftover tmp files: {leftovers}"


# ---------- track_url_to_uri rejects malformed IDs ------------------------

def test_track_url_to_uri_rejects_empty_id():
    assert spotify_client.track_url_to_uri("https://open.spotify.com/track/") is None
    assert spotify_client.track_url_to_uri("https://open.spotify.com/track") is None


def test_track_url_to_uri_rejects_wrong_length():
    short = "https://open.spotify.com/track/abc123"
    assert spotify_client.track_url_to_uri(short) is None
    too_long = "https://open.spotify.com/track/" + "a" * 30
    assert spotify_client.track_url_to_uri(too_long) is None


def test_track_url_to_uri_rejects_bad_chars():
    bad = "https://open.spotify.com/track/" + "A" * 21 + "!"  # 22 chars, last invalid
    assert spotify_client.track_url_to_uri(bad) is None


def test_track_url_to_uri_accepts_valid():
    valid_id = "abcDEF123456789012ABCD"  # 22 base62 chars
    url = f"https://open.spotify.com/track/{valid_id}?si=ignored"
    assert spotify_client.track_url_to_uri(url) == f"spotify:track:{valid_id}"


# ---------- playlist description includes streamer + repo link ------------

def test_build_playlist_description_with_streamer_includes_both():
    desc = spotify_client.build_playlist_description("twitch.tv/eevi")
    assert "twitch.tv/eevi" in desc
    assert "stream-tracklist" in desc
    assert "github.com/NexRushVr/stream-tracklist" in desc


def test_build_playlist_description_without_streamer_still_credits_repo():
    desc = spotify_client.build_playlist_description()
    assert "stream-tracklist" in desc
    assert "github.com/NexRushVr/stream-tracklist" in desc


def test_build_playlist_description_under_spotify_limit():
    long_url = "twitch.tv/" + "a" * 300
    desc = spotify_client.build_playlist_description(long_url)
    assert len(desc) <= spotify_client.DESCRIPTION_MAX
