"""Per-entity repository for ``semesters``. Plain functions, no class."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Semester:
    id: int
    name: str
    starts_on: str
    ends_on: str
    pledge_takeover_starts_on: str | None
    is_current: bool
    archived_at: str | None


def _row_to_semester(row: sqlite3.Row) -> Semester:
    return Semester(
        id=row["id"],
        name=row["name"],
        starts_on=row["starts_on"],
        ends_on=row["ends_on"],
        pledge_takeover_starts_on=row["pledge_takeover_starts_on"],
        is_current=bool(row["is_current"]),
        archived_at=row["archived_at"],
    )


def insert(
    conn: sqlite3.Connection,
    *,
    name: str,
    starts_on: str,
    ends_on: str,
    pledge_takeover_starts_on: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO semesters (name, starts_on, ends_on, pledge_takeover_starts_on)
        VALUES (?, ?, ?, ?)
        """,
        (name, starts_on, ends_on, pledge_takeover_starts_on),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def list_all(conn: sqlite3.Connection) -> list[Semester]:
    rows = conn.execute("SELECT * FROM semesters ORDER BY starts_on DESC").fetchall()
    return [_row_to_semester(r) for r in rows]


def get_by_id(conn: sqlite3.Connection, semester_id: int) -> Semester | None:
    row = conn.execute("SELECT * FROM semesters WHERE id = ?", (semester_id,)).fetchone()
    return _row_to_semester(row) if row else None


def get_by_name(conn: sqlite3.Connection, name: str) -> Semester | None:
    row = conn.execute("SELECT * FROM semesters WHERE name = ?", (name,)).fetchone()
    return _row_to_semester(row) if row else None


def get_current(conn: sqlite3.Connection) -> Semester | None:
    row = conn.execute("SELECT * FROM semesters WHERE is_current = 1").fetchone()
    return _row_to_semester(row) if row else None


def set_current(conn: sqlite3.Connection, name: str) -> int:
    """Demote any existing current row, then promote ``name``. Caller wraps in transaction.

    Returns the new current semester's id, or raises ``LookupError`` if not found.
    """
    target = get_by_name(conn, name)
    if target is None:
        raise LookupError(f"No semester named {name!r}")
    conn.execute("UPDATE semesters SET is_current = 0 WHERE is_current = 1")
    conn.execute("UPDATE semesters SET is_current = 1 WHERE id = ?", (target.id,))
    return target.id


def mark_archived(conn: sqlite3.Connection, *, semester_id: int, archived_at: str) -> int:
    cur = conn.execute(
        "UPDATE semesters SET archived_at = ? WHERE id = ? AND archived_at IS NULL",
        (archived_at, semester_id),
    )
    return cur.rowcount


def unarchive(conn: sqlite3.Connection, *, semester_id: int) -> int:
    cur = conn.execute(
        "UPDATE semesters SET archived_at = NULL WHERE id = ? AND archived_at IS NOT NULL",
        (semester_id,),
    )
    return cur.rowcount
