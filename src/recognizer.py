import asyncio
from dataclasses import dataclass

try:
    from shazamio import Shazam
except ImportError:  # CI / tests can import this module without the audio stack
    Shazam = None  # type: ignore[assignment]

_shazam = None


def _get_shazam():
    global _shazam
    if Shazam is None:
        raise RuntimeError(
            "shazamio is not installed. Run `pip install -r requirements.txt`."
        )
    if _shazam is None:
        _shazam = Shazam()
    return _shazam


async def close() -> None:
    """Close the shared Shazam client (and its aiohttp ClientSession) if open.

    Call this between long-running batches — e.g. between streamers in a
    multi-streamer run — to avoid 'Unclosed client session' warnings and
    leaked connectors on Windows asyncio.
    """
    global _shazam
    if _shazam is None:
        return
    # shazamio.Shazam doesn't expose a public close, but its session lives at
    # `.session` on recent versions. Best-effort.
    closer = getattr(_shazam, "close", None)
    if callable(closer):
        try:
            result = closer()
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            pass
    session = getattr(_shazam, "session", None)
    if session is not None and hasattr(session, "close"):
        try:
            await session.close()
        except Exception:
            pass
    _shazam = None


@dataclass
class RecognitionResult:
    title: str
    artist: str
    timestamp: int
    shazam_track_id: str


async def recognize_clip(clip_path: str, timestamp: int) -> RecognitionResult | None:
    """Send a clip to Shazam and return a result, or None if unrecognized."""
    shazam = _get_shazam()

    for attempt in range(2):
        try:
            out = await shazam.recognize(clip_path)
            break
        except Exception:
            if attempt == 0:
                await asyncio.sleep(3)
            else:
                return None

    matches = out.get("matches", [])
    track = out.get("track", {})
    if not matches or not track:
        return None

    title = track.get("title", "").strip()
    artist = track.get("subtitle", "").strip()
    track_id = str(track.get("key", ""))

    if not title or not artist:
        return None

    return RecognitionResult(
        title=title,
        artist=artist,
        timestamp=timestamp,
        shazam_track_id=track_id,
    )


def deduplicate(results: list[RecognitionResult]) -> list[RecognitionResult]:
    """Keep one entry per (title, artist) pair — the one with the earliest timestamp."""
    seen: dict[tuple[str, str], RecognitionResult] = {}
    for r in results:
        key = (r.title.lower(), r.artist.lower())
        if key not in seen or r.timestamp < seen[key].timestamp:
            seen[key] = r
    return sorted(seen.values(), key=lambda r: r.timestamp)
