#!/usr/bin/env python3
"""One-shot migration: move per-run output files out of the repo root.

Before this script, `--output-dir` defaulted to ``"."`` so every VOD's
``<handle>_<date>_songs.csv``, ``*_songs.txt``, and ``*_matches.jsonl``
landed in the project root. The default is now ``"output"``; running
this script once moves any pre-existing files into ``./output/`` so the
old data still gets found by ``--rebuild`` / ``--backfill-spotify`` /
the search-cache prime under the new default.

Idempotent — re-runs are no-ops once the root is clean. Files already
present at the destination are skipped (not overwritten) to avoid
clobbering anything edited by hand.

Usage:
    python migrate_output.py          # do it
    python migrate_output.py --dry    # show what would move
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from glob import glob

PATTERNS = ("*_songs.csv", "*_songs.txt", "*_matches.jsonl")
DEFAULT_DEST = "output"


def collect(root: str) -> list[str]:
    out: list[str] = []
    for pat in PATTERNS:
        for path in glob(os.path.join(root, pat)):
            if os.path.isfile(path):
                out.append(path)
    return sorted(set(out))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--root", default=".",
                   help="Repo root to migrate from (default: cwd)")
    p.add_argument("--dest", default=DEFAULT_DEST,
                   help=f"Destination dir (default: {DEFAULT_DEST!r})")
    p.add_argument("--dry", action="store_true",
                   help="Print what would move, don't touch files")
    args = p.parse_args()

    root = os.path.abspath(args.root)
    dest = os.path.abspath(os.path.join(root, args.dest))

    files = collect(root)
    if not files:
        print(f"No matching files in {root!r} — nothing to migrate.")
        return 0

    print(f"Found {len(files)} file(s) to move into {dest!r}:")
    moved = skipped = collision = 0
    for src in files:
        name = os.path.basename(src)
        target = os.path.join(dest, name)
        if os.path.exists(target):
            print(f"  SKIP  {name}  (already exists at destination)")
            collision += 1
            continue
        if args.dry:
            print(f"  DRY   {name}")
        else:
            os.makedirs(dest, exist_ok=True)
            shutil.move(src, target)
            print(f"  MOVE  {name}")
            moved += 1

    if args.dry:
        print(f"\nDry run — {len(files) - collision} would move, "
              f"{collision} would skip.")
    else:
        print(f"\nDone — {moved} moved, {collision} skipped (already present).")
        skipped = collision
    return 0


if __name__ == "__main__":
    sys.exit(main())
