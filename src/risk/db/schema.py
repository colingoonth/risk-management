"""Load and apply schema migrations.

Phase 0 has a single migration file. A real ordered-migration runner with
a ``_schema_migrations`` tracking table lands in a later phase; for now
schema is idempotent via ``CREATE ... IF NOT EXISTS``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def load_schema() -> str:
    """Concatenate all migration SQL files in lexicographic order."""
    parts = []
    for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
        parts.append(sql_file.read_text())
    return "\n".join(parts)


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(load_schema())
