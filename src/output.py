import csv
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class SongEntry:
    timestamp: int
    title: str
    artist: str
    spotify_url: str | None       # direct track link, or None if not found
    spotify_uri: str | None       # spotify:track:... URI for playlist creation
    youtube_url: str


def make_spotify_search_url(title: str, artist: str) -> str:
    """Fallback search URL when real Spotify lookup isn't available."""
    query = urllib.parse.quote(f"{artist} {title}")
    return f"https://open.spotify.com/search/{query}"


def make_youtube_url(title: str, artist: str) -> str:
    query = urllib.parse.quote_plus(f"{artist} {title}")
    return f"https://www.youtube.com/results?search_query={query}"


def format_timestamp(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def print_results(entries: list[SongEntry]) -> None:
    if not entries:
        print("No songs identified.")
        return
    print(f"\nIdentified {len(entries)} song(s):\n")
    print("─" * 60)
    for e in entries:
        print(f"[{format_timestamp(e.timestamp)}] \"{e.title}\" by {e.artist}")
        spotify = e.spotify_url or make_spotify_search_url(e.title, e.artist)
        print(f"  Spotify: {spotify}")
        print(f"  YouTube: {e.youtube_url}")
        print()


def write_txt(entries: list[SongEntry], filepath: str, source_name: str) -> None:
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"Songs identified in: {source_name}\n")
        f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("─" * 60 + "\n\n")
        if not entries:
            f.write("No songs identified.\n")
            return
        for e in entries:
            spotify = e.spotify_url or make_spotify_search_url(e.title, e.artist)
            f.write(f"[{format_timestamp(e.timestamp)}] \"{e.title}\" by {e.artist}\n")
            f.write(f"  Spotify: {spotify}\n")
            f.write(f"  YouTube: {e.youtube_url}\n\n")


_CSV_INJECTION_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _safe_csv_cell(value: str) -> str:
    """Defang spreadsheet-formula injection: any cell whose first character
    is one Excel / LibreOffice treats as a formula opener gets a leading
    apostrophe so the spreadsheet renders it as a literal string."""
    if value and value[0] in _CSV_INJECTION_PREFIXES:
        return "'" + value
    return value


def write_csv(entries: list[SongEntry], filepath: str) -> None:
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Timestamp", "Title", "Artist", "Spotify", "YouTube"])
        for e in entries:
            spotify = e.spotify_url or make_spotify_search_url(e.title, e.artist)
            writer.writerow([
                format_timestamp(e.timestamp),
                _safe_csv_cell(e.title),
                _safe_csv_cell(e.artist),
                spotify,
                e.youtube_url,
            ])


def derive_output_name(source_name: str) -> str:
    import re
    sanitized = re.sub(r"[^\w]", "_", source_name).strip("_").lower()
    return sanitized[:60] or "stream_songs_output"
