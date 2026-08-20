"""Run the API in-process and show it in a native window.

This is the packaged Dock-app entry point. Unlike ``python -m risk.api``
(a headless server, two-process dev workflow), this:

  1. picks a free localhost port,
  2. starts uvicorn on it in a background *daemon* thread,
  3. waits for ``/api/health`` to report 200,
  4. opens a native WKWebView window (pywebview) at the same-origin SPA.

pywebview must own the main thread (an AppKit requirement), which is why the
server runs on a daemon thread and uvicorn's signal handlers are disabled
(they can only be installed on the main thread). Closing the window returns
from ``webview.start()`` and the process exits, tearing the daemon server
down with it — no orphaned uvicorn, no IPC, one process.

Single-user, single-window, localhost-only (ADR-014).
"""

from __future__ import annotations

import multiprocessing
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

APP_NAME = "Risk Management"
_HEALTH_TIMEOUT_S = 15.0
_HEALTH_POLL_S = 0.1


def _free_port() -> int:
    """Reserve an ephemeral localhost port. Avoids colliding with a :8000 dev
    server and means the app never needs a fixed port the user must keep clear."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _default_db_path() -> Path:
    """Where the packaged app keeps the chapter's database.

    Delegates to ``db.connection`` rather than restating the path. It used to
    restate it, and the two definitions drifted: the CLI resolved to the XDG
    location while this resolved to Application Support, so the app and the
    shell opened different databases on the same machine and the first bare
    ``risk`` command silently created an empty second chapter.
    """
    from risk.db.connection import DEFAULT_DB_PATH

    return DEFAULT_DB_PATH


def _bundled_static_dir() -> Path | None:
    """When frozen (PyInstaller), the SPA bundle ships alongside the binary;
    locate it via ``sys._MEIPASS``. Returns ``None`` from source, where the app
    factory auto-detects ``web/dist`` itself."""
    base = getattr(sys, "_MEIPASS", None)
    if base is None:
        return None
    candidate = Path(base) / "web_dist"
    return candidate if candidate.is_dir() else None


def _configure_env() -> None:
    """Point the API at the user-data DB and (when frozen) the bundled SPA.
    ``setdefault`` so an explicit ``RISK_DB_PATH`` (e.g. a smoke test) still wins."""
    os.environ.setdefault("RISK_DB_PATH", str(_default_db_path()))
    static = _bundled_static_dir()
    if static is not None:
        os.environ.setdefault("RISK_STATIC_DIR", str(static))


def _bootstrap_db() -> None:
    """Create the DB file, apply the schema, and switch on WAL once, up front.

    On a brand-new empty DB the SPA's first paint fires several /api calls at
    once; left to race, two connections can attempt ``PRAGMA journal_mode=WAL``
    on the still-rollback-mode file simultaneously and one gets
    ``database is locked`` (a mode change needs an exclusive lock and does not
    honour busy_timeout). Doing it once on the main thread, before the server
    accepts requests, makes first-run deterministic; on an existing DB it is a
    cheap idempotent no-op (schema already present, already WAL)."""
    from risk.db.connection import close_conn, connect, resolve_db_path
    from risk.db.schema import ensure_schema

    conn = connect(resolve_db_path(None))
    try:
        ensure_schema(conn)
    finally:
        close_conn(conn)


def _serve(port: int) -> None:
    """Run uvicorn in this daemon thread. uvicorn only installs signal handlers
    on the main thread (its ``capture_signals`` no-ops elsewhere), which pywebview
    owns — so running ``server.run()`` off-thread is safe with no extra wiring."""
    import uvicorn

    from risk.api import create_app

    config = uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    server.run()


def _await_healthy(port: int) -> bool:
    """Poll ``/api/health`` until it returns 200 or the timeout elapses."""
    url = f"http://127.0.0.1:{port}/api/health"
    deadline = time.monotonic() + _HEALTH_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:  # noqa: S310 — localhost
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(_HEALTH_POLL_S)
    return False


def main() -> None:
    multiprocessing.freeze_support()  # frozen-app hygiene; no-op from source
    _configure_env()
    _bootstrap_db()

    import webview

    port = _free_port()
    threading.Thread(target=_serve, args=(port,), daemon=True).start()

    if not _await_healthy(port):
        print(f"{APP_NAME}: API did not become healthy in time", file=sys.stderr)
        raise SystemExit(1)

    webview.create_window(
        APP_NAME,
        f"http://127.0.0.1:{port}/",
        width=1200,
        height=820,
        min_size=(900, 600),
    )
    webview.start()


if __name__ == "__main__":
    main()
