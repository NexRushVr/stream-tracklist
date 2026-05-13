import os
import subprocess
import tempfile

# Drop "file" so a hostile m3u8 segment can't coerce ffmpeg into reading local
# disk (e.g. file:///C:/Users/.../.spotify_token_cache). Local MP4 sources
# don't need this — they're a single argument to -i, not playlist contents.
_HLS_PROTOCOL_WHITELIST = "crypto,data,http,https,tcp,tls"


class ExtractionError(Exception):
    def __init__(self, timestamp: int, detail: str):
        self.timestamp = timestamp
        super().__init__(f"Extraction failed at {timestamp}s: {detail}")


def _is_remote(source: str) -> bool:
    """True if the source is a URL ffmpeg will pull over the network."""
    return source.startswith(("http://", "https://"))


def get_duration(source: str) -> float | None:
    """Return total duration in seconds via ffprobe, or None for live/unknown streams."""
    cmd = ["ffprobe", "-v", "error"]
    if _is_remote(source):
        cmd += ["-protocol_whitelist", _HLS_PROTOCOL_WHITELIST]
    cmd += [
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        source,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        text = result.stdout.strip()
        if text and text != "N/A":
            return float(text)
    except (subprocess.TimeoutExpired, ValueError, OSError):
        pass
    return None


def extract_clip(source: str, start_seconds: int, duration: int = 20) -> str:
    """Extract a short audio clip from source at start_seconds. Returns temp mp3 path.

    Applies loudnorm so quiet background music gets boosted to a consistent level
    before fingerprinting, which helps recognition on mixed streams.
    """
    # NamedTemporaryFile gives us a file already created with secure perms
    # (no symlink-race window the way tempfile.mktemp had). We close it
    # immediately because ffmpeg wants to write to the path itself.
    fd, out_path = tempfile.mkstemp(suffix=".mp3", prefix="stream_songs_")
    os.close(fd)
    cmd = ["ffmpeg", "-y"]
    if _is_remote(source):
        cmd += ["-protocol_whitelist", _HLS_PROTOCOL_WHITELIST]
    cmd += [
        "-ss", str(start_seconds),
        "-i", source,
        "-t", str(duration),
        "-vn",
        # loudnorm: normalise perceived loudness so quiet music is amplified
        "-af", "loudnorm=I=-14:TP=-1:LRA=11",
        "-acodec", "libmp3lame",
        "-ar", "44100",
        "-ac", "2",       # stereo — preserves L/R separation between voice and music
        "-ab", "192k",    # higher bitrate = more fingerprint detail
        "-loglevel", "error",
        out_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired:
        _safe_unlink(out_path)
        raise ExtractionError(start_seconds, "ffmpeg timed out after 90s")
    except OSError as exc:
        _safe_unlink(out_path)
        raise ExtractionError(start_seconds, f"ffmpeg not found: {exc}")

    if result.returncode != 0:
        _safe_unlink(out_path)
        stderr = result.stderr.strip()
        if "403" in stderr or "Forbidden" in stderr:
            raise ExtractionError(start_seconds, "CDN returned 403 Forbidden — stream segments are not publicly accessible")
        raise ExtractionError(start_seconds, stderr or "non-zero exit")

    if not os.path.exists(out_path) or os.path.getsize(out_path) < 1024:
        _safe_unlink(out_path)
        raise ExtractionError(start_seconds, "output file too small or missing")

    return out_path


def _safe_unlink(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.unlink(path)
    except OSError:
        pass
