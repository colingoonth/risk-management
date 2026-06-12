"""Request-scoped dependencies for the API layer."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

from fastapi import Request

from risk.db.connection import close_conn, connect, resolve_db_path
from risk.db.schema import ensure_schema


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    """Open a per-request SQLite connection (WAL → safe for concurrent reads).

    DB path resolution mirrors the CLI: an explicit ``app.state.db_path`` wins
    (set by tests / ``create_app``), otherwise ``resolve_db_path`` honours the
    ``RISK_DB_PATH`` env + XDG defaults.
    """
    db_path = getattr(request.app.state, "db_path", None) or resolve_db_path(None)
    # cross_thread: FastAPI may run this dependency's setup and the endpoint on
    # different threadpool threads; the per-request connection is still used by
    # only one request at a time, so relaxing the same-thread check is safe.
    conn = connect(db_path, cross_thread=True)
    ensure_schema(conn)
    try:
        yield conn
    finally:
        close_conn(conn)
