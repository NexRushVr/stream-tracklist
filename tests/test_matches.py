"""Tests for the incremental recognition artifact (src/matches.py)."""
import json

from src import matches
from src.recognizer import RecognitionResult


def _rr(ts, title="T", artist="A", isrc="ISRC", key="k"):
    return RecognitionResult(title=title, artist=artist, timestamp=ts,
                             shazam_track_id=key, isrc=isrc)


def test_record_and_read_round_trip(tmp_path):
    path = matches.path_for(str(tmp_path), "vod")
    matches.record_slot(path, 0, _rr(0, "Song A", "Artist A", "ISRCA"))
    matches.record_slot(path, 120, None)            # sampled, no match
    matches.record_slot(path, 240, _rr(240, "Song B", "Artist B", "ISRCB"))

    art = matches.read(path)
    assert art.done is False
    assert art.last_ts == 240                        # advances on every slot
    assert [m.title for m in art.matches] == ["Song A", "Song B"]
    assert art.matches[0].isrc == "ISRCA"
    assert art.matches[1].timestamp == 240


def test_done_marker(tmp_path):
    path = matches.path_for(str(tmp_path), "vod")
    matches.record_slot(path, 0, _rr(0))
    matches.mark_done(path, total=10)
    art = matches.read(path)
    assert art.done is True
    assert art.last_ts == 0
    assert len(art.matches) == 1


def test_read_missing_file_is_empty(tmp_path):
    art = matches.read(matches.path_for(str(tmp_path), "nope"))
    assert art.matches == [] and art.last_ts is None and art.done is False


def test_read_tolerates_truncated_final_line(tmp_path):
    """A kill mid-write can leave a half-written last line; it must not break
    recovery of the complete lines before it."""
    path = matches.path_for(str(tmp_path), "vod")
    matches.record_slot(path, 0, _rr(0, "Good", "Act"))
    with open(path, "a", encoding="utf-8") as f:
        f.write('{"ts": 120, "title": "Truncat')   # no newline, invalid JSON
    art = matches.read(path)
    assert [m.title for m in art.matches] == ["Good"]
    assert art.last_ts == 0


def test_resume_point_computation(tmp_path):
    """Simulate the resume arithmetic process_vod does."""
    path = matches.path_for(str(tmp_path), "vod")
    interval = 120
    for ts in (0, 120, 240, 360):
        matches.record_slot(path, ts, _rr(ts) if ts % 240 == 0 else None)
    art = matches.read(path)
    resume_from = art.last_ts + interval
    assert resume_from == 480
    full = list(range(0, 1200, interval))
    remaining = [t for t in full if t >= resume_from]
    assert remaining == [480, 600, 720, 840, 960, 1080]


def test_clear(tmp_path):
    path = matches.path_for(str(tmp_path), "vod")
    matches.record_slot(path, 0, _rr(0))
    matches.clear(path)
    assert matches.read(path).matches == []


def test_record_slot_is_valid_jsonl(tmp_path):
    path = matches.path_for(str(tmp_path), "vod")
    # _rr(ts, title, artist, isrc, key)
    matches.record_slot(path, 60, _rr(60, "X", "Y", isrc="Z", key="kk"))
    matches.mark_done(path, 1)
    with open(path, encoding="utf-8") as f:
        lines = [json.loads(ln) for ln in f if ln.strip()]
    assert lines[0] == {"ts": 60, "title": "X", "artist": "Y", "key": "kk", "isrc": "Z"}
    assert lines[1] == {"done": True, "total": 1}
