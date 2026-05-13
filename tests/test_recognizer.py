from src.recognizer import RecognitionResult, deduplicate


def _result(title, artist, ts):
    return RecognitionResult(title=title, artist=artist, timestamp=ts, shazam_track_id=f"id-{ts}")


def test_deduplicate_keeps_earliest_timestamp():
    results = [
        _result("Song A", "Artist X", 300),
        _result("Song A", "Artist X", 60),   # earliest
        _result("Song A", "Artist X", 600),
    ]
    deduped = deduplicate(results)
    assert len(deduped) == 1
    assert deduped[0].timestamp == 60


def test_deduplicate_is_case_insensitive():
    results = [
        _result("song a", "ARTIST X", 120),
        _result("Song A", "Artist X", 60),
    ]
    deduped = deduplicate(results)
    assert len(deduped) == 1
    assert deduped[0].timestamp == 60


def test_deduplicate_keeps_different_songs():
    results = [
        _result("Song A", "Artist X", 60),
        _result("Song B", "Artist X", 90),
        _result("Song A", "Artist Y", 120),  # same title, different artist
    ]
    deduped = deduplicate(results)
    assert len(deduped) == 3


def test_deduplicate_sorts_by_timestamp():
    results = [
        _result("Z", "Z", 500),
        _result("A", "A", 100),
        _result("M", "M", 300),
    ]
    deduped = deduplicate(results)
    assert [r.timestamp for r in deduped] == [100, 300, 500]


def test_deduplicate_empty():
    assert deduplicate([]) == []
