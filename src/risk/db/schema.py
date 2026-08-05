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
    # Phase 7 unavailability v2. Back-filled without the CHECK constraints the
    # CREATE TABLE carries — SQLite cannot add a CHECK via ALTER. Format is
    # validated in the service layer, so legacy DBs are guarded there instead.
    ("unavailability", "starts_at_time", "TEXT"),
    ("unavailability", "ends_at_time", "TEXT"),
    ("unavailability", "repeats_weekday", "INTEGER"),
)


def load_schema() -> str:
    """Concatenate all migration SQL files in lexicographic order."""
    parts = []
    for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
        parts.append(sql_file.read_text())
    return "\n".join(parts)


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """Back-fill post-hoc columns onto DBs created before they were declared.

    A table missing entirely means this is a fresh DB and the migrations have
    not run yet; its ``CREATE TABLE`` already declares the column, so there is
    nothing to back-fill and the ALTER would fail on a non-existent table.
    """
    for table, column, decl in _RECONCILED_COLUMNS:
        cols = list(conn.execute(f"PRAGMA table_info({table})"))
        if not cols:
            continue
        if column not in {row["name"] for row in cols}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def ensure_schema(conn: sqlite3.Connection) -> None:
    # Back-fill BEFORE replaying the migrations: a later migration may build an
    # index over a reconciled column (unavailability's time/recurrence columns
    # do exactly this), and on a pre-existing DB that column would not yet be
    # there if we reconciled afterwards. On a fresh DB every table is absent at
    # this point, so this is a no-op and the CREATE TABLEs supply the columns.
    _ensure_columns(conn)
    conn.executescript(load_schema())
