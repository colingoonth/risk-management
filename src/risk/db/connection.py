"""SQLite connection layer.

All connections opened through this module apply the canonical PRAGMAs:
    foreign_keys=ON, journal_mode=WAL, synchronous=NORMAL, busy_timeout=5000,
    temp_store=MEMORY, cache_size=-64000.

Dates are stored as ISO 8601 TEXT (`YYYY-MM-DD`); times as `HH:MM`.

Writes go through ``transaction()`` which wraps ``BEGIN IMMEDIATE`` so two
concurrent writers serialize cleanly. ``SQLITE_BUSY`` raised by a contended
``BEGIN IMMEDIATE`` is retried with exponential backoff up to 3 attempts.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_xdg_data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
DEFAULT_DB_PATH = _xdg_data / "risk" / "risk.db"

BUSY_TIMEOUT_MS = 5000
RETRY_MAX_ATTEMPTS = 3
RETRY_BASE_DELAY_S = 0.05

# Legacy cwd-relative path used before XDG migration.
_LEGACY_DB_PATH = Path("data/risk.db")


def resolve_db_path(explicit: Path | None = None) -> Path:
    """Resolution order: explicit arg > RISK_DB_PATH env > legacy fallback > default.

    If the legacy ``./data/risk.db`` exists in the cwd and the XDG path does
    not yet exist, a one-time warning is printed to stderr and the old path is
    used to avoid silent data loss.
    """
    if explicit is not None:
        return explicit
    env = os.environ.get("RISK_DB_PATH")
    if env:
        return Path(env)
    # Migration fallback: legacy path exists AND new XDG path does not.
    if _LEGACY_DB_PATH.exists() and not DEFAULT_DB_PATH.exists():
        print(
            f"Warning: found existing DB at {_LEGACY_DB_PATH} — using it. "
            f"To migrate to the standard location,\n"
            f"run: mv {_LEGACY_DB_PATH} {DEFAULT_DB_PATH} (or set RISK_DB_PATH)",
            file=sys.stderr,
        )
        return _LEGACY_DB_PATH
    return DEFAULT_DB_PATH


def connect(db_path: Path, *, cross_thread: bool = False) -> sqlite3.Connection:
    """Open a connection with all canonical PRAGMAs applied.

    ``cross_thread=True`` (used by the API layer) relaxes sqlite3's
    same-thread check. FastAPI runs sync dependencies and endpoints in a
    threadpool and may place the connection's creation and its use on
    different threads; since each request still owns its own connection,
    this is safe. The CLI keeps the default strict check.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not db_path.exists() or db_path.stat().st_size == 0
    conn = sqlite3.connect(
        db_path,
        isolation_level=None,
        timeout=BUSY_TIMEOUT_MS / 1000,
        check_same_thread=not cross_thread,
    )
    if is_new:
        db_path.chmod(0o600)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA cache_size = -64000")
    return conn


def close_conn(conn: sqlite3.Connection) -> None:
    """Run PRAGMA optimize then close.  Call this instead of conn.close() directly."""
    conn.execute("PRAGMA analysis_limit = 400")
    conn.execute("PRAGMA optimize")
    conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Wrap a write in ``BEGIN IMMEDIATE`` with retry-on-busy."""
    last_exc: sqlite3.OperationalError | None = None
    for attempt in range(RETRY_MAX_ATTEMPTS):
        try:
            conn.execute("BEGIN IMMEDIATE")
            break
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc) and "busy" not in str(exc):
                raise
            last_exc = exc
            time.sleep(RETRY_BASE_DELAY_S * (2**attempt))
    else:
        assert last_exc is not None
        raise last_exc

    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def init_schema(conn: sqlite3.Connection, schema_sql: str) -> None:
    """Apply schema DDL. Idempotent — schema uses ``CREATE TABLE IF NOT EXISTS``."""
    conn.executescript(schema_sql)
