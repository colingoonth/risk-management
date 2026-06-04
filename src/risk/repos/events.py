"""Per-entity repository for ``events``."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Event:
    id: int
    semester_id: int
    event_type_id: int
    host_house_id: int | None
    display_name: str
    date: str
    start_time: str | None
    end_time: str | None
    status: str
    resync_pending: bool
    notes: str | None
    semester_name: str
    event_type_slug: str
    host_house_slug: str | None


_SELECT_JOINED = """
SELECT
  e.id, e.semester_id, e.event_type_id, e.host_house_id,
  e.display_name, e.date, e.start_time, e.end_time,
  e.status, e.resync_pending, e.notes,
  s.name AS semester_name,
  et.slug AS event_type_slug,
  h.slug AS host_house_slug
FROM events e
JOIN semesters s ON s.id = e.semester_id
JOIN event_types et ON et.id = e.event_type_id
LEFT JOIN houses h ON h.id = e.host_house_id
"""


def _row(r: sqlite3.Row) -> Event:
    return Event(
        id=r["id"],
        semester_id=r["semester_id"],
        event_type_id=r["event_type_id"],
        host_house_id=r["host_house_id"],
        display_name=r["display_name"],
        date=r["date"],
        start_time=r["start_time"],
        end_time=r["end_time"],
        status=r["status"],
        resync_pending=bool(r["resync_pending"]),
        notes=r["notes"],
        semester_name=r["semester_name"],
        event_type_slug=r["event_type_slug"],
        host_house_slug=r["host_house_slug"],
    )


def insert(
    conn: sqlite3.Connection,
    *,
    semester_id: int,
    event_type_id: int,
    display_name: str,
    date: str,
    host_house_id: int | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    notes: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO events
          (semester_id, event_type_id, display_name, date,
           host_house_id, start_time, end_time, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            semester_id,
            event_type_id,
            display_name,
            date,
            host_house_id,
            start_time,
            end_time,
            notes,
        ),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def get_by_id(conn: sqlite3.Connection, event_id: int) -> Event | None:
    row = conn.execute(f"{_SELECT_JOINED} WHERE e.id = ?", (event_id,)).fetchone()
    return _row(row) if row else None


def get_by_semester_and_name(
    conn: sqlite3.Connection, *, semester_id: int, display_name: str
) -> Event | None:
    row = conn.execute(
        f"{_SELECT_JOINED} WHERE e.semester_id = ? AND e.display_name = ?",
        (semester_id, display_name),
    ).fetchone()
    return _row(row) if row else None


def list_for_semester(
    conn: sqlite3.Connection,
    semester_id: int,
    *,
    status: str | None = None,
) -> list[Event]:
    if status is not None:
        rows = conn.execute(
            f"{_SELECT_JOINED} WHERE e.semester_id = ? AND e.status = ? ORDER BY e.date, e.start_time",
            (semester_id, status),
        ).fetchall()
    else:
        rows = conn.execute(
            f"{_SELECT_JOINED} WHERE e.semester_id = ? ORDER BY e.date, e.start_time",
            (semester_id,),
        ).fetchall()
    return [_row(r) for r in rows]


def update_status(conn: sqlite3.Connection, *, event_id: int, status: str) -> int:
    cur = conn.execute("UPDATE events SET status = ? WHERE id = ?", (status, event_id))
    return cur.rowcount


def update_host(conn: sqlite3.Connection, *, event_id: int, host_house_id: int | None) -> int:
    cur = conn.execute(
        "UPDATE events SET host_house_id = ? WHERE id = ?",
        (host_house_id, event_id),
    )
    return cur.rowcount


def clear_resync_pending(conn: sqlite3.Connection, event_id: int) -> int:
    cur = conn.execute("UPDATE events SET resync_pending = 0 WHERE id = ?", (event_id,))
    return cur.rowcount


def resolve(conn: sqlite3.Connection, key: str) -> Event | None:
    """Resolve to an event by id or display_name in the current semester."""
    if key.isdigit():
        return get_by_id(conn, int(key))
    row = conn.execute(
        f"{_SELECT_JOINED} JOIN semesters cs ON cs.id = e.semester_id AND cs.is_current = 1 "
        f"WHERE e.display_name = ?",
        (key,),
    ).fetchone()
    return _row(row) if row else None
