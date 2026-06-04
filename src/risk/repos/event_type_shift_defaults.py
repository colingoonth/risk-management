"""Per-entity repository for ``event_type_shift_defaults`` (Layer 3 of the merge stack)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EventTypeShiftDefault:
    event_type_id: int
    shift_type_id: int
    event_type_slug: str
    shift_type_slug: str
    min_count: int
    target_count: int


def _row(r: sqlite3.Row) -> EventTypeShiftDefault:
    return EventTypeShiftDefault(
        event_type_id=r["event_type_id"],
        shift_type_id=r["shift_type_id"],
        event_type_slug=r["event_type_slug"],
        shift_type_slug=r["shift_type_slug"],
        min_count=r["min_count"],
        target_count=r["target_count"],
    )


def upsert(
    conn: sqlite3.Connection,
    *,
    event_type_id: int,
    shift_type_id: int,
    min_count: int,
    target_count: int,
) -> None:
    conn.execute(
        """
        INSERT INTO event_type_shift_defaults
          (event_type_id, shift_type_id, min_count, target_count)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (event_type_id, shift_type_id) DO UPDATE SET
          min_count = excluded.min_count,
          target_count = excluded.target_count
        """,
        (event_type_id, shift_type_id, min_count, target_count),
    )


def delete(conn: sqlite3.Connection, *, event_type_id: int, shift_type_id: int) -> int:
    cur = conn.execute(
        """
        DELETE FROM event_type_shift_defaults
        WHERE event_type_id = ? AND shift_type_id = ?
        """,
        (event_type_id, shift_type_id),
    )
    return cur.rowcount


def list_for_event_type(
    conn: sqlite3.Connection, event_type_id: int
) -> list[EventTypeShiftDefault]:
    rows = conn.execute(
        """
        SELECT
          d.event_type_id, d.shift_type_id,
          et.slug AS event_type_slug, st.slug AS shift_type_slug,
          d.min_count, d.target_count
        FROM event_type_shift_defaults d
        JOIN event_types et ON et.id = d.event_type_id
        JOIN shift_types st ON st.id = d.shift_type_id
        WHERE d.event_type_id = ?
        ORDER BY st.slug
        """,
        (event_type_id,),
    ).fetchall()
    return [_row(r) for r in rows]


def list_all(conn: sqlite3.Connection) -> list[EventTypeShiftDefault]:
    rows = conn.execute(
        """
        SELECT
          d.event_type_id, d.shift_type_id,
          et.slug AS event_type_slug, st.slug AS shift_type_slug,
          d.min_count, d.target_count
        FROM event_type_shift_defaults d
        JOIN event_types et ON et.id = d.event_type_id
        JOIN shift_types st ON st.id = d.shift_type_id
        ORDER BY et.slug, st.slug
        """
    ).fetchall()
    return [_row(r) for r in rows]
