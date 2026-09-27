"""Start the app: ``python run.py`` — or just double-click ``Start Level Stats.bat``.

Handles the things that make a double-click reliable:

* picks the next free port if the preferred one is busy (a stale server from a
  previous run no longer produces a cryptic bind error);
* opens the browser only once the server actually answers, not before;
* prints the version and the folder it is serving from, so there is never any
  doubt about which copy is running.
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

import uvicorn

from app import __version__
from app.config import PROJECT_ROOT

DEFAULT_PORT = 8000
PORT_SEARCH_ATTEMPTS = 12


def is_port_free(host: str, port: int) -> bool:
    """True if we can bind ``port`` on ``host`` right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def find_free_port(host: str, preferred: int, attempts: int = PORT_SEARCH_ATTEMPTS) -> int | None:
    """The preferred port, or the next free one above it. ``None`` if all busy."""
    for port in range(preferred, preferred + attempts):
        if is_port_free(host, port):
            return port
    return None


def open_browser_when_ready(url: str, timeout: float = 25.0) -> None:
    """Poll /healthz in a background thread, then open the browser.

    Opening it before uvicorn binds gives the user a connection error on a page
    that is about to work, which is the worst possible first impression.
    """
    def worker() -> None:
        deadline = time.monotonic() + timeout
        probe = url.rstrip("/") + "/healthz"
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(probe, timeout=1.5) as response:
                    if response.status == 200:
                        webbrowser.open(url)
                        return
            except (urllib.error.URLError, OSError):
                time.sleep(0.35)
        print(f"  (server did not answer within {timeout:.0f}s — open {url} yourself)")

    threading.Thread(target=worker, daemon=True).start()


def banner(host: str, port: int, requested_port: int) -> str:
    url = f"http://{host}:{port}/"
    lines = [
        "",
        f"  Level Stats v{__version__}",
        f"  Serving from : {PROJECT_ROOT}",
        "  Tabs         : One player  |  Compare a list",
        f"  Open         : {url}",
    ]
    if port != requested_port:
        lines.append(
            f"  Note         : port {requested_port} was busy, using {port} instead."
        )
        lines.append("                 An older copy of this app may still be running.")
    lines += ["", "  Leave this window open. Close it (or press Ctrl-C) to stop the app.", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the MLB Level Stats web app.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--reload", action="store_true", help="auto-reload on code changes")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    parser.add_argument(
        "--strict-port",
        action="store_true",
        help="fail instead of moving to the next free port",
    )
    args = parser.parse_args()

    if args.strict_port:
        port = args.port if is_port_free(args.host, args.port) else None
    else:
        port = find_free_port(args.host, args.port)

    if port is None:
        print(
            f"\n  Port {args.port} is already in use"
            + ("" if args.strict_port else f" and so are the next {PORT_SEARCH_ATTEMPTS - 1}")
            + ".\n"
            "  An older copy of this app is probably still running — close its\n"
            "  window, or start this one on a different port:\n\n"
            f"      python run.py --port {args.port + 100}\n"
        )
        return 1

    url = f"http://{args.host}:{port}/"
    print(banner(args.host, port, args.port))

    if not args.no_browser:
        open_browser_when_ready(url)

    try:
        uvicorn.run("app.main:app", host=args.host, port=port, reload=args.reload)
    except KeyboardInterrupt:
        pass
    print("\n  Level Stats stopped.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
