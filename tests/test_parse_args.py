"""Coverage for the argparse mutual-exclusion and validation logic in
`stream_songs.parse_args`. None of this was exercised before — typos in any
flag-routing branch would have shipped silently.
"""
import os
import sys

import pytest

import stream_songs


def _run(*argv, monkeypatch=None, env=None):
    """Helper: invoke parse_args with a synthesized sys.argv and optional env."""
    monkeypatch.setattr(sys, "argv", ["stream_songs.py", *argv])
    if env is not None:
        for k, v in env.items():
            if v is None:
                monkeypatch.delenv(k, raising=False)
            else:
                monkeypatch.setenv(k, v)
    return stream_songs.parse_args()


def _no_spotify(monkeypatch):
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.delenv("SPOTIFY_CLIENT_SECRET", raising=False)


# ----- happy paths --------------------------------------------------------

def test_parse_args_accepts_single_source(monkeypatch):
    _no_spotify(monkeypatch)
    args = _run("https://example.com/foo.m3u8", monkeypatch=monkeypatch)
    assert args.source_input == "https://example.com/foo.m3u8"
    assert args.streamers is None
    assert not args.create_playlist


def test_parse_args_streamers_implies_all(monkeypatch):
    # streamer-mode requires Spotify creds.
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "x")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "y")
    args = _run("--streamer-mode", "--streamers", "eevi", "kick:abehamm",
                monkeypatch=monkeypatch)
    assert args.streamers == ["eevi", "kick:abehamm"]
    assert args.all is True


# ----- mutual-exclusion errors -------------------------------------------

def test_parse_args_streamers_without_streamer_mode_errors(monkeypatch):
    _no_spotify(monkeypatch)
    with pytest.raises(SystemExit):
        _run("--streamers", "eevi", monkeypatch=monkeypatch)


def test_parse_args_source_required_without_streamers(monkeypatch):
    _no_spotify(monkeypatch)
    with pytest.raises(SystemExit):
        _run(monkeypatch=monkeypatch)


def test_parse_args_minimum_interval(monkeypatch):
    _no_spotify(monkeypatch)
    with pytest.raises(SystemExit):
        _run("--interval", "10", "https://example.com/foo.m3u8",
             monkeypatch=monkeypatch)


def test_parse_args_minimum_clip_duration(monkeypatch):
    _no_spotify(monkeypatch)
    with pytest.raises(SystemExit):
        _run("--clip-duration", "1", "https://example.com/foo.m3u8",
             monkeypatch=monkeypatch)


def test_parse_args_create_playlist_requires_spotify_creds(monkeypatch):
    _no_spotify(monkeypatch)
    with pytest.raises(SystemExit):
        _run("--create-playlist", "https://example.com/foo.m3u8",
             monkeypatch=monkeypatch)


def test_parse_args_rebuild_requires_spotify_creds(monkeypatch):
    _no_spotify(monkeypatch)
    with pytest.raises(SystemExit):
        _run("--rebuild", "eevi", monkeypatch=monkeypatch)


def test_parse_args_all_requires_vodvod_or_kick_url(monkeypatch):
    _no_spotify(monkeypatch)
    with pytest.raises(SystemExit):
        _run("--all", "https://example.com/random/page",
             monkeypatch=monkeypatch)


def test_parse_args_all_accepts_kick_url(monkeypatch):
    _no_spotify(monkeypatch)
    args = _run("--all", "https://kick.com/abehamm", monkeypatch=monkeypatch)
    assert args.all is True


# ----- inspection commands short-circuit before source validation -------

def test_parse_args_list_streamers_no_source_ok(monkeypatch):
    _no_spotify(monkeypatch)
    args = _run("--list-streamers", monkeypatch=monkeypatch)
    assert args.list_streamers is True


def test_parse_args_show_streamer_no_source_ok(monkeypatch):
    _no_spotify(monkeypatch)
    args = _run("--show-streamer", "eevi", monkeypatch=monkeypatch)
    assert args.show_streamer == "eevi"
