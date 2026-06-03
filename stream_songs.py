#!/usr/bin/env python3
import argparse
import asyncio
import io
import os
import shutil
import sys
from urllib.parse import urlparse

# Force UTF-8 output on Windows so emoji/special chars in stream titles don't crash
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from src import __version__, extractor, matches, output, recognizer, source
from src import spotify_client, streamer_log, youtube_playlist


def _read_streamers_file(path: str) -> list[str]:
    """Read streamer handles from a text file: one per line, first whitespace
    token used, blank lines and '#' comments ignored. Preserves order."""
    handles: list[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            handles.append(line.split()[0])
    return handles


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="stream-tracklist",
        description="Auto-Shazam a Twitch/Kick VOD and build a Spotify playlist of every song that played.",
    )
    parser.add_argument("--version", action="version", version=f"stream-tracklist {__version__}")
    parser.add_argument("--list-streamers", action="store_true",
                        help="List all streamers with logs in --log-dir, then exit")
    parser.add_argument("--show-streamer", metavar="HANDLE", default=None,
                        help="Print the VOD log for a streamer handle, then exit")
    parser.add_argument("--rebuild", nargs="+", metavar="HANDLE", default=None,
                        help="Rebuild Spotify playlist(s) from existing *_songs.csv files in --output-dir. "
                             "Globs <handle>*_songs.csv for each handle and dedup-appends every Spotify URL "
                             "found into a playlist named after the handle. No audio scanning. "
                             "(e.g. --rebuild eevi abehamm)")
    parser.add_argument("--backfill-spotify", nargs="*", metavar="HANDLE", default=None,
                        help="Retry Spotify resolution for unresolved rows in existing "
                             "*_songs.csv files (those that fell back to a /search/ URL, e.g. "
                             "because the daily quota was hit mid-scan). Uses the ISRC column "
                             "for exact lookups when present, rewrites the CSVs in place, and "
                             "dedup-appends newly-resolved tracks to each handle's playlist. "
                             "No audio scanning. Pass handles to scope it (e.g. --backfill-spotify "
                             "moonbuvr zayi) or no args to backfill every CSV in --output-dir.")
    parser.add_argument("--serve", action="store_true",
                        help="Launch the local web UI (pywebview window if installed, "
                             "otherwise browser). All flags after this are ignored.")
    parser.add_argument("--port", type=int, default=8731,
                        help="Port for --serve (default: 8731)")
    parser.add_argument("--no-window", action="store_true",
                        help="With --serve, don't open a pywebview window — print the "
                             "URL and let you open it in any browser yourself.")
    parser.add_argument("--from-youtube", metavar="URL", default=None,
                        help="Convert a YouTube playlist into a Spotify playlist (no audio "
                             "scanning — reads video titles via yt-dlp, searches Spotify, "
                             "dedup-appends into a playlist named after the YouTube playlist "
                             "or --playlist-name). e.g. --from-youtube "
                             "\"https://youtube.com/playlist?list=...\"")
    parser.add_argument("source_input", metavar="SOURCE", nargs="?",
                        help="Local MP4 path, M3U8 URL, or vodvod.top URL")
    parser.add_argument("--all", action="store_true",
                        help="Process every VOD on a vodvod.top channel (newest first)")
    parser.add_argument("--interval", type=int, default=120,
                        help="Seconds between recognition samples (default: 120)")
    parser.add_argument("--clip-duration", type=int, default=20,
                        help="Duration of each audio clip in seconds (default: 20)")
    parser.add_argument("--output-dir", default=".",
                        help="Directory for output files (default: current dir)")
    parser.add_argument("--output-name", default=None,
                        help="Base filename for output — single-VOD mode only")
    parser.add_argument("--max-duration", type=int, default=86400,
                        help="Fallback scan limit in seconds if duration can't be detected (default: 86400 = 24h)")
    parser.add_argument("--retries", type=int, default=4,
                        help="Extra offsets to try within each slot on no-match (default: 4)")
    parser.add_argument("--create-playlist", action="store_true",
                        help="Create a Spotify playlist for each VOD processed")
    parser.add_argument("--mega-playlist", default=None, metavar="NAME",
                        help="Combine all VODs into one Spotify playlist with this name (--all mode only)")
    parser.add_argument("--private-playlist", action="store_true",
                        help="Make playlists private instead of public (default: public/shareable)")
    parser.add_argument("--playlist-name", default=None,
                        help="Override playlist name — single-VOD mode only")
    parser.add_argument("--streamer-mode", action="store_true",
                        help="Maintain a per-streamer log + single playlist named after the handle. "
                             "Skips VODs already processed (use --rescan to override). "
                             "Requires a vodvod.top URL or --streamers.")
    parser.add_argument("--streamers", nargs="+", metavar="HANDLE", default=None,
                        help="One or more streamer handles to process in --streamer-mode. "
                             "Defaults to vodvod.top; prefix with 'kick:' for Kick.com "
                             "(e.g. --streamers eevi kick:abehamm). "
                             "Implies --all. When set, SOURCE is not required.")
    parser.add_argument("--streamers-file", metavar="PATH", default=None,
                        help="Read streamer handles from a file (one per line; blank lines and "
                             "'#' comments ignored; 'kick:' prefix supported). Merged with any "
                             "--streamers. Handy for a curated daily-run list. Use with --streamer-mode.")
    parser.add_argument("--rescan", action="store_true",
                        help="In --streamer-mode, re-process VODs even if logged as done")
    parser.add_argument("--fresh", action="store_true",
                        help="Ignore any existing <name>_matches.jsonl artifact and rescan "
                             "from the start (default: resume an interrupted scan / re-resolve "
                             "a completed one without re-fingerprinting audio)")
    parser.add_argument("--log-dir", default="logs",
                        help="Directory for per-streamer JSON logs (default: ./logs)")
    parser.add_argument("--verbose", action="store_true",
                        help="Print each recognition result as it arrives")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print timestamps that would be sampled, then exit")

    args = parser.parse_args()

    # Resolve --streamers-file once; it can scope both a scan (--streamers) and
    # a --backfill-spotify run. 'kick:'-prefixed handles are kept verbatim for
    # scanning but stripped for backfill (which works off local CSVs by handle).
    # --serve only needs --port / --no-window / --log-dir / --output-dir;
    # skip all other validation since the UI spawns fresh CLI subprocesses.
    if args.serve:
        return args

    file_handles: list[str] = []
    if args.streamers_file:
        try:
            file_handles = _read_streamers_file(args.streamers_file)
        except OSError as exc:
            parser.error(f"--streamers-file could not be read: {exc}")
        if not file_handles:
            parser.error(f"--streamers-file {args.streamers_file!r} contained no handles")

    # Inspection / rebuild / youtube commands short-circuit before any
    # SOURCE validation — none of them scan audio.
    if (args.list_streamers or args.show_streamer or args.rebuild
            or args.from_youtube or args.backfill_spotify is not None):
        if args.rebuild and not spotify_client.credentials_available():
            parser.error("--rebuild requires SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in .env")
        if args.from_youtube and not spotify_client.credentials_available():
            parser.error("--from-youtube requires SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in .env")
        if args.backfill_spotify is not None:
            if not spotify_client.credentials_available():
                parser.error("--backfill-spotify requires SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in .env")
            # Let --streamers-file scope the backfill too (handles, kick: stripped).
            for h in file_handles:
                bare = h[5:] if h.lower().startswith("kick:") else h
                if bare not in args.backfill_spotify:
                    args.backfill_spotify.append(bare)
        return args

    if file_handles:
        # Merge with --streamers (file appended), de-duplicated, order preserved.
        merged = list(args.streamers or [])
        for h in file_handles:
            if h not in merged:
                merged.append(h)
        args.streamers = merged

    if args.streamers and not args.streamer_mode:
        parser.error("--streamers / --streamers-file only work with --streamer-mode")
    if args.streamers:
        # Multi-streamer mode: SOURCE is not required, --all is implied.
        args.all = True
    elif not args.source_input:
        parser.error("SOURCE is required (or pass --streamers / --list-streamers / --show-streamer)")

    if args.interval < 30:
        parser.error("--interval must be at least 30 seconds")
    if args.clip_duration < 5:
        parser.error("--clip-duration must be at least 5 seconds")
    if args.create_playlist and not spotify_client.credentials_available():
        parser.error("--create-playlist requires SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in .env")
    if args.all and not args.streamers:
        if "vodvod.top" not in args.source_input and "kick.com" not in args.source_input:
            parser.error("--all only works with vodvod.top or kick.com channel URLs")
    if args.streamer_mode:
        if not args.streamers:
            src = args.source_input or ""
            if "vodvod.top" not in src and "kick.com" not in src:
                parser.error("--streamer-mode requires a vodvod.top/kick.com URL or --streamers HANDLE [HANDLE ...]")
        if not spotify_client.credentials_available():
            parser.error("--streamer-mode requires SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in .env")

    return args


def check_deps() -> None:
    missing = []
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            missing.append(tool)
    if missing:
        print(f"ERROR: Missing required tool(s): {', '.join(missing)}")
        print("Install ffmpeg: https://ffmpeg.org/download.html")
        sys.exit(1)


async def process_vod(
    resolved_url: str,
    source_name: str,
    file_name: str,
    args: argparse.Namespace,
    create_playlist: bool = True,
) -> list[output.SongEntry]:
    """Run the full recognition pipeline for a single VOD."""
    has_spotify = spotify_client.credentials_available()

    print(f"Stream URL  : {resolved_url[:80]}{'...' if len(resolved_url) > 80 else ''}")
    print(f"Spotify     : {'enabled' if has_spotify else 'not configured (search links only)'}")

    print("Detecting duration...")
    dur = extractor.get_duration(resolved_url)
    if dur:
        total_sec = int(dur)
        h, m = divmod(total_sec, 3600)
        m, s = divmod(m, 60)
        print(f"Duration    : {h:02d}:{m:02d}:{s:02d}")
    else:
        total_sec = args.max_duration
        print(f"Duration    : unknown — will scan up to {total_sec // 3600}h")

    # Cap probed duration against --max-duration to prevent runaway scans on
    # forged / pathological metadata.
    total_sec = min(total_sec, args.max_duration)

    timestamps = list(range(0, total_sec, args.interval))

    if args.dry_run:
        print(f"\nDry run — would sample {len(timestamps)} timestamps:")
        for ts in timestamps:
            h, m = divmod(ts, 3600)
            m, s = divmod(m, 60)
            print(f"  {h:02d}:{m:02d}:{s:02d}")
        return []

    # Per-VOD recognition artifact: written incrementally during the scan so a
    # crash / quota stall / Ctrl-C never loses recognitions, and a re-run can
    # resume from where it stopped (or just re-resolve a completed scan).
    base_name = output.derive_output_name(file_name)
    os.makedirs(args.output_dir, exist_ok=True)
    artifact_path = matches.path_for(args.output_dir, base_name)

    results: list[recognizer.RecognitionResult] = []
    scan_timestamps = timestamps
    skip_scan = False

    if args.fresh:
        matches.clear(artifact_path)
    else:
        prior = matches.read(artifact_path)
        if prior.done:
            # Completed scan (even one that found zero songs) — re-resolve from
            # the artifact, never re-fingerprint. Avoids re-appending a done
            # marker on every re-run of a no-match VOD.
            print(f"Found a complete matches artifact ({len(prior.matches)} match(es)) — "
                  f"re-resolving without re-scanning audio.")
            print(f"  (delete {os.path.basename(artifact_path)} or pass --fresh to force a rescan)")
            results = prior.matches
            skip_scan = True
        elif prior.last_ts is not None:
            resume_from = prior.last_ts + args.interval
            scan_timestamps = [t for t in timestamps if t >= resume_from]
            results = list(prior.matches)
            print(f"Resuming: recovered {len(prior.matches)} match(es), continuing from "
                  f"{output.format_timestamp(resume_from)} "
                  f"({len(scan_timestamps)} of {len(timestamps)} slots remaining).")

    if not skip_scan:
        attempts_per_slot = 1 + args.retries
        retry_step = args.interval // attempts_per_slot
        print(f"\nSampling {len(scan_timestamps)} timestamps every {args.interval}s "
              f"({args.clip_duration}s clips, {attempts_per_slot} attempt(s) per slot)...\n")

        loop = asyncio.get_event_loop()
        consecutive_failures = 0
        consecutive_403s = 0
        scan_aborted = False

        for i, ts in enumerate(scan_timestamps):
            ts_str = output.format_timestamp(ts)
            print(f"[{i+1}/{len(scan_timestamps)}] {ts_str}  ", end="", flush=True)

            slot_result = None
            slot_extracted_any = False
            slot_error_msg = ""

            for attempt in range(attempts_per_slot):
                sample_ts = ts + attempt * retry_step
                clip_path = None
                try:
                    clip_path = await loop.run_in_executor(
                        None, extractor.extract_clip, resolved_url, sample_ts, args.clip_duration
                    )
                except extractor.ExtractionError as exc:
                    slot_error_msg = str(exc)
                    if args.verbose and attempt > 0:
                        print(f"\n    retry {attempt} extraction failed", end="", flush=True)
                    continue

                slot_extracted_any = True

                try:
                    slot_result = await recognizer.recognize_clip(clip_path, sample_ts)
                finally:
                    if clip_path and os.path.exists(clip_path):
                        try:
                            os.unlink(clip_path)
                        except OSError:
                            pass

                if slot_result:
                    break

                if attempt < attempts_per_slot - 1:
                    if args.verbose:
                        print(f"\n    +{retry_step}s  ", end="", flush=True)
                    await asyncio.sleep(0.5)

            # A slot is an extraction failure only if NO attempt produced a clip.
            # A clip that extracted on a retry and matched must still be recorded.
            if slot_result is None and not slot_extracted_any:
                consecutive_failures += 1
                is_403 = "403" in slot_error_msg or "Forbidden" in slot_error_msg
                if is_403:
                    consecutive_403s += 1
                    print("[skip] 403 Forbidden — CDN blocks access")
                    if consecutive_403s == 3:
                        print("\n  WARNING: CDN is returning 403 Forbidden on every segment.")
                        print("  This VOD's stream segments are not publicly accessible.")
                        print("  Tip: try a different channel or grab the .m3u8 URL from browser DevTools.\n")
                    if consecutive_403s >= 5:
                        print(f"  Aborting this VOD after {consecutive_403s} consecutive 403s.\n")
                        scan_aborted = True
                        break
                else:
                    consecutive_403s = 0
                    print("[skip] extraction failed")
                    if consecutive_failures % 10 == 0:
                        print(f"  ({consecutive_failures} consecutive extraction failures — continuing)")
                matches.record_slot(artifact_path, ts, None)
                await asyncio.sleep(1.0)
                continue

            consecutive_failures = 0
            consecutive_403s = 0

            if slot_result:
                results.append(slot_result)
                matches.record_slot(artifact_path, ts, slot_result)
                if args.verbose:
                    print(f"[match] \"{slot_result.title}\" by {slot_result.artist}")
                else:
                    print(f"[match] {slot_result.title} — {slot_result.artist}")
            else:
                matches.record_slot(artifact_path, ts, None)
                print("[no match]")

            await asyncio.sleep(1.0)

        # Only mark the scan complete if it ran to the end (not a 403 abort), so
        # an interrupted scan resumes next time instead of being treated as done.
        if not scan_aborted:
            matches.mark_done(artifact_path, len(timestamps))

    unique = recognizer.deduplicate(results)

    if has_spotify and unique:
        print(f"\nLooking up {len(unique)} track(s) on Spotify...")
    entries: list[output.SongEntry] = []
    for r in unique:
        spot_url, spot_uri = None, None
        if has_spotify:
            spot_url, spot_uri = spotify_client.search_track(r.title, r.artist, r.isrc)
            status = "found" if spot_url else "not found"
            print(f"  {r.artist} — {r.title}: {status}")
        entries.append(output.SongEntry(
            timestamp=r.timestamp,
            title=r.title,
            artist=r.artist,
            spotify_url=spot_url,
            spotify_uri=spot_uri,
            youtube_url=output.make_youtube_url(r.title, r.artist),
            isrc=r.isrc,
        ))

    output.print_results(entries)

    # base_name / output_dir were established above for the matches artifact.
    txt_path = os.path.join(args.output_dir, f"{base_name}_songs.txt")
    csv_path = os.path.join(args.output_dir, f"{base_name}_songs.csv")

    output.write_txt(entries, txt_path, source_name)
    output.write_csv(entries, csv_path)
    print(f"Output written to:\n  {txt_path}\n  {csv_path}")

    if create_playlist and args.create_playlist:
        uris = [e.spotify_uri for e in entries if e.spotify_uri]
        if not uris:
            print("\nNo Spotify tracks found — playlist not created.")
        else:
            playlist_name = args.playlist_name or source_name
            print(f"\nCreating Spotify playlist: \"{playlist_name}\" ({len(uris)} tracks)...")
            spotify_client.ensure_oauth_token_fresh()
            try:
                playlist_url = spotify_client.create_playlist(
                    playlist_name, uris,
                    public=not args.private_playlist,
                    description=spotify_client.build_playlist_description(),
                )
                print(f"Playlist created: {playlist_url}")
            except Exception as exc:
                print(f"ERROR creating playlist: {exc}")

    return entries


async def _run_streamer_mode(args: argparse.Namespace) -> None:
    if args.streamers:
        handles: list[str] = []
        urls: list[str] = []
        for raw in args.streamers:
            raw = raw.strip()
            if not raw:
                continue
            if raw.lower().startswith("kick:"):
                h = raw[5:].lstrip("@").strip()
                base = f"https://kick.com/{h}"
            else:
                h = raw.lstrip("@").strip()
                base = f"https://vodvod.top/channels/@{h}"
            if not streamer_log.is_valid_handle(h):
                print(f"ERROR: invalid handle {raw!r} — handles may contain only "
                      f"letters, digits, '_' and '-' (max 64 chars).")
                sys.exit(1)
            handles.append(h)
            urls.append(base)
        print(f"Multi-streamer run: {', '.join(handles)}\n")
        summary: list[tuple[str, int, int, str]] = []
        for i, (handle, url) in enumerate(zip(handles, urls), 1):
            print(f"{'#'*60}")
            print(f"# [{i}/{len(handles)}] {handle}")
            print(f"{'#'*60}\n")
            try:
                processed, added_total, purl = await _process_one_streamer(args, url)
                summary.append((handle, processed, added_total, purl))
            except SystemExit:
                summary.append((handle, 0, 0, "(failed)"))
            except Exception as exc:
                # One streamer failing shouldn't kill the whole batch.
                print(f"ERROR processing {handle}: {exc}")
                summary.append((handle, 0, 0, "(error)"))
            finally:
                # Reset Shazam's aiohttp session between streamers so connectors
                # don't pile up over a multi-hour run.
                await recognizer.close()
            print()

        print(f"{'='*60}")
        print("Summary")
        print(f"{'='*60}")
        for h, processed, added, purl in summary:
            print(f"  {h:<20} {processed} new VOD(s)  +{added} track(s)  {purl}")
        return

    await _process_one_streamer(args, args.source_input)


async def _process_one_streamer(args: argparse.Namespace, url: str) -> tuple[int, int, str]:
    """Run streamer mode against a single vodvod.top or kick.com URL.
    Returns (vods_processed, total_tracks_added, playlist_url)."""
    handle = streamer_log.extract_handle(url)
    if not handle:
        print(f"ERROR: could not parse a streamer handle from URL: {url}")
        sys.exit(1)

    # Derive the public streamer page URL for the playlist description.
    # vodvod.top mirrors Twitch VODs, so the underlying handle is a Twitch handle.
    is_kick = "kick.com" in urlparse(url).netloc
    streamer_url = f"kick.com/{handle}" if is_kick else f"twitch.tv/{handle}"
    description = spotify_client.build_playlist_description(streamer_url)

    log = streamer_log.load(args.log_dir, handle)
    playlist_name = args.playlist_name or handle
    print(f"Streamer    : {handle}")
    print(f"Log file    : {streamer_log.log_path(args.log_dir, handle)}")
    print(f"Playlist    : \"{playlist_name}\" (existing tracks will be preserved)")

    # Resolve VODs first so we don't create an empty playlist for a bad handle.
    if args.all:
        try:
            vods = source.list_vods(url)
        except (ValueError, RuntimeError) as exc:
            print(f"ERROR: {exc}")
            sys.exit(1)
    else:
        try:
            resolved_url, source_name, _ = source.resolve(url)
        except (ValueError, RuntimeError) as exc:
            print(f"ERROR: {exc}")
            sys.exit(1)
        vods = [source.VodInfo(
            m3u8_url=resolved_url,
            file_name=output.derive_output_name(source_name),
            display_title=source_name,
        )]

    spotify_client.ensure_oauth_token_fresh()
    try:
        playlist_id, playlist_url = spotify_client.find_or_create_playlist(
            playlist_name,
            public=not args.private_playlist,
            description=description,
        )
    except Exception as exc:
        print(f"ERROR resolving streamer playlist: {exc}")
        sys.exit(1)
    streamer_log.set_playlist(log, playlist_id, playlist_url)
    streamer_log.save(args.log_dir, handle, log)
    print(f"Playlist URL: {playlist_url}\n")

    pending = [v for v in vods if args.rescan or not streamer_log.is_processed(log, v.file_name)]
    skipped = len(vods) - len(pending)
    print(f"Found {len(vods)} VOD(s) — {skipped} already logged, {len(pending)} to process\n")

    # Fetch existing playlist contents once and reuse across every VOD in this
    # run — avoids paying for a paginated fetch per VOD.
    try:
        existing_uris = spotify_client.playlist_track_uris(playlist_id)
    except Exception as exc:
        print(f"WARNING: could not fetch existing playlist contents ({exc}); "
              f"per-VOD dedup will refetch.")
        existing_uris = None

    total_added = 0
    for idx, vod in enumerate(pending, 1):
        print(f"{'='*60}")
        print(f"VOD {idx}/{len(pending)}: {vod.display_title}")
        print(f"{'='*60}")
        entries = await process_vod(
            vod.m3u8_url, vod.display_title, vod.file_name, args,
            create_playlist=False,
        )

        uris = [e.spotify_uri for e in entries if e.spotify_uri]
        added = 0
        if uris:
            spotify_client.ensure_oauth_token_fresh()
            try:
                added = spotify_client.add_tracks_dedup(
                    playlist_id, uris, existing=existing_uris,
                )
                print(f"\nAdded {added} new track(s) to \"{playlist_name}\" "
                      f"({len(uris) - added} already present)")
            except Exception as exc:
                print(f"ERROR appending to streamer playlist: {exc}")
        else:
            print("\nNo Spotify tracks found in this VOD — nothing to append.")

        total_added += added
        streamer_log.record_vod(
            log, vod.file_name,
            display_title=vod.display_title,
            m3u8_url=vod.m3u8_url,
            song_count=len(entries),
            tracks_added=added,
        )
        streamer_log.save(args.log_dir, handle, log)
        print()

    print(f"Streamer playlist: {playlist_url}")
    return len(pending), total_added, playlist_url


def _cmd_list_streamers(log_dir: str) -> None:
    if not os.path.isdir(log_dir):
        print(f"No log directory at: {log_dir}")
        return
    files = sorted(f for f in os.listdir(log_dir) if f.endswith(".json"))
    if not files:
        print(f"No streamer logs in: {log_dir}")
        return
    print(f"Streamers logged in {log_dir}:\n")
    for f in files:
        handle = f[:-5]
        log = streamer_log.load(log_dir, handle)
        n_vods = len(log.get("vods", {}))
        total_songs = sum(v.get("song_count", 0) for v in log.get("vods", {}).values())
        purl = log.get("playlist_url") or "(no playlist)"
        print(f"  {handle:<25} {n_vods:>3} VOD(s)  {total_songs:>4} song(s)  {purl}")


def _cmd_rebuild(handles: list[str], output_dir: str, log_dir: str, public: bool) -> None:
    """For each handle, glob <output_dir>/<handle>*_songs.csv, harvest Spotify URLs,
    and dedup-append them into a playlist named after the handle.
    Pure CSV→playlist replay; no audio scanning."""
    import csv
    import glob

    print(f"Rebuilding playlists from CSVs in: {output_dir}\n")
    summary: list[tuple[str, int, int, int, str]] = []

    for raw in handles:
        handle = raw.lstrip("@").strip()
        if not streamer_log.is_valid_handle(handle):
            print(f"ERROR: invalid handle {raw!r} — handles may contain only "
                  f"letters, digits, '_' and '-' (max 64 chars).\n")
            continue
        pattern = os.path.join(output_dir, f"{handle}*_songs.csv")
        csv_paths = sorted(glob.glob(pattern))
        print(f"{'='*60}")
        print(f"# {handle}")
        print(f"{'='*60}")
        print(f"Pattern : {pattern}")
        print(f"Matched : {len(csv_paths)} CSV file(s)")
        if not csv_paths:
            print("(nothing to rebuild — no CSVs match this handle)\n")
            summary.append((handle, 0, 0, 0, "(no CSVs)"))
            continue

        seen_uris: list[str] = []
        seen_set: set[str] = set()
        per_csv_counts: list[tuple[str, int]] = []
        for path in csv_paths:
            count_in_file = 0
            with open(path, "r", encoding="utf-8", newline="") as f:
                for row in csv.DictReader(f):
                    url = (row.get("Spotify") or "").strip()
                    uri = spotify_client.track_url_to_uri(url)
                    if uri and uri not in seen_set:
                        seen_set.add(uri)
                        seen_uris.append(uri)
                        count_in_file += 1
            per_csv_counts.append((os.path.basename(path), count_in_file))
            print(f"  {os.path.basename(path):<55} +{count_in_file} new URI(s)")

        if not seen_uris:
            print(f"\n(no Spotify URLs found across {len(csv_paths)} CSV(s))\n")
            summary.append((handle, len(csv_paths), 0, 0, "(no URIs)"))
            continue

        spotify_client.ensure_oauth_token_fresh()
        # Rebuild can't tell whether this handle came from vodvod or Kick (no
        # source URL in the per-handle log), so the description omits the
        # streamer-platform link and only credits the tool.
        try:
            playlist_id, playlist_url = spotify_client.find_or_create_playlist(
                handle,
                public=public,
                description=spotify_client.build_playlist_description(),
            )
        except Exception as exc:
            print(f"ERROR resolving playlist \"{handle}\": {exc}\n")
            summary.append((handle, len(csv_paths), len(seen_uris), 0, "(failed)"))
            continue

        try:
            added = spotify_client.add_tracks_dedup(playlist_id, seen_uris)
        except Exception as exc:
            print(f"ERROR appending to playlist: {exc}\n")
            summary.append((handle, len(csv_paths), len(seen_uris), 0, "(append failed)"))
            continue

        print(f"\nPlaylist: \"{handle}\" — {playlist_url}")
        print(f"Added {added} new track(s) "
              f"({len(seen_uris) - added} already present in playlist)\n")

        # Update or create the streamer log so future --streamer-mode runs see this state.
        log = streamer_log.load(log_dir, handle)
        streamer_log.set_playlist(log, playlist_id, playlist_url)
        streamer_log.save(log_dir, handle, log)

        summary.append((handle, len(csv_paths), len(seen_uris), added, playlist_url))

    print(f"{'='*60}")
    print("Summary")
    print(f"{'='*60}")
    for h, n_csv, n_uri, added, purl in summary:
        print(f"  {h:<20} {n_csv:>3} CSV(s)  {n_uri:>4} unique URI(s)  +{added:>4} added  {purl}")


def _cmd_backfill(handles: list[str], output_dir: str, log_dir: str, public: bool) -> None:
    """Retry Spotify resolution for unresolved (/search/ fallback) rows in existing
    CSVs, rewrite them in place, and dedup-append newly-resolved tracks to each
    handle's playlist. The recovery path for runs that hit the Spotify daily quota."""
    import csv
    import glob

    spotify_client.reset_quota_flag()
    # Skip negatives: a prior /search/ fallback row is exactly what we're here
    # to retry, so it must not be cached as a permanent miss.
    primed = spotify_client.prime_search_cache(output_dir, include_negatives=False)
    if primed:
        print(f"Spotify cache: {primed} prior positive lookup(s) loaded\n")

    # With handles → <handle>*_songs.csv each; without → every *_songs.csv (one group).
    targets: list[tuple[str | None, list[str]]] = []
    if handles:
        for raw in handles:
            handle = raw.lstrip("@").strip()
            if not streamer_log.is_valid_handle(handle):
                print(f"ERROR: invalid handle {raw!r} — letters, digits, '_' and '-' only.\n")
                continue
            pattern = os.path.join(output_dir, f"{handle}*_songs.csv")
            targets.append((handle, sorted(glob.glob(pattern))))
    else:
        targets.append((None, sorted(glob.glob(os.path.join(output_dir, "*_songs.csv")))))

    summary: list[tuple[str, int, int, str]] = []
    for handle, csv_paths in targets:
        label = handle or "(all CSVs)"
        print(f"{'='*60}")
        print(f"# {label}")
        print(f"{'='*60}")
        if not csv_paths:
            print("(no CSVs match)\n")
            summary.append((label, 0, 0, "(no CSVs)"))
            continue

        resolved_total = 0
        still_unresolved = 0
        newly_resolved_uris: list[str] = []

        for path in csv_paths:
            with open(path, "r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                fieldnames = reader.fieldnames
                rows = list(reader)
            if not fieldnames:
                continue

            changed = 0
            for row in rows:
                spotify = (row.get("Spotify") or "").strip()
                if "/search/" not in spotify:
                    continue  # already a real /track/ link, or empty
                title = (row.get("Title") or "").lstrip("'").strip()
                artist = (row.get("Artist") or "").lstrip("'").strip()
                isrc = (row.get("ISRC") or "").lstrip("'").strip()
                if not title or not artist or spotify_client.quota_exhausted():
                    still_unresolved += 1
                    continue
                url, uri = spotify_client.search_track(title, artist, isrc)
                if url:
                    row["Spotify"] = url
                    changed += 1
                    resolved_total += 1
                    if uri:
                        newly_resolved_uris.append(uri)
                else:
                    still_unresolved += 1

            if changed:
                # Rewrite preserving the original column schema (old CSVs lack
                # ISRC). Write-to-temp + atomic replace so a crash mid-write
                # can't truncate/lose the existing CSV.
                tmp_path = path + ".tmp"
                with open(tmp_path, "w", encoding="utf-8", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(rows)
                os.replace(tmp_path, path)
            print(f"  {os.path.basename(path):<55} +{changed} resolved")

        playlist_url = "(no playlist update)"
        if handle and newly_resolved_uris:
            spotify_client.ensure_oauth_token_fresh()
            try:
                playlist_id, playlist_url = spotify_client.find_or_create_playlist(
                    handle, public=public,
                    description=spotify_client.build_playlist_description(),
                )
                added = spotify_client.add_tracks_dedup(playlist_id, newly_resolved_uris)
                print(f"\nPlaylist: \"{handle}\" — {playlist_url}  (+{added} new track(s))")
                log = streamer_log.load(log_dir, handle)
                streamer_log.set_playlist(log, playlist_id, playlist_url)
                streamer_log.save(log_dir, handle, log)
            except Exception as exc:
                print(f"ERROR updating playlist \"{handle}\": {exc}")
                playlist_url = "(playlist update failed)"

        summary.append((label, resolved_total, still_unresolved, playlist_url))

        if spotify_client.quota_exhausted():
            print("\n⚠ Spotify quota hit during backfill — remaining unresolved rows "
                  "kept for a future --backfill-spotify run.")
            break

    print(f"{'='*60}")
    print("Backfill summary")
    print(f"{'='*60}")
    for label, res, rem, purl in summary:
        print(f"  {label:<20} +{res:>4} resolved, {rem:>4} still unresolved  {purl}")


def _cmd_from_youtube(
    url: str,
    output_dir: str,
    playlist_name: str | None,
    public: bool,
) -> None:
    """Enumerate a YouTube playlist, resolve each video to a Spotify track,
    and dedup-append the matches into a Spotify playlist. No audio scanning."""
    print(f"Reading YouTube playlist: {url}\n")
    try:
        pl_title, yt_tracks = youtube_playlist.fetch_playlist(url)
    except youtube_playlist.YouTubeError as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)

    print(f"Playlist    : {pl_title}")
    print(f"Videos      : {len(yt_tracks)} with usable titles")
    if not yt_tracks:
        print("Nothing to do — no parseable video titles.")
        return

    print(f"\nLooking up {len(yt_tracks)} track(s) on Spotify...")
    entries: list[output.SongEntry] = []
    found = 0
    for i, t in enumerate(yt_tracks):
        spot_url, spot_uri = spotify_client.search_track(t.title, t.artist)
        if spot_url:
            found += 1
        print(f"  [{i+1}/{len(yt_tracks)}] {t.artist} — {t.title}: "
              f"{'found' if spot_url else 'not found'}")
        entries.append(output.SongEntry(
            timestamp=i,  # playlist position — preserves ordering in TXT/CSV
            title=t.title,
            artist=t.artist,
            spotify_url=spot_url,
            spotify_uri=spot_uri,
            youtube_url=output.make_youtube_url(t.title, t.artist),
        ))

    base_name = output.derive_output_name(pl_title)
    os.makedirs(output_dir, exist_ok=True)
    txt_path = os.path.join(output_dir, f"{base_name}_songs.txt")
    csv_path = os.path.join(output_dir, f"{base_name}_songs.csv")
    output.write_txt(entries, txt_path, f"YouTube playlist: {pl_title}")
    output.write_csv(entries, csv_path)
    print(f"\n{found}/{len(yt_tracks)} matched on Spotify.")
    print(f"Output written to:\n  {txt_path}\n  {csv_path}")

    uris = [e.spotify_uri for e in entries if e.spotify_uri]
    if not uris:
        print("\nNo Spotify matches — playlist not created.")
        return

    name = playlist_name or pl_title
    spotify_client.ensure_oauth_token_fresh()
    try:
        playlist_id, playlist_url = spotify_client.find_or_create_playlist(
            name,
            public=public,
            description=spotify_client.build_playlist_description(),
        )
        added = spotify_client.add_tracks_dedup(playlist_id, uris)
    except Exception as exc:
        print(f"\nERROR creating/updating playlist: {exc}")
        sys.exit(1)

    print(f"\nPlaylist: \"{name}\" — {playlist_url}")
    print(f"Added {added} new track(s) "
          f"({len(uris) - added} already present).")


def _cmd_show_streamer(log_dir: str, handle: str) -> None:
    handle = handle.lstrip("@").strip()
    if not streamer_log.is_valid_handle(handle):
        print(f"ERROR: invalid handle {handle!r} — handles may contain only "
              f"letters, digits, '_' and '-' (max 64 chars).")
        return
    path = streamer_log.log_path(log_dir, handle)
    if not os.path.isfile(path):
        print(f"No log for streamer \"{handle}\" at: {path}")
        return
    log = streamer_log.load(log_dir, handle)
    print(f"Streamer    : {handle}")
    print(f"Log file    : {path}")
    print(f"Playlist    : {log.get('playlist_url') or '(none)'}")
    vods = log.get("vods", {})
    print(f"VODs logged : {len(vods)}")
    if not vods:
        return
    print()
    by_date = sorted(vods.items(), key=lambda kv: kv[1].get("processed_at", ""), reverse=True)
    for key, v in by_date:
        print(f"  [{v.get('processed_at', '?')}] v{v.get('tool_version', '?')}  "
              f"{v.get('song_count', 0)} songs  +{v.get('tracks_added', 0)} added  {key}")
        title = v.get("display_title", "")
        if title and title != key:
            print(f"      {title}")


async def run_pipeline(args: argparse.Namespace) -> None:
    if args.streamer_mode:
        await _run_streamer_mode(args)
        return

    if args.all:
        print(f"Fetching all VODs for: {args.source_input}")
        try:
            vods = source.list_vods(args.source_input)
        except (ValueError, RuntimeError) as exc:
            print(f"ERROR: {exc}")
            sys.exit(1)

        print(f"Found {len(vods)} VOD(s)\n")

        # When building a mega playlist, suppress per-VOD playlist creation
        per_vod_playlist = args.create_playlist and not args.mega_playlist
        all_entries: list[output.SongEntry] = []

        for idx, vod in enumerate(vods, 1):
            print(f"{'='*60}")
            print(f"VOD {idx}/{len(vods)}: {vod.display_title}")
            print(f"{'='*60}")
            entries = await process_vod(
                vod.m3u8_url, vod.display_title, vod.file_name, args,
                create_playlist=per_vod_playlist,
            )
            all_entries.extend(entries)
            print()

        if args.mega_playlist:
            # Deduplicate across all VODs by (title, artist)
            seen: dict[tuple[str, str], output.SongEntry] = {}
            for e in all_entries:
                key = (e.title.lower(), e.artist.lower())
                if key not in seen:
                    seen[key] = e
            unique_entries = list(seen.values())

            uris = [e.spotify_uri for e in unique_entries if e.spotify_uri]
            if not uris:
                print("Mega playlist: no Spotify tracks found across any VOD.")
            else:
                print(f"{'='*60}")
                print(f"Creating mega playlist: \"{args.mega_playlist}\" "
                      f"({len(uris)} unique tracks across {len(vods)} VODs)...")
                spotify_client.ensure_oauth_token_fresh()
                try:
                    playlist_url = spotify_client.create_playlist(
                        args.mega_playlist, uris,
                        public=not args.private_playlist,
                        description=spotify_client.build_playlist_description(),
                    )
                    print(f"Playlist created: {playlist_url}")
                except Exception as exc:
                    print(f"ERROR creating mega playlist: {exc}")
    else:
        print(f"Resolving source: {args.source_input}")
        try:
            resolved_url, source_name, _ = source.resolve(args.source_input)
        except (ValueError, RuntimeError) as exc:
            print(f"ERROR: {exc}")
            sys.exit(1)

        file_name = args.output_name or source_name
        print(f"Source name : {source_name}")
        await process_vod(resolved_url, source_name, file_name, args, create_playlist=True)

    # Close the shared Shazam aiohttp session so non-streamer multi-VOD runs
    # don't leak connectors / emit "Unclosed client session" on exit.
    await recognizer.close()


def main() -> None:
    args = parse_args()
    if args.serve:
        from src import webui
        webui.serve(
            port=args.port, open_window=not args.no_window,
            log_dir=args.log_dir, output_dir=args.output_dir,
        )
        return
    if args.list_streamers:
        _cmd_list_streamers(args.log_dir)
        return
    if args.show_streamer:
        _cmd_show_streamer(args.log_dir, args.show_streamer)
        return
    if args.rebuild:
        _cmd_rebuild(args.rebuild, args.output_dir, args.log_dir, public=not args.private_playlist)
        return
    if args.backfill_spotify is not None:
        _cmd_backfill(args.backfill_spotify, args.output_dir, args.log_dir,
                      public=not args.private_playlist)
        return
    if args.from_youtube:
        _cmd_from_youtube(
            args.from_youtube, args.output_dir, args.playlist_name,
            public=not args.private_playlist,
        )
        return
    check_deps()
    if spotify_client.credentials_available():
        primed = spotify_client.prime_search_cache(args.output_dir)
        if primed:
            print(f"Spotify cache: {primed} prior lookup(s) loaded from {args.output_dir}")
    asyncio.run(run_pipeline(args))


if __name__ == "__main__":
    main()
