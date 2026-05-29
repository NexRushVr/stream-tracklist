"""Durable, incremental recognition artifact.

The expensive, hard-to-reproduce part of a run is the recognition pass: hours of
ffmpeg segment pulls + Shazam fingerprinting. Spotify resolution, by contrast,
is cheap to redo later and is the thing that fails (daily quota). So recognitions
are persisted to a per-VOD JSONL *as each slot completes* — before any Spotify
call — which means:

  * a crash / quota stall / Ctrl-C mid-scan never throws away what was already
    recognized, and
  * a re-run can resume from the last sampled timestamp instead of rescanning
    from zero.

File format — one JSON object per line, in slot order:
  {"ts": 120, "title": "...", "artist": "...", "key": "...", "isrc": "..."}  # a match
  {"ts": 240}                                                                 # sampled, no match
  {"done": true, "total": 720}                                                # scan ran to completion

`ts` advances on every slot (match or not) so resume knows where to pick up.
The `done` marker distinguishes "scan finished" (just re-resolve) from "scan was
interrupted" (resume the remaining timestamps).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

from . import recognizer


def path_for(output_dir: str, base_name: str) -> str:
    return os.path.join(output_dir, f"{base_name}_matches.jsonl")


def record_slot(path: str, ts: int, result: "recognizer.RecognitionResult | None") -> None:
    """Append one slot outcome. Opens in append mode each call so a kill at any
    point leaves a valid, complete-up-to-the-last-line file."""
    if result is not None:
        obj = {
            "ts": ts,
            "title": result.title,
            "artist": result.artist,
            "key": result.shazam_track_id,
            "isrc": result.isrc,
        }
    else:
        obj = {"ts": ts}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def mark_done(path: str, total: int) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"done": True, "total": total}) + "\n")


@dataclass
class Artifact:
    matches: list["recognizer.RecognitionResult"]
    last_ts: int | None   # highest sampled timestamp, or None if nothing recorded
    done: bool            # whether a completion marker was written


def read(path: str) -> Artifact:
    """Parse an existing artifact. Tolerates a truncated final line (a kill
    mid-write) by skipping any line that doesn't parse."""
    matches: list[recognizer.RecognitionResult] = []
    last_ts: int | None = None
    done = False
    if not os.path.exists(path):
        return Artifact(matches, last_ts, done)
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (ValueError, json.JSONDecodeError):
                continue  # truncated/garbled line — skip, keep what's valid
            if obj.get("done"):
                done = True
                continue
            ts = obj.get("ts")
            if not isinstance(ts, int):
                continue
            last_ts = ts if last_ts is None else max(last_ts, ts)
            if "title" in obj:
                matches.append(recognizer.RecognitionResult(
                    title=obj.get("title", ""),
                    artist=obj.get("artist", ""),
                    timestamp=ts,
                    shazam_track_id=str(obj.get("key", "")),
                    isrc=str(obj.get("isrc", "") or ""),
                ))
    return Artifact(matches, last_ts, done)


def clear(path: str) -> None:
    try:
        if os.path.exists(path):
            os.unlink(path)
    except OSError:
        pass
