import csv

from src.output import (
    SongEntry,
    derive_output_name,
    format_timestamp,
    make_spotify_search_url,
    make_youtube_url,
    write_csv,
    write_txt,
)


def test_format_timestamp_zero():
    assert format_timestamp(0) == "00:00:00"


def test_format_timestamp_under_an_hour():
    assert format_timestamp(125) == "00:02:05"


def test_format_timestamp_over_an_hour():
    assert format_timestamp(3 * 3600 + 7 * 60 + 9) == "03:07:09"


def test_make_spotify_search_url_encodes_query():
    url = make_spotify_search_url("Hello World", "Foo & Bar")
    assert url.startswith("https://open.spotify.com/search/")
    assert "Foo%20%26%20Bar" in url
    assert "Hello%20World" in url


def test_make_youtube_url_uses_plus_for_spaces():
    url = make_youtube_url("Song Title", "Some Artist")
    assert "search_query=" in url
    # quote_plus turns spaces into "+"
    assert "Some+Artist+Song+Title" in url


def test_derive_output_name_sanitizes_and_lowercases():
    assert derive_output_name("Cool Stream Title!") == "cool_stream_title"


def test_derive_output_name_truncates_to_60():
    long_name = "a" * 200
    assert len(derive_output_name(long_name)) == 60


def test_derive_output_name_falls_back_when_empty():
    assert derive_output_name("!!!") == "stream_songs_output"
    assert derive_output_name("") == "stream_songs_output"


def _sample_entries():
    return [
        SongEntry(
            timestamp=60,
            title="Track A",
            artist="Artist A",
            spotify_url="https://open.spotify.com/track/aaa",
            spotify_uri="spotify:track:aaa",
            youtube_url="https://www.youtube.com/results?search_query=Artist+A+Track+A",
        ),
        SongEntry(
            timestamp=180,
            title="Track B",
            artist="Artist B",
            spotify_url=None,  # forces fallback to search URL
            spotify_uri=None,
            youtube_url="https://www.youtube.com/results?search_query=Artist+B+Track+B",
        ),
    ]


def test_write_csv_round_trip(tmp_path):
    path = tmp_path / "out.csv"
    write_csv(_sample_entries(), str(path))

    with open(path, encoding="utf-8") as f:
        rows = list(csv.reader(f))

    assert rows[0] == ["Timestamp", "Title", "Artist", "Spotify", "YouTube"]
    assert rows[1][:3] == ["00:01:00", "Track A", "Artist A"]
    assert rows[1][3] == "https://open.spotify.com/track/aaa"
    # Track B has no spotify_url — should fall back to a search URL
    assert rows[2][3].startswith("https://open.spotify.com/search/")


def test_write_txt_contains_expected_lines(tmp_path):
    path = tmp_path / "out.txt"
    write_txt(_sample_entries(), str(path), source_name="My Stream")

    content = path.read_text(encoding="utf-8")
    assert "My Stream" in content
    assert "[00:01:00]" in content
    assert "Track A" in content
    assert "Artist B" in content


def test_write_txt_handles_empty(tmp_path):
    path = tmp_path / "empty.txt"
    write_txt([], str(path), source_name="Empty")
    assert "No songs identified." in path.read_text(encoding="utf-8")
