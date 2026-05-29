"""Tests for --streamers-file parsing (stream_songs._read_streamers_file)."""
import stream_songs


def test_reads_handles_ignoring_comments_and_blanks(tmp_path):
    p = tmp_path / "list.txt"
    p.write_text(
        "# a comment\n"
        "\n"
        "abehamm\n"
        "moonbuvr   # inline comment\n"
        "  zayi  \n"
        "kick:somedj\n"
        "   \n"
        "isthisrealvr extra tokens ignored\n",
        encoding="utf-8",
    )
    handles = stream_songs._read_streamers_file(str(p))
    assert handles == ["abehamm", "moonbuvr", "zayi", "kick:somedj", "isthisrealvr"]


def test_empty_file_returns_empty(tmp_path):
    p = tmp_path / "empty.txt"
    p.write_text("# only comments\n\n   \n", encoding="utf-8")
    assert stream_songs._read_streamers_file(str(p)) == []
