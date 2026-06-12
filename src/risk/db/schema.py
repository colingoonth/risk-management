"""Load and apply schema migrations.

Phase 0 has a single migration file. A real ordered-migration runner with
a ``_schema_migrations`` tracking table lands in a later phase; for now
schema is idempotent via ``CREATE ... IF NOT EXISTS``.

SQLite has no ``ALTER TABLE ... ADD COLUMN IF NOT EXISTS``, and ``ensure_schema``
re-runs the full concatenated script on every connection — so a bare ``ALTER``
in a migration file would raise "duplicate column" on the second open. New
columns are therefore declared in the table's ``CREATE TABLE`` (covering fresh
DBs and the migration-replay parity test) and back-filled onto pre-existing DBs
by ``_ensure_columns``, which is a no-op once the column is present.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def _migrations_dir() -> Path:
    """Locate the migration SQL files.

    Normally they sit next to this module. In a PyInstaller-frozen app the
    package source lives inside the bundle, so the SQL is shipped as data and
    found under ``sys._MEIPASS`` instead (the desktop build collects it to
    ``risk/db/migrations``)."""
    base = getattr(sys, "_MEIPASS", None)
    if base is not None:
        return Path(base) / "risk" / "db" / "migrations"
    return Path(__file__).parent / "migrations"


MIGRATIONS_DIR = _migrations_dir()

# Columns added after their table's original migration shipped. Each entry is
# back-filled onto legacy DBs that predate the column. Fresh DBs already have it
# from the CREATE TABLE, so the ALTER never fires for them (keeps the schema
# byte-identical to a from-files replay).
_RECONCILED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("members", "pledge_class", "TEXT"),
)


def load_schema() -> str:
    """Concatenate all migration SQL files in lexicographic order."""
    parts = []
    for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
        parts.append(sql_file.read_text())
    return "\n".join(parts)


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """Back-fill post-hoc columns onto DBs created before they were declared."""
    for table, column, decl in _RECONCILED_COLUMNS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(load_schema())
    _ensure_columns(conn)
