"""Local Flask UI for stream-tracklist.

Spawns the existing CLI as a subprocess per job, parses its line-oriented
progress output into structured events, and fans them out to the browser
over Server-Sent Events. No new orchestration logic — the CLI stays the
single source of truth; this is a thin window onto it.
"""
from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable

try:
    from flask import Flask, Response, jsonify, render_template, request
except ImportError:  # tests import the parser without Flask installed
    Flask = None  # type: ignore[assignment]

from . import __version__, streamer_log


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CLI = os.path.join(_REPO_ROOT, "stream_songs.py")
# UI assets live under gui/web/ so they're trivially co-locatable with the
# pywebview launcher and ride into the PyInstaller bundle via a single
# `datas=[('gui/web','web')]` entry. When frozen, gui/app.py overrides this
# with a sys._MEIPASS path before create_app() runs.
_TEMPLATES = os.environ.get(
    "STREAM_TRACKLIST_TEMPLATES",
    os.path.join(_REPO_ROOT, "gui", "web"),
)

# Cap per-job event history so a multi-hour scan can't balloon memory.
_MAX_EVENTS_PER_JOB = 10_000
# Cap concurrent jobs so a fat-fingered click can't fork-bomb the box.
_MAX_CONCURRENT = 4

# When the GUI is launched as a frozen windowed exe (no console attached),
# every child Popen() would otherwise flash a console window. Suppress it on
# Windows. Always harmless — non-Windows platforms don't define the flag.
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ---------------------------------------------------------------- parser

# Sample line: "[12/135] 00:22:00  [match] Title — Artist"
#              "[12/135] 00:22:00  [no match]"
#              "[12/135] 00:22:00  [skip] 403 Forbidden — CDN blocks access"
_SAMPLE_RE = re.compile(
    r"^\[(\d+)/(\d+)\]\s+(\d{2}:\d{2}:\d{2})\s+\[(match|no match|skip)\](?:\s+(.+))?$"
)
# "VOD 2/5: title here"
_VOD_RE = re.compile(r"^VOD\s+(\d+)/(\d+):\s+(.+)$")
# "# [1/2] eevi"  (multi-streamer header)
_STREAMER_RE = re.compile(r"^#\s+\[(\d+)/(\d+)\]\s+(\S+)\s*$")
# Spotify playlist URL announcements — all variants the CLI emits.
_PLAYLIST_RE = re.compile(
    r"^(?:Playlist URL|Streamer playlist|Playlist|Playlist created):"
    r"\s*\"?[^\"]*\"?\s*[—-]?\s*(https://open\.spotify\.com/playlist/\S+)"
)
# "Found 15 VOD(s) — 10 already logged, 5 to process"
_VOD_SUMMARY_RE = re.compile(
    r"^Found\s+(\d+)\s+VOD\(s\)(?:\s+—\s+(\d+)\s+already logged,\s+(\d+)\s+to process)?"
)
# "Added 25 new track(s) ..."
_ADDED_RE = re.compile(r"^Added\s+(\d+)\s+new\s+track")
# "Duration    : 04:28:40"
_DURATION_RE = re.compile(r"^Duration\s+:\s+(\d{2}:\d{2}:\d{2})")
# "ERROR ..."
_ERROR_RE = re.compile(r"^ERROR\b(.+)$")
# "Sampling 135 timestamps every 120s ..."
_TOTAL_RE = re.compile(r"^Sampling\s+(\d+)\s+timestamps")


def parse_line(line: str) -> dict[str, Any]:
    """Convert one line of CLI stdout into a structured event.

    Always returns a dict with a ``kind`` field — falls back to
    ``{"kind": "log", "text": line}`` for anything unrecognized so the
    frontend can render it as a plain log line.
    """
    s = line.strip()
    if not s:
        return {"kind": "blank"}

    m = _SAMPLE_RE.match(s)
    if m:
        n, total, ts, status, rest = m.groups()
        evt: dict[str, Any] = {
            "kind": "sample",
            "n": int(n), "total": int(total),
            "ts": ts, "status": status,
        }
        if status == "match" and rest:
            # "Title — Artist"  (em-dash, with optional " by " variant from print_results)
            if " — " in rest:
                title, artist = rest.split(" — ", 1)
            elif " by " in rest:
                title, artist = rest.split(" by ", 1)
                title = title.strip(' "')
            else:
                title, artist = rest, ""
            evt["title"] = title.strip()
            evt["artist"] = artist.strip()
        elif rest:
            evt["detail"] = rest
        return evt

    m = _VOD_RE.match(s)
    if m:
        return {"kind": "vod_start", "n": int(m.group(1)),
                "total": int(m.group(2)), "title": m.group(3).strip()}

    m = _STREAMER_RE.match(s)
    if m:
        return {"kind": "streamer_start", "n": int(m.group(1)),
                "total": int(m.group(2)), "handle": m.group(3)}

    m = _PLAYLIST_RE.match(s)
    if m:
        return {"kind": "playlist", "url": m.group(1)}

    m = _ADDED_RE.match(s)
    if m:
        return {"kind": "tracks_added", "count": int(m.group(1))}

    m = _DURATION_RE.match(s)
    if m:
        return {"kind": "duration", "value": m.group(1)}

    m = _TOTAL_RE.match(s)
    if m:
        return {"kind": "sample_total", "total": int(m.group(1))}

    m = _VOD_SUMMARY_RE.match(s)
    if m:
        total, skipped, pending = m.groups()
        evt = {"kind": "vod_summary", "total": int(total)}
        if skipped is not None:
            evt["skipped"] = int(skipped); evt["pending"] = int(pending)
        return evt

    m = _ERROR_RE.match(s)
    if m:
        return {"kind": "error", "text": s}

    return {"kind": "log", "text": s}


# ---------------------------------------------------------------- jobs

@dataclass
class Job:
    id: str
    label: str            # "streamer-mode: eevi, kick:abehamm" etc.
    kind: str             # "streamer" | "youtube" | "single"
    cmd: list[str]
    started_at: float
    proc: subprocess.Popen | None = None
    events: deque = field(default_factory=lambda: deque(maxlen=_MAX_EVENTS_PER_JOB))
    listeners: list[queue.Queue] = field(default_factory=list)
    status: str = "running"  # running | done | error | killed
    exit_code: int | None = None
    # Rolling state derived from events — cheap to keep, lets the UI render
    # a fresh card without replaying every line.
    summary: dict[str, Any] = field(default_factory=dict)

    def to_meta(self) -> dict[str, Any]:
        return {
            "id": self.id, "label": self.label, "kind": self.kind,
            "status": self.status, "started_at": self.started_at,
            "exit_code": self.exit_code, "summary": self.summary,
        }


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []   # newest-first
        self._lock = threading.Lock()

    # ----- listing / lookup ----------------------------------------------
    def list(self) -> list[Job]:
        with self._lock:
            return [self._jobs[jid] for jid in self._order if jid in self._jobs]

    def running_count(self) -> int:
        return sum(1 for j in self.list() if j.status == "running")

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    # ----- spawn ---------------------------------------------------------
    def start(self, label: str, kind: str, cli_args: list[str]) -> Job:
        if self.running_count() >= _MAX_CONCURRENT:
            raise RuntimeError(
                f"Already {_MAX_CONCURRENT} jobs running — wait for one to finish."
            )

        cmd = [sys.executable, "-u", _CLI, *cli_args]
        job = Job(
            id=uuid.uuid4().hex[:8], label=label, kind=kind,
            cmd=cmd, started_at=time.time(),
        )
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        proc = subprocess.Popen(
            cmd, cwd=_REPO_ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            bufsize=1, text=True, encoding="utf-8", errors="replace",
            env=env, creationflags=_CREATE_NO_WINDOW,
        )
        job.proc = proc

        with self._lock:
            self._jobs[job.id] = job
            self._order.insert(0, job.id)

        threading.Thread(target=self._reader, args=(job,), daemon=True).start()
        return job

    # ----- kill ----------------------------------------------------------
    def kill(self, job_id: str) -> bool:
        job = self.get(job_id)
        if not job or job.status != "running" or not job.proc:
            return False
        try:
            job.proc.terminate()
        except Exception:
            return False
        return True

    # ----- reader thread -------------------------------------------------
    def _emit(self, job: Job, evt: dict[str, Any]) -> None:
        job.events.append(evt)
        self._update_summary(job, evt)
        # Best-effort fan-out. Drop on slow/full listeners — SSE clients
        # will reconcile via /jobs/<id> on reconnect.
        dead: list[queue.Queue] = []
        for q in job.listeners:
            try:
                q.put_nowait(evt)
            except queue.Full:
                dead.append(q)
        for q in dead:
            try:
                job.listeners.remove(q)
            except ValueError:
                pass

    @staticmethod
    def _update_summary(job: Job, evt: dict[str, Any]) -> None:
        s = job.summary
        k = evt.get("kind")
        if k == "streamer_start":
            s["current_streamer"] = evt["handle"]
        elif k == "vod_start":
            s["current_vod"] = {"n": evt["n"], "total": evt["total"], "title": evt["title"]}
            s["sample_progress"] = {"n": 0, "total": 0}
            s["matches_this_vod"] = 0
        elif k == "sample_total":
            s.setdefault("sample_progress", {"n": 0, "total": 0})["total"] = evt["total"]
        elif k == "sample":
            sp = s.setdefault("sample_progress", {"n": 0, "total": 0})
            sp["n"], sp["total"] = evt["n"], evt["total"]
            if evt["status"] == "match":
                s["matches_this_vod"] = s.get("matches_this_vod", 0) + 1
                last = s.setdefault("last_matches", [])
                last.append({"title": evt.get("title", ""), "artist": evt.get("artist", "")})
                # Keep the tail small — the UI shows a "recent matches" strip.
                if len(last) > 12:
                    del last[: len(last) - 12]
        elif k == "playlist":
            s["playlist_url"] = evt["url"]
        elif k == "tracks_added":
            s["last_tracks_added"] = evt["count"]
            s["total_tracks_added"] = s.get("total_tracks_added", 0) + evt["count"]
        elif k == "duration":
            s["current_duration"] = evt["value"]
        elif k == "vod_summary":
            s["vod_summary"] = {
                "total": evt["total"],
                "skipped": evt.get("skipped"),
                "pending": evt.get("pending"),
            }
        elif k == "error":
            s.setdefault("errors", []).append(evt["text"])

    def _reader(self, job: Job) -> None:
        assert job.proc and job.proc.stdout
        try:
            for raw in job.proc.stdout:
                evt = parse_line(raw.rstrip("\n"))
                if evt.get("kind") == "blank":
                    continue
                self._emit(job, evt)
        except Exception as exc:  # pragma: no cover — reader is best-effort
            self._emit(job, {"kind": "error", "text": f"reader crashed: {exc}"})
        finally:
            rc = job.proc.wait()
            job.exit_code = rc
            job.status = "done" if rc == 0 else "error"
            self._emit(job, {"kind": "job_end", "exit_code": rc, "status": job.status})


# ---------------------------------------------------------------- routes

def create_app(log_dir: str = "logs", output_dir: str = "output") -> "Flask":
    if Flask is None:
        raise RuntimeError(
            "Flask is not installed. Run `pip install -r requirements.txt`."
        )

    app = Flask(__name__, template_folder=_TEMPLATES)
    mgr = JobManager()
    app.config["JOB_MANAGER"] = mgr
    app.config["LOG_DIR"] = log_dir
    app.config["OUTPUT_DIR"] = output_dir

    @app.route("/")
    def index():
        return render_template("index.html", version=__version__)

    @app.route("/api/jobs")
    def list_jobs():
        return jsonify([j.to_meta() for j in mgr.list()])

    @app.route("/api/jobs/<job_id>")
    def job_detail(job_id: str):
        job = mgr.get(job_id)
        if not job:
            return jsonify({"error": "not found"}), 404
        return jsonify({**job.to_meta(), "events": list(job.events)})

    @app.route("/api/jobs/<job_id>/kill", methods=["POST"])
    def kill_job(job_id: str):
        return jsonify({"ok": mgr.kill(job_id)})

    @app.route("/api/jobs/<job_id>/events")
    def job_events(job_id: str):
        job = mgr.get(job_id)
        if not job:
            return Response("not found", status=404)
        q: queue.Queue = queue.Queue(maxsize=1000)
        # Snapshot history first so a late connector sees what already happened.
        backlog = list(job.events)
        job.listeners.append(q)

        def stream() -> Iterable[bytes]:
            try:
                for evt in backlog:
                    yield f"data: {json.dumps(evt)}\n\n".encode("utf-8")
                while True:
                    try:
                        evt = q.get(timeout=15)
                    except queue.Empty:
                        # Keep-alive comment — proxies otherwise close the
                        # connection mid-job during a quiet stretch.
                        yield b": keep-alive\n\n"
                        if job.status != "running":
                            return
                        continue
                    yield f"data: {json.dumps(evt)}\n\n".encode("utf-8")
                    if evt.get("kind") == "job_end":
                        return
            finally:
                try:
                    job.listeners.remove(q)
                except ValueError:
                    pass

        return Response(stream(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache",
                                 "X-Accel-Buffering": "no"})

    @app.route("/api/run/streamer", methods=["POST"])
    def run_streamer():
        data = request.get_json(silent=True) or {}
        raw = data.get("handles") or []
        if isinstance(raw, str):
            raw = raw.split()
        handles = [h.strip() for h in raw if h and h.strip()]
        if not handles:
            return jsonify({"error": "handles required"}), 400
        for h in handles:
            # streamer_log.is_valid_handle already enforces the safe charset;
            # strip the optional "kick:" prefix before checking.
            core = h.split(":", 1)[1] if h.startswith("kick:") else h
            if not streamer_log.is_valid_handle(core):
                return jsonify({"error": f"invalid handle: {h}"}), 400
        cli_args = ["--streamer-mode", "--streamers", *handles,
                    "--log-dir", app.config["LOG_DIR"],
                    "--output-dir", app.config["OUTPUT_DIR"]]
        if data.get("fresh"):
            cli_args.append("--fresh")
        if data.get("rescan"):
            cli_args.append("--rescan")
        try:
            job = mgr.start(
                label=f"streamer-mode: {', '.join(handles)}",
                kind="streamer", cli_args=cli_args,
            )
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 429
        return jsonify(job.to_meta())

    @app.route("/api/run/backfill", methods=["POST"])
    def run_backfill():
        data = request.get_json(silent=True) or {}
        raw = data.get("handles") or []
        if isinstance(raw, str):
            raw = raw.split()
        handles = [h.strip() for h in raw if h and h.strip()]
        # backfill works off local CSVs by bare handle; strip any kick: prefix.
        clean = [h.split(":", 1)[1] if h.startswith("kick:") else h for h in handles]
        for h in clean:
            if not streamer_log.is_valid_handle(h):
                return jsonify({"error": f"invalid handle: {h}"}), 400
        cli_args = ["--backfill-spotify", *clean,
                    "--log-dir", app.config["LOG_DIR"],
                    "--output-dir", app.config["OUTPUT_DIR"]]
        label = (f"backfill: {', '.join(clean)}" if clean
                 else "backfill: every CSV in output-dir")
        try:
            job = mgr.start(label=label, kind="backfill", cli_args=cli_args)
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 429
        return jsonify(job.to_meta())

    @app.route("/api/run/youtube", methods=["POST"])
    def run_youtube():
        data = request.get_json(silent=True) or {}
        url = (data.get("url") or "").strip()
        if not url:
            return jsonify({"error": "url required"}), 400
        # The CLI re-validates the host; this is just a friendly early reject.
        from . import youtube_playlist as ytp
        if not ytp.is_youtube_playlist_url(url):
            return jsonify({"error": "not a YouTube playlist URL"}), 400
        args = ["--from-youtube", url,
                "--output-dir", app.config["OUTPUT_DIR"]]
        name = (data.get("playlist_name") or "").strip()
        if name:
            args += ["--playlist-name", name]
        try:
            job = mgr.start(
                label=f"youtube: {url[:50]}{'…' if len(url) > 50 else ''}",
                kind="youtube", cli_args=args,
            )
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 429
        return jsonify(job.to_meta())

    @app.route("/api/run/single", methods=["POST"])
    def run_single():
        data = request.get_json(silent=True) or {}
        src = (data.get("source") or "").strip()
        if not src:
            return jsonify({"error": "source required"}), 400
        args = [src, "--output-dir", app.config["OUTPUT_DIR"]]
        if data.get("create_playlist"):
            args.append("--create-playlist")
        if data.get("private_playlist"):
            args.append("--private-playlist")
        name = (data.get("playlist_name") or "").strip()
        if name:
            args += ["--playlist-name", name]
        try:
            job = mgr.start(
                label=f"single: {src[:60]}{'…' if len(src) > 60 else ''}",
                kind="single", cli_args=args,
            )
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 429
        return jsonify(job.to_meta())

    @app.route("/api/streamers")
    def streamers():
        out = []
        log_dir = app.config["LOG_DIR"]
        if not os.path.isdir(log_dir):
            return jsonify(out)
        for fname in sorted(os.listdir(log_dir)):
            if not fname.endswith(".json"):
                continue
            handle = fname[:-5]
            if not streamer_log.is_valid_handle(handle):
                continue
            try:
                with open(os.path.join(log_dir, fname), "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            vods = data.get("vods") or {}
            last_proc = max(
                (v.get("processed_at", "") for v in vods.values()),
                default="",
            )
            out.append({
                "handle": handle,
                "playlist_url": data.get("playlist_url"),
                "vod_count": len(vods),
                "last_processed_at": last_proc,
            })
        return jsonify(out)

    return app


# ---------------------------------------------------------------- entry

def serve(host: str = "127.0.0.1", port: int = 8731,
          open_window: bool = True, log_dir: str = "logs",
          output_dir: str = "output") -> None:
    """Start Flask in a background thread and (optionally) a pywebview window.

    If pywebview isn't installed we fall back to printing the URL — Flask
    still runs in the main thread so the process stays alive.
    """
    app = create_app(log_dir=log_dir, output_dir=output_dir)
    url = f"http://{host}:{port}"

    if not open_window:
        print(f"stream-tracklist UI: {url}")
        app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
        return

    try:
        import webview  # type: ignore[import-not-found]
    except ImportError:
        print(f"pywebview not installed — open this URL in your browser: {url}")
        app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
        return

    threading.Thread(
        target=lambda: app.run(host=host, port=port, debug=False,
                               use_reloader=False, threaded=True),
        daemon=True,
    ).start()
    # Tiny grace period so the first window load doesn't race the bind.
    time.sleep(0.4)
    webview.create_window(
        f"stream-tracklist {__version__}", url,
        width=1100, height=780, min_size=(820, 560),
    )
    webview.start()
