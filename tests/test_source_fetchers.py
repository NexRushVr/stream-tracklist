"""Coverage for the vodvod / Kick API fetchers — including the safety
filters added during hardening. urllib.request.urlopen is mocked so the
suite stays offline.
"""
import io
import json
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from src import source


@contextmanager
def mock_urlopen(payload):
    """Yield a mock that returns the given JSON-serializable payload."""
    body = json.dumps(payload).encode()

    class _Resp(io.BytesIO):
        def __enter__(self_inner):
            return self_inner
        def __exit__(self_inner, *a):
            return False

    def fake(req, timeout=15):
        return _Resp(body)

    with patch("src.source.urllib.request.urlopen", side_effect=fake) as m:
        yield m


# ----- vodvod fetcher -----------------------------------------------------

def test_vodvod_happy_path():
    payload = [
        {
            "Link": "/m3u8/abc/123/index.m3u8",
            "Metadata": {"TitleAtStart": "Friday Stream", "StartTime": "2026-05-12T20:00:00Z"},
        },
        {
            "Link": "/m3u8/abc/124/index.m3u8",
            "Metadata": {"TitleAtStart": "Saturday Stream", "StartTime": "2026-05-13T20:00:00Z"},
        },
    ]
    with mock_urlopen(payload):
        vods = source._fetch_vodvod_vods("https://vodvod.top/channels/@eevi")
    assert len(vods) == 2
    assert vods[0].file_name == "eevi_2026-05-12"
    assert vods[0].display_title == "Friday Stream"
    assert vods[0].m3u8_url == "https://api.vodvod.top/m3u8/abc/123/index.m3u8"


def test_vodvod_skips_entries_with_no_link():
    payload = [
        {"Link": "", "Metadata": {"TitleAtStart": "Empty"}},
        {"Link": "/m3u8/x/1.m3u8", "Metadata": {"TitleAtStart": "Real", "StartTime": "2026-05-13T..."}},
    ]
    with mock_urlopen(payload):
        vods = source._fetch_vodvod_vods("https://vodvod.top/channels/@eevi")
    assert len(vods) == 1


def test_vodvod_handles_missing_metadata():
    payload = [
        {"Link": "/m3u8/x/1.m3u8", "Metadata": {}},  # no title, no start_time
    ]
    with mock_urlopen(payload):
        vods = source._fetch_vodvod_vods("https://vodvod.top/channels/@eevi")
    assert len(vods) == 1
    assert vods[0].file_name == "eevi_unknown"
    # display_title falls back to file_name when title is missing
    assert vods[0].display_title == "eevi_unknown"


def test_vodvod_raises_on_empty_response():
    with mock_urlopen([]):
        with pytest.raises(RuntimeError, match="No VODs"):
            source._fetch_vodvod_vods("https://vodvod.top/channels/@eevi")


# ----- kick fetcher -------------------------------------------------------

def test_kick_skips_live_streams():
    payload = [
        {"is_live": True, "source": "https://cdn.kick.com/live.m3u8", "session_title": "LIVE"},
        {"is_live": False, "source": "https://cdn.kick.com/vod.m3u8",
         "session_title": "Last Night", "start_time": "2026-05-13T20:00:00Z"},
    ]
    with mock_urlopen(payload):
        vods = source._fetch_kick_vods("https://kick.com/abehamm")
    assert len(vods) == 1
    assert vods[0].display_title == "Last Night"


def test_kick_rejects_non_http_source_url():
    """Hostile / compromised Kick API can't trick ffmpeg into reading file://."""
    payload = [
        {"is_live": False, "source": "file:///etc/passwd",
         "session_title": "Sneaky", "start_time": "2026-05-13T20:00:00Z"},
        {"is_live": False, "source": "https://cdn.kick.com/real.m3u8",
         "session_title": "Real", "start_time": "2026-05-13T20:00:00Z"},
    ]
    with mock_urlopen(payload):
        vods = source._fetch_kick_vods("https://kick.com/abehamm")
    assert len(vods) == 1
    assert vods[0].m3u8_url == "https://cdn.kick.com/real.m3u8"


def test_kick_skips_entries_with_no_source():
    payload = [
        {"is_live": False, "source": None, "session_title": "Empty"},
        {"is_live": False, "source": "", "session_title": "Empty2"},
        {"is_live": False, "source": "https://cdn.kick.com/real.m3u8",
         "session_title": "Real", "start_time": "2026-05-13T20:00:00Z"},
    ]
    with mock_urlopen(payload):
        vods = source._fetch_kick_vods("https://kick.com/abehamm")
    assert len(vods) == 1


def test_kick_raises_on_empty_response():
    with mock_urlopen([]):
        with pytest.raises(RuntimeError, match="No VODs"):
            source._fetch_kick_vods("https://kick.com/abehamm")


def test_kick_url_without_handle_raises():
    with pytest.raises(ValueError):
        source._fetch_kick_vods("https://kick.com/")


# ----- response-size cap --------------------------------------------------

def test_oversize_response_is_rejected():
    """A hostile/MITMed endpoint can't stream gigabytes into json.loads."""
    huge = b"[" + b"0," * (source._MAX_API_RESPONSE) + b"0]"

    class _Resp(io.BytesIO):
        def __enter__(self_inner): return self_inner
        def __exit__(self_inner, *a): return False

    def fake(req, timeout=15):
        return _Resp(huge)

    with patch("src.source.urllib.request.urlopen", side_effect=fake):
        with pytest.raises(RuntimeError):
            source._fetch_vodvod_vods("https://vodvod.top/channels/@eevi")
