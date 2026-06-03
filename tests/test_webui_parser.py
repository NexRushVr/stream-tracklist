"""Coverage for the CLI-output-line -> structured-event parser used by the
web UI. Pure-function tests, no Flask import — the module guards Flask so
this file imports cleanly under the pytest-only CI install.
"""
import pytest

from src.webui import parse_line


def test_blank():
    assert parse_line("").get("kind") == "blank"
    assert parse_line("   ").get("kind") == "blank"


@pytest.mark.parametrize("line,expected", [
    # Match (em-dash form — the streamer-mode default)
    (
        "[12/135] 00:22:00  [match] Divine Beast Dancing Lion — Shoi Miyazawa",
        {"kind": "sample", "n": 12, "total": 135, "ts": "00:22:00",
         "status": "match",
         "title": "Divine Beast Dancing Lion", "artist": "Shoi Miyazawa"},
    ),
    # Match (quoted "by" form — print_results / single-VOD)
    (
        '[1/5] 00:00:00  [match] "Get Lucky" by Daft Punk',
        {"kind": "sample", "n": 1, "total": 5, "ts": "00:00:00",
         "status": "match", "title": "Get Lucky", "artist": "Daft Punk"},
    ),
    # No match
    (
        "[13/135] 00:24:00  [no match]",
        {"kind": "sample", "n": 13, "total": 135, "ts": "00:24:00",
         "status": "no match"},
    ),
    # Skip
    (
        "[14/135] 00:26:00  [skip] 403 Forbidden — CDN blocks access",
        {"kind": "sample", "n": 14, "total": 135, "ts": "00:26:00",
         "status": "skip", "detail": "403 Forbidden — CDN blocks access"},
    ),
])
def test_sample_lines(line, expected):
    evt = parse_line(line)
    for k, v in expected.items():
        assert evt[k] == v, f"{k!r}: got {evt.get(k)!r}, expected {v!r}"


def test_vod_header():
    evt = parse_line("VOD 2/5: still so sick, more elden")
    assert evt == {"kind": "vod_start", "n": 2, "total": 5,
                   "title": "still so sick, more elden"}


def test_streamer_header():
    assert parse_line("# [1/2] eevi") == {
        "kind": "streamer_start", "n": 1, "total": 2, "handle": "eevi",
    }


@pytest.mark.parametrize("line", [
    "Playlist URL: https://open.spotify.com/playlist/AAA111",
    'Playlist: "abehamm" — https://open.spotify.com/playlist/AAA111',
    "Streamer playlist: https://open.spotify.com/playlist/AAA111",
    "Playlist created: https://open.spotify.com/playlist/AAA111",
])
def test_playlist_urls(line):
    evt = parse_line(line)
    assert evt["kind"] == "playlist"
    assert evt["url"].startswith("https://open.spotify.com/playlist/")


def test_tracks_added():
    assert parse_line("Added 25 new track(s) (3 already present in playlist)") \
        == {"kind": "tracks_added", "count": 25}


def test_duration():
    assert parse_line("Duration    : 04:28:40") \
        == {"kind": "duration", "value": "04:28:40"}


def test_sample_total():
    evt = parse_line("Sampling 135 timestamps every 120s (20s clips, 5 attempt(s) per slot)...")
    assert evt == {"kind": "sample_total", "total": 135}


def test_vod_summary_full():
    evt = parse_line("Found 15 VOD(s) — 10 already logged, 5 to process")
    assert evt == {"kind": "vod_summary", "total": 15,
                   "skipped": 10, "pending": 5}


def test_vod_summary_bare():
    assert parse_line("Found 5 VOD(s)") == {"kind": "vod_summary", "total": 5}


def test_error():
    evt = parse_line(
        "ERROR appending to streamer playlist: ('Connection aborted.',)"
    )
    assert evt["kind"] == "error"
    assert "Connection aborted" in evt["text"]


def test_fallback_log():
    evt = parse_line("Something unstructured the CLI printed")
    assert evt == {"kind": "log",
                   "text": "Something unstructured the CLI printed"}


# ----- summary update propagation ---------------------------------------

def test_summary_match_accumulates():
    from src.webui import JobManager, Job

    job = Job(id="x", label="t", kind="streamer", cmd=[], started_at=0.0)
    for evt in [
        {"kind": "streamer_start", "n": 1, "total": 2, "handle": "eevi"},
        {"kind": "vod_start", "n": 1, "total": 5, "title": "test"},
        {"kind": "sample_total", "total": 10},
        {"kind": "sample", "n": 3, "total": 10, "ts": "00:06:00",
         "status": "match", "title": "Song A", "artist": "Artist A"},
        {"kind": "sample", "n": 4, "total": 10, "ts": "00:08:00",
         "status": "match", "title": "Song B", "artist": "Artist B"},
        {"kind": "tracks_added", "count": 12},
        {"kind": "playlist", "url": "https://open.spotify.com/playlist/XYZ"},
    ]:
        JobManager._update_summary(job, evt)

    s = job.summary
    assert s["current_streamer"] == "eevi"
    assert s["current_vod"]["title"] == "test"
    assert s["sample_progress"] == {"n": 4, "total": 10}
    assert s["matches_this_vod"] == 2
    assert [m["title"] for m in s["last_matches"]] == ["Song A", "Song B"]
    assert s["total_tracks_added"] == 12
    assert s["playlist_url"].endswith("XYZ")


def test_summary_recent_matches_capped():
    from src.webui import JobManager, Job

    job = Job(id="x", label="t", kind="streamer", cmd=[], started_at=0.0)
    JobManager._update_summary(job, {"kind": "vod_start", "n": 1, "total": 1,
                                     "title": "v"})
    for i in range(30):
        JobManager._update_summary(job, {
            "kind": "sample", "n": i + 1, "total": 30, "ts": "00:00:00",
            "status": "match", "title": f"T{i}", "artist": f"A{i}",
        })
    assert len(job.summary["last_matches"]) == 12
    # tail kept, head dropped
    assert job.summary["last_matches"][-1]["title"] == "T29"
    assert job.summary["last_matches"][0]["title"] == "T18"
