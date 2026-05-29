"""Tests for the Spotify search cache, ISRC-first lookup, 429 quota degrade,
and the --backfill-spotify CSV-rewrite path. These don't touch the network —
the search client is replaced with an in-memory fake."""
import csv
import os

import pytest

from src import spotify_client


class FakeSearchClient:
    """Stand-in for spotipy.Spotify. Records queries; returns a track when the
    query string is in `mapping`, else empty. Raises `raise_exc` if set."""

    def __init__(self, mapping=None, raise_exc=None):
        self.queries = []
        self.mapping = mapping or {}
        self.raise_exc = raise_exc

    def search(self, q, type="track", limit=1):
        self.queries.append(q)
        if self.raise_exc is not None:
            raise self.raise_exc
        if q in self.mapping:
            tid = self.mapping[q]
            return {"tracks": {"items": [{
                "external_urls": {"spotify": f"https://open.spotify.com/track/{tid}"},
                "uri": f"spotify:track:{tid}",
            }]}}
        return {"tracks": {"items": []}}


class RateLimitError(Exception):
    http_status = 429


@pytest.fixture(autouse=True)
def _reset_state():
    """Each test starts with a clean cache / quota flag / no client."""
    spotify_client._search_cache.clear()
    spotify_client._search_cache_loaded = False
    spotify_client._search_client = None
    spotify_client.reset_quota_flag()
    yield
    spotify_client._search_cache.clear()
    spotify_client._search_cache_loaded = False
    spotify_client._search_client = None
    spotify_client.reset_quota_flag()


def test_isrc_query_tried_first():
    fake = FakeSearchClient(mapping={"isrc:QT2VB2507605": "abc123"})
    spotify_client._search_client = fake
    url, uri = spotify_client.search_track("Nirvana", "Sickick", isrc="QT2VB2507605")
    assert url == "https://open.spotify.com/track/abc123"
    assert uri == "spotify:track:abc123"
    assert fake.queries == ["isrc:QT2VB2507605"]  # resolved on the first, exact query


def test_fuzzy_fallback_when_no_isrc():
    fake = FakeSearchClient(mapping={"artist x title y": "zzz"})
    spotify_client._search_client = fake
    url, _ = spotify_client.search_track("title y", "artist x")
    assert url.endswith("/track/zzz")
    # exact track:/artist: query first (miss), then broadened query (hit)
    assert fake.queries == ["track:title y artist:artist x", "artist x title y"]


def test_429_sets_quota_flag_and_short_circuits():
    fake = FakeSearchClient(raise_exc=RateLimitError())
    spotify_client._search_client = fake
    url, uri = spotify_client.search_track("Song", "Artist", isrc="CODE")
    assert (url, uri) == (None, None)
    assert spotify_client.quota_exhausted() is True
    assert len(fake.queries) == 1  # bailed on the very first 429, didn't keep trying

    # A subsequent call must not issue any further requests.
    spotify_client.search_track("Other", "Band")
    assert len(fake.queries) == 1


def test_quota_miss_not_cached_as_negative():
    fake = FakeSearchClient(raise_exc=RateLimitError())
    spotify_client._search_client = fake
    spotify_client.search_track("Song", "Artist")
    # The (title, artist) must NOT be in the cache — a quota miss is transient
    # and must stay retryable by --backfill-spotify.
    assert spotify_client._cache_key("Song", "Artist") not in spotify_client._search_cache


def test_confirmed_no_match_cached_as_negative():
    fake = FakeSearchClient(mapping={})  # answers, but nothing matches
    spotify_client._search_client = fake
    spotify_client.search_track("Nope", "Nobody")
    assert spotify_client._search_cache[spotify_client._cache_key("Nope", "Nobody")] == (None, None)


def test_cache_hit_avoids_network():
    # Pre-seed a positive; the client should never be consulted.
    spotify_client._search_cache[spotify_client._cache_key("Cached", "Act")] = (
        "https://open.spotify.com/track/cc", "spotify:track:cc")

    class Boom:
        def search(self, *a, **k):
            raise AssertionError("network must not be hit on a cache hit")

    spotify_client._search_client = Boom()
    url, uri = spotify_client.search_track("Cached", "Act")
    assert url.endswith("/track/cc")


def _write_csv(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Timestamp", "Title", "Artist", "Spotify", "YouTube", "ISRC"])
        w.writerows(rows)


def test_prime_cache_skips_negatives_when_requested(tmp_path):
    csv_path = tmp_path / "x_songs.csv"
    _write_csv(csv_path, [
        ["00:00:10", "Real", "Band", "https://open.spotify.com/track/" + "a" * 22, "https://yt", "ISRC1"],
        ["00:00:20", "Missing", "Other", "https://open.spotify.com/search/Other%20Missing", "https://yt", "ISRC2"],
    ])
    n = spotify_client.prime_search_cache(str(tmp_path), include_negatives=False)
    assert n == 1  # only the positive
    assert spotify_client._cache_key("Real", "Band") in spotify_client._search_cache
    assert spotify_client._cache_key("Missing", "Other") not in spotify_client._search_cache


def test_backfill_rewrites_unresolved_rows(tmp_path):
    import stream_songs

    csv_path = tmp_path / "moonbuvr_2026_05_20_songs.csv"
    _write_csv(csv_path, [
        ["00:00:10", "Resolved", "A", "https://open.spotify.com/track/" + "k" * 22, "https://yt", ""],
        ["00:00:20", "Needsfix", "B", "https://open.spotify.com/search/B%20Needsfix", "https://yt", "ISRCX"],
    ])

    # ISRC lookup resolves the unresolved row.
    fake = FakeSearchClient(mapping={"isrc:ISRCX": "newid"})
    spotify_client._search_client = fake

    # No handle → no playlist update (avoids OAuth/network).
    stream_songs._cmd_backfill([], str(tmp_path), str(tmp_path / "logs"), public=True)

    with open(csv_path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["Spotify"].endswith("/track/" + "k" * 22)  # untouched
    assert rows[1]["Spotify"] == "https://open.spotify.com/track/newid"  # resolved
    # ISRC-first query was used.
    assert fake.queries == ["isrc:ISRCX"]
