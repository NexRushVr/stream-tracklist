"""Filesystem anchoring for the desktop GUI.

The GUI is a thin pywebview shell that drives the existing CLI in place: it
shells out to ``.venv\\Scripts\\python.exe stream_songs.py --serve`` and
points a window at the resulting Flask backend. So every path here is
resolved relative to the **repo root**, whether we're running from source
(``python gui/app.py``) or from a PyInstaller-frozen ``stream-tracklist.exe``
that sits at the repo root.

Two different anchors, easy to confuse:

* **Repo / sibling files** (``.venv``, ``stream_songs.py``, ``.env``,
  ``logs/``, ``output/``) live next to the exe → anchor on the *exe
  directory* (:func:`app_dir`), NEVER ``sys._MEIPASS``.
* **Bundled UI assets** (the ``web/`` directory, the icon) are packed
  *into* the exe → anchor on ``sys._MEIPASS`` (:func:`resource_path`).

Pattern lifted from the sibling ``twitch-highlights`` project, which
already proved this design ships a working Windows exe.
"""
from __future__ import annotations

import os
import sys


def app_dir() -> str:
    """The repo root: where ``.venv``, ``stream_songs.py``, ``.env`` live.

    Frozen: the directory containing the exe (we ship it at the repo root).
    Source: the parent of this ``gui/`` package.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_path(*parts: str) -> str:
    """Absolute path to a bundled GUI asset (``web/index.html``, the icon).

    Frozen: under PyInstaller's ``_MEIPASS`` temp-extract dir.
    Source: under this ``gui/`` package directory.
    """
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


def venv_python() -> str:
    """Path to the project's venv interpreter (the one install.bat created)."""
    # Windows layout — the GUI is Windows-first.
    return os.path.join(app_dir(), ".venv", "Scripts", "python.exe")


def stream_songs_script() -> str:
    return os.path.join(app_dir(), "stream_songs.py")


def env_file() -> str:
    return os.path.join(app_dir(), ".env")


def env_example_file() -> str:
    return os.path.join(app_dir(), ".env.example")


def output_dir() -> str:
    return os.path.join(app_dir(), "output")


def logs_dir() -> str:
    return os.path.join(app_dir(), "logs")


def venv_ready() -> bool:
    """True once install.bat (or manual pip install) has built the venv."""
    return os.path.isfile(venv_python())
