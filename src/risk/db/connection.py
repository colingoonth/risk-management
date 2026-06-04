"""SQLite connection layer.

All connections opened through this module apply the canonical PRAGMAs:
    foreign_keys=ON, journal_mode=WAL, synchronous=NORMAL, busy_timeout=5000.

Dates are stored as ISO 8601 TEXT (`YYYY-MM-DD`); times as `HH:MM`.

Writes go through ``transaction()`` which wraps ``BEGIN IMMEDIATE`` so two
concurrent writers serialize cleanly. ``SQLITE_BUSY`` raised by a contended
``BEGIN IMMEDIATE`` is retried with exponential backoff up to 3 attempts.
"""

from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

DEFAULT_DB_PATH = Path("data/risk.db")
BUSY_TIMEOUT_MS = 5000
RETRY_MAX_ATTEMPTS = 3
RETRY_BASE_DELAY_S = 0.05


def resolve_db_path(explicit: Path | None = None) -> Path:
    """Resolution order: explicit arg > RISK_DB_PATH env > default."""
    if explicit is not None:
        return explicit
    env = os.environ.get("RISK_DB_PATH")
    if env:
        return Path(env)
    return DEFAULT_DB_PATH


def connect(db_path: Path) -> sqlite3.Connection:
    """Open a connection with all canonical PRAGMAs applied."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    return conn


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
