import json

from src import streamer_log


def test_extract_handle_vodvod():
    assert streamer_log.extract_handle("https://vodvod.top/channels/@eevi") == "eevi"


def test_extract_handle_vodvod_without_at_sign():
    assert streamer_log.extract_handle("https://vodvod.top/channels/eevi") == "eevi"


def test_extract_handle_kick():
    assert streamer_log.extract_handle("https://kick.com/abehamm") == "abehamm"


def test_extract_handle_kick_with_videos_path():
    assert streamer_log.extract_handle("https://kick.com/abehamm/videos") == "abehamm"


def test_extract_handle_returns_none_for_unknown():
    assert streamer_log.extract_handle("https://youtube.com/@somebody") is None


def test_load_missing_returns_empty_skeleton(tmp_path):
    data = streamer_log.load(str(tmp_path), "newhandle")
    assert data["handle"] == "newhandle"
    assert data["vods"] == {}
    assert data["playlist_id"] is None
    assert data["playlist_url"] is None


def test_save_and_load_round_trip(tmp_path):
    data = streamer_log.load(str(tmp_path), "h1")
    streamer_log.set_playlist(data, "pid123", "https://open.spotify.com/playlist/pid123")
    streamer_log.record_vod(
        data,
        vod_key="vod-1",
        display_title="Some Stream",
        m3u8_url="https://example.com/index.m3u8",
        song_count=12,
        tracks_added=10,
    )
    streamer_log.save(str(tmp_path), "h1", data)

    reloaded = streamer_log.load(str(tmp_path), "h1")
    assert reloaded["playlist_id"] == "pid123"
    assert "vod-1" in reloaded["vods"]
    assert reloaded["vods"]["vod-1"]["song_count"] == 12
    assert reloaded["vods"]["vod-1"]["tracks_added"] == 10


def test_is_processed():
    data = {"vods": {"abc": {"song_count": 1}}}
    assert streamer_log.is_processed(data, "abc")
    assert not streamer_log.is_processed(data, "xyz")


def test_load_recovers_from_corrupt_json(tmp_path):
    # Pre-write garbage to the expected log path so json.load fails.
    path = streamer_log.log_path(str(tmp_path), "broken")
    with open(path, "w", encoding="utf-8") as f:
        f.write("{not valid json")

    data = streamer_log.load(str(tmp_path), "broken")
    assert data["handle"] == "broken"
    assert data["vods"] == {}


def test_save_is_utf8_safe(tmp_path):
    data = streamer_log.load(str(tmp_path), "h2")
    streamer_log.record_vod(
        data,
        vod_key="v1",
        display_title="♡ MEOW ♡",  # non-ASCII title shouldn't break save
        m3u8_url="https://example.com/x.m3u8",
        song_count=1,
        tracks_added=1,
    )
    streamer_log.save(str(tmp_path), "h2", data)

    raw = (tmp_path / "h2.json").read_text(encoding="utf-8")
    # ensure_ascii=False — the emoji should be present literally, not escaped
    assert "♡ MEOW ♡" in raw
    # Still valid JSON
    assert json.loads(raw)["vods"]["v1"]["display_title"] == "♡ MEOW ♡"
