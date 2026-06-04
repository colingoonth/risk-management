"""Per-entity repository for ``event_types`` and its allow-list."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EventType:
    id: int
    slug: str
    display_name: str


def _row(r: sqlite3.Row) -> EventType:
    return EventType(id=r["id"], slug=r["slug"], display_name=r["display_name"])


def insert(conn: sqlite3.Connection, *, slug: str, display_name: str) -> int:
    cur = conn.execute(
        "INSERT INTO event_types (slug, display_name) VALUES (?, ?)",
        (slug, display_name),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def list_all(conn: sqlite3.Connection) -> list[EventType]:
    rows = conn.execute("SELECT * FROM event_types ORDER BY slug").fetchall()
    return [_row(r) for r in rows]


def get_by_slug(conn: sqlite3.Connection, slug: str) -> EventType | None:
    row = conn.execute("SELECT * FROM event_types WHERE slug = ?", (slug,)).fetchone()
    return _row(row) if row else None


def allow_shift_type(conn: sqlite3.Connection, *, event_type_id: int, shift_type_id: int) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO event_type_shift_type_allowed (event_type_id, shift_type_id)
        VALUES (?, ?)
        """,
        (event_type_id, shift_type_id),
    )


def disallow_shift_type(
    conn: sqlite3.Connection, *, event_type_id: int, shift_type_id: int
) -> None:
    conn.execute(
        """
        DELETE FROM event_type_shift_type_allowed
        WHERE event_type_id = ? AND shift_type_id = ?
        """,
        (event_type_id, shift_type_id),
    )


def list_allowed_shift_types(conn: sqlite3.Connection, event_type_id: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT st.slug AS slug
        FROM event_type_shift_type_allowed a
        JOIN shift_types st ON st.id = a.shift_type_id
        WHERE a.event_type_id = ?
        ORDER BY st.slug
        """,
        (event_type_id,),
    ).fetchall()
    return [r["slug"] for r in rows]
