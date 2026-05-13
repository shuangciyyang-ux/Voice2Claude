"""
Voice2Claude - desktop launcher (no pywebview)
Starts the FastAPI server, then opens the app in the default browser.

Does not depend on pywebview, so it works on Python 3.14 too.

Run with:  python app.py
"""

import os
import sys
import time
import socket
import shutil
import threading
import subprocess
import traceback
from contextlib import closing
from pathlib import Path

# In windowed PyInstaller mode, sys.stdout / sys.stderr can be None.
# Anything that writes to them (e.g. print, logging) will crash. Replace
# them with a no-op file handle before importing anything that might log.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

import uvicorn

from server import app  # FastAPI app object from server.py


HOST = "0.0.0.0"          # bind on all interfaces so LAN devices can connect
BROWSER_HOST = "127.0.0.1"  # local browser still opens via loopback
DEFAULT_PORT = 8000


def log_error(msg: str):
    """Write a line to app_error.log next to the executable."""
    try:
        if getattr(sys, "frozen", False):
            log_dir = Path(sys.executable).parent
        else:
            log_dir = Path(__file__).resolve().parent
        with open(log_dir / "app_error.log", "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def find_free_port(preferred: int) -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        try:
            s.bind((HOST, preferred))
            return preferred
        except OSError:
            pass
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def wait_for_server(port: int, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
            s.settimeout(0.2)
            try:
                s.connect((BROWSER_HOST, port))
                return True
            except OSError:
                time.sleep(0.1)
    return False


def find_app_browser():
    candidates = []
    if sys.platform == "win32":
        program_files = [
            os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
            os.environ.get("LOCALAPPDATA", ""),
        ]
        for pf in program_files:
            if not pf:
                continue
            candidates.append((os.path.join(pf, r"Google\Chrome\Application\chrome.exe"), []))
            candidates.append((os.path.join(pf, r"Microsoft\Edge\Application\msedge.exe"), []))
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            candidates.append((os.path.join(local, r"Google\Chrome\Application\chrome.exe"), []))
    elif sys.platform == "darwin":
        candidates.append(("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", []))
        candidates.append(("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge", []))
    else:
        for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
            path = shutil.which(name)
            if path:
                candidates.append((path, []))
    for path, args in candidates:
        if path and os.path.exists(path):
            return (path, args)
    return None


def open_app_window(url: str):
    # Just open in the default browser. We previously tried Chrome / Edge
    # --app mode for an address-bar-less window, but Popen fails silently on
    # some Windows configurations (Defender / EdgeWebView interactions, etc).
    # Default browser is reliable.
    import webbrowser
    webbrowser.open(url)
    return None


def run_server(port: int):
    try:
        config = uvicorn.Config(
            app,
            host=HOST,
            port=port,
            log_level="warning",
            access_log=False,
            log_config=None,
        )
        uvicorn.Server(config).run()
    except Exception:
        log_error("Server thread crashed:\n" + traceback.format_exc())


def main():
    port = find_free_port(DEFAULT_PORT)
    url = f"http://{BROWSER_HOST}:{port}"

    # Log LAN URLs so other devices know what to point at.
    try:
        hostname = socket.gethostname()
        lan_ip = socket.gethostbyname(hostname)
        log_error(f"LAN access: http://{lan_ip}:{port} (also http://{hostname}.local:{port})")
    except Exception:
        pass

    server_thread = threading.Thread(target=run_server, args=(port,), daemon=True)
    server_thread.start()

    if not wait_for_server(port):
        log_error("Server failed to start in time")
        sys.exit(1)

    open_app_window(url)

    # Keep the main thread alive so the daemon server thread keeps running.
    # Exit when user closes the console window or hits Ctrl+C.
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    log_error("=== Startup ===")
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        log_error("FATAL:\n" + traceback.format_exc())
        sys.exit(1)
