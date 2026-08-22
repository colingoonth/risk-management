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
    # FA26 scheduling. Deliberately added as plain nullable TEXT even though
    # 0006 declares it NOT NULL DEFAULT 'confirmed' with a CHECK: SQLite can add
    # neither via ALTER, and a DEFAULT here would fill every legacy row with
    # 'confirmed' before 0015 got the chance to read the real value out of
    # notes. NULL is the signal 0015's back-fill keys on, and it is also what
    # makes that back-fill fire exactly once instead of on every connect.
    ("events", "planning_status", "TEXT"),
    # Unlike planning_status above, this one CAN carry its default: every
    # existing shift type is a risk post and should count, so 1 is right for
    # every legacy row. 0016 then flips dj to 0 exactly once. The CHECK is
    # still lost to ALTER's limitations, as always.
    ("shift_types", "counts_toward_tally", "INTEGER NOT NULL DEFAULT 1"),
    # The strike this shift works off, for databases created before 0017.
    # Fresh ones get it from 0007's CREATE TABLE, which declares it with no
    # REFERENCES clause precisely so the two paths produce the same column —
    # ALTER TABLE cannot attach a foreign key, so declaring one on the CREATE
    # would make a fresh schema diverge from a reconciled one.
    ("shifts", "serves_strike_id", "INTEGER"),
    # Preference-vs-conflict on an unavailability row. Carries its DEFAULT
    # because every pre-existing row is a real conflict, which is what 0 means —
    # unlike planning_status, there is nothing here to derive from other columns.
    ("unavailability", "is_soft", "INTEGER NOT NULL DEFAULT 0"),
    # Effort weight per shift type. Carries its DEFAULT for the same reason
    # counts_toward_tally does: 1.0 is right for every pre-existing row, and
    # 0020 then adjusts the one type that differs.
    ("shift_types", "effort_weight", "REAL NOT NULL DEFAULT 1.0"),
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
