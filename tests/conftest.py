"""Shared pytest fixtures."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from risk.db.connection import connect
from risk.db.schema import ensure_schema


@pytest.fixture()
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """File-backed SQLite DB with full schema applied. WAL mode is real."""
    conn = connect(tmp_path / "risk.db")
    ensure_schema(conn)
    try:
        yield conn
    finally:
        conn.close()
