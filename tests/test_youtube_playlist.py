"""Coverage for the YouTube-playlist path: URL gating, the title->(artist,
title) parser, and fetch_playlist with yt-dlp mocked out so the suite stays
offline and yt-dlp-free.
"""
from unittest.mock import patch

import pytest

from src import youtube_playlist as yt
from src.youtube_playlist import YouTubeError, split_artist_title


# ----- is_youtube_playlist_url -------------------------------------------

@pytest.mark.parametrize("url", [
    "https://youtube.com/playlist?list=PLabc",
    "https://www.youtube.com/playlist?list=PLabc",
    "https://www.youtube.com/watch?v=xyz&list=PLabc",
    "https://music.youtube.com/playlist?list=PLabc",
    "https://youtu.be/xyz?list=PLabc",
    "http://youtube.com/playlist?list=PLabc",
])
def test_url_accepted(url):
    assert yt.is_youtube_playlist_url(url)


@pytest.mark.parametrize("url", [
    "https://youtube.com/watch?v=xyz",            # no list=
    "https://notyoutube.com/playlist?list=PL",    # substring host
    "https://youtube.com.evil.net/playlist?list=PL",  # subdomain trick
    "https://evil.com/youtube.com?list=PL",
    "ftp://youtube.com/playlist?list=PL",         # bad scheme
    "file:///etc/passwd?list=PL",
    "not a url",
])
def test_url_rejected(url):
    assert not yt.is_youtube_playlist_url(url)


# ----- split_artist_title ------------------------------------------------

@pytest.mark.parametrize("raw,uploader,expected", [
    # plain "Artist - Title"
    ("Daft Punk - Get Lucky", "anyone", ("Get Lucky", "Daft Punk")),
    # unicode en-dash + production junk that must be stripped
    ("Daft Punk – Get Lucky (Official Video)", "x", ("Get Lucky", "Daft Punk")),
    ("Daft Punk - Get Lucky (Official Music Video) [HD]", "x",
     ("Get Lucky", "Daft Punk")),
    # YouTube Music "- Topic" channel: channel IS the artist
    ("Instant Crush", "Daft Punk - Topic", ("Instant Crush", "Daft Punk")),
    # VEVO / Official channel cruft stripped in the fallback path
    ("Some Song", "RihannaVEVO", ("Some Song", "Rihanna")),
    ("Some Song", "Metallica Official", ("Some Song", "Metallica")),
    # leading track number stripped, dash split still works
    ("01. Queen - Bohemian Rhapsody", "x",
     ("Bohemian Rhapsody", "Queen")),
    # pipe separator
    ("Queen | Bohemian Rhapsody", "x", ("Bohemian Rhapsody", "Queen")),
    # version info that DOES change the track is preserved
    ("Avicii - Levels (Skrillex Remix)", "x",
     ("Levels (Skrillex Remix)", "Avicii")),
    ("Artist - Song (feat. Someone)", "x",
     ("Song (feat. Someone)", "Artist")),
    # no dash, ordinary channel -> channel as artist
    ("Just A Title", "CoolUploader", ("Just A Title", "CoolUploader")),
    # surrounding quotes trimmed
    ('Artist - "Quoted Title"', "x", ("Quoted Title", "Artist")),
])
def test_split(raw, uploader, expected):
    assert split_artist_title(raw, uploader) == expected


def test_split_empty_title_falls_back_to_raw():
    title, artist = split_artist_title("(Official Video)", "Chan")
    assert title  # never returns an empty title
    assert artist == "Chan"


# ----- fetch_playlist (yt-dlp mocked) ------------------------------------

def _data(entries, title="My Mix"):
    return {"title": title, "entries": entries}


def test_fetch_happy_path():
    payload = _data([
        {"title": "Daft Punk - One More Time", "uploader": "DaftPunkVEVO"},
        {"title": "Instant Crush", "uploader": "Daft Punk - Topic"},
        {"title": "[Private video]", "uploader": ""},   # skipped
        {"title": "", "uploader": "x"},                  # skipped
        {"not_a_dict": True},                            # ignored
    ])
    with patch("src.youtube_playlist._run_ytdlp", return_value=payload):
        name, tracks = yt.fetch_playlist("https://youtube.com/playlist?list=PL")
    assert name == "My Mix"
    assert [(t.title, t.artist) for t in tracks] == [
        ("One More Time", "Daft Punk"),
        ("Instant Crush", "Daft Punk"),
    ]
    assert tracks[0].raw_title == "Daft Punk - One More Time"


def test_fetch_rejects_non_youtube_url():
    with pytest.raises(YouTubeError):
        yt.fetch_playlist("https://example.com/playlist?list=PL")


def test_fetch_entry_cap():
    payload = _data([{"title": f"A - {i}"} for i in range(yt._MAX_ENTRIES + 1)])
    with patch("src.youtube_playlist._run_ytdlp", return_value=payload):
        with pytest.raises(YouTubeError, match="cap"):
            yt.fetch_playlist("https://youtube.com/playlist?list=PL")


def test_fetch_default_title_when_missing():
    with patch("src.youtube_playlist._run_ytdlp",
               return_value={"entries": [{"title": "A - B"}]}):
        name, tracks = yt.fetch_playlist("https://youtube.com/playlist?list=PL")
    assert name == "YouTube Playlist"
    assert len(tracks) == 1
