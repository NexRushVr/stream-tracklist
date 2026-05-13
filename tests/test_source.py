import pytest

from src.source import MediaType, resolve


def test_resolve_local_mp4(tmp_path):
    video = tmp_path / "my video.mp4"
    video.write_bytes(b"")  # contents don't matter for resolve()

    resolved_path, name, media_type = resolve(str(video))
    assert media_type == MediaType.LOCAL_MP4
    assert name == "my video"
    assert resolved_path.endswith("my video.mp4")


def test_resolve_missing_file_raises(tmp_path):
    with pytest.raises(ValueError):
        resolve(str(tmp_path / "does_not_exist.mp4"))


def test_resolve_non_mp4_local_raises(tmp_path):
    txt = tmp_path / "notes.txt"
    txt.write_text("hi")
    with pytest.raises(ValueError):
        resolve(str(txt))


def test_resolve_direct_m3u8_url():
    url = "https://example.com/stream/foo.m3u8"
    resolved, name, media_type = resolve(url)
    assert media_type == MediaType.M3U8
    assert resolved == url
    assert "example_com" in name
    assert "foo" in name


def test_resolve_unknown_url_raises():
    with pytest.raises(ValueError):
        resolve("https://example.com/some/random/page")
