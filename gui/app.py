"""stream-tracklist — desktop GUI entry point.

A thin pywebview shell around the existing CLI. The CLI does all real work;
this module just:

1. Confirms the project's ``.venv`` is built (install.bat creates it).
2. Confirms the Edge WebView2 runtime is installed (Windows 10/11; Win11
   ships it by default, Win10 sometimes doesn't).
3. Spawns ``.venv\\Scripts\\python.exe stream_songs.py --serve --no-window``
   on a free local port — that subprocess owns the Flask backend and the
   ``JobManager`` that drives further CLI runs.
4. Waits for the backend to start listening, then opens a native window
   pointed at ``http://127.0.0.1:<port>/``.
5. Kills the backend's process tree on window close.

Run from source: ``python gui/app.py`` (see ``gui.bat``).
Frozen: drop ``stream-tracklist.exe`` next to ``.venv`` and double-click.

Pattern lifted from the sibling ``twitch-highlights`` project, which proved
this shape with a working Windows exe.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

# Make sibling gui modules importable both from source and when frozen.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import paths  # noqa: E402

# Frozen-exe debug log. Set STREAM_TRACKLIST_GUI_DEBUG=1 to write each step
# of the launch sequence to <app_dir>/gui_debug.log so a silent failure
# (typically: MessageBox not shown because the bootloader already exited)
# can be diagnosed. Cheap no-op when the env var is unset.
def _dlog(msg: str) -> None:
    if not os.environ.get("STREAM_TRACKLIST_GUI_DEBUG"):
        return
    try:
        with open(os.path.join(paths.app_dir(), "gui_debug.log"),
                  "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except OSError:
        pass


WEBVIEW2_DOWNLOAD = "https://developer.microsoft.com/microsoft-edge/webview2/"
_BACKEND_READY_TIMEOUT = 30.0   # seconds; backend imports flask/spotipy/etc.
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# --------------------------------------------------------------- helpers

def _message_box(title: str, text: str) -> None:
    """Native Win32 message box — used before the webview window exists."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x40)  # MB_ICONINFORMATION
    except Exception:
        print(f"{title}: {text}", file=sys.stderr)


def _webview2_present() -> bool:
    """True if the Evergreen WebView2 runtime is installed on this machine.

    Ships on Windows 11 by default, sometimes missing on Win10. Reading the
    registry directly so we can warn the user *before* pywebview crashes
    with an opaque error. Non-Windows → assume present (the GUI is
    Windows-first; this just keeps dev convenient on Linux/macOS).
    """
    if sys.platform != "win32":
        return True
    try:
        import winreg
    except ImportError:
        return True
    # Evergreen runtime registers under this client GUID. 64-bit Windows
    # puts the machine-wide key under WOW6432Node; per-user installs land
    # in HKCU. Any non-zero "pv" string counts.
    candidates = [
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
    ]
    for hive, subkey in candidates:
        try:
            with winreg.OpenKey(hive, subkey) as k:
                pv, _ = winreg.QueryValueEx(k, "pv")
                if pv and pv != "0.0.0.0":
                    return True
        except OSError:
            continue
    return False


def _free_port() -> int:
    """Bind a transient socket to discover a free localhost port. There's a
    brief race window between releasing and reusing it; the backend may
    rarely lose the race and exit with EADDRINUSE — the user can just
    relaunch. Worth it to avoid hard-coding a port that another app on the
    machine already owns."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_backend(port: int, timeout: float) -> bool:
    url = f"http://127.0.0.1:{port}/"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(0.25)
    return False


def _spawn_backend(port: int) -> subprocess.Popen:
    """Launch ``.venv python stream_songs.py --serve --no-window --port <p>``
    with the parent's stdio piped so the GUI process can keep running cleanly
    even if the backend prints a lot.
    """
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    cmd = [
        paths.venv_python(), "-u", paths.stream_songs_script(),
        "--serve", "--no-window", "--port", str(port),
    ]
    # cwd at the repo root so logs/, output/, .env all resolve as the CLI
    # expects. DEVNULL keeps the backend from inheriting the (possibly
    # absent) GUI console; the backend writes to stdout but the GUI doesn't
    # consume it.
    return subprocess.Popen(
        cmd, cwd=paths.app_dir(), env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=_CREATE_NO_WINDOW,
    )


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the backend process *and any children it spawned* — pywebview
    closes the window first, leaving the Flask server orphaned otherwise.
    """
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        # /T = kill child tree (each CLI job is a grandchild Popen).
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
            creationflags=_CREATE_NO_WINDOW,
            capture_output=True, check=False,
        )
    else:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            try:
                proc.kill()
            except OSError:
                pass


# --------------------------------------------------------------- entry

def main() -> int:
    _dlog(f"main() start; frozen={getattr(sys, 'frozen', False)}; "
          f"app_dir={paths.app_dir()}; exe={sys.executable}")
    # 0. venv check — install.bat creates it.
    if not paths.venv_ready():
        _dlog(f"venv NOT ready at {paths.venv_python()}")
        _message_box(
            "stream-tracklist — setup needed",
            "The project isn't installed yet (no .venv found next to the "
            "exe).\n\nDouble-click install.bat in this folder first, then "
            "launch this app again.",
        )
        return 1

    _dlog("venv OK")
    # 1. WebView2 check.
    if not _webview2_present():
        _dlog("WebView2 NOT present")
        _message_box(
            "stream-tracklist — WebView2 required",
            "This app needs the Microsoft Edge WebView2 runtime, which "
            "wasn't found on this PC.\n\nInstall the free Evergreen "
            f"runtime from:\n{WEBVIEW2_DOWNLOAD}\n\nThen launch this app "
            "again.",
        )
        try:
            os.startfile(WEBVIEW2_DOWNLOAD)  # noqa: S606
        except OSError:
            pass
        return 1

    _dlog("WebView2 OK")
    # 2. Spawn backend on a free port; wait for it to start listening.
    port = _free_port()
    _dlog(f"port={port}; spawning backend")
    backend = _spawn_backend(port)
    _dlog(f"backend spawned pid={backend.pid}; waiting up to "
          f"{_BACKEND_READY_TIMEOUT}s")
    if not _wait_for_backend(port, _BACKEND_READY_TIMEOUT):
        _dlog(f"backend NOT ready; exit_code={backend.poll()}")
        _kill_tree(backend)
        _message_box(
            "stream-tracklist — backend failed to start",
            f"The Flask backend didn't respond on port {port} within "
            f"{int(_BACKEND_READY_TIMEOUT)}s. Check that .venv has the full "
            "requirements installed (run install.bat to (re)create it).",
        )
        return 1

    _dlog("backend ready; opening window")
    # 3. Open the window. webview.start() blocks until close.
    import webview  # local import — keeps the venv-check error path fast
    url = f"http://127.0.0.1:{port}/"
    icon = paths.resource_path("icon.ico")
    window_kwargs = dict(
        width=1120, height=820, min_size=(900, 640),
        background_color="#14171c",
    )
    window = webview.create_window("stream-tracklist", url=url, **window_kwargs)

    # Some pywebview versions reject unknown create_window kwargs; the icon
    # is set per-call instead. Wrap in try so a missing icon doesn't fail
    # the launch.
    try:
        if os.path.isfile(icon):
            window.icon = icon
    except Exception:
        pass

    # When the window closes, kill the backend tree on a daemon thread.
    # pywebview's close hook runs while webview.start() still blocks the
    # main thread, so we can't just call _kill_tree() inline without a
    # potential deadlock.
    def _on_closed() -> None:
        threading.Thread(target=_kill_tree, args=(backend,), daemon=True).start()

    try:
        window.events.closed += _on_closed
    except Exception:
        pass

    try:
        webview.start()
    finally:
        _kill_tree(backend)
    return 0


if __name__ == "__main__":
    sys.exit(main())
