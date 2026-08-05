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


@dataclass(frozen=True, slots=True)
class EventWithFill(Event):
    """An event plus its staffing rollup — what the calendar reads at a glance.

    ``target_slots`` is SUM(event_shift_requirements.target_count) and is computed
    independently of ``shifts``. That independence is the whole point: shifts rows
    are created LAZILY by auto-assign, so an event nobody has assigned yet has ZERO
    shift rows and must still report its real target. 0 of 13, never 0 of 0 — the
    latter reads as "fully staffed" in any fraction or bar.

    ``assigned_slots`` is RAW and MAY EXCEED ``target_slots``. Lowering a
    requirement target leaves orphaned assigned shifts behind (a known, reported,
    still-open defect). The excess is surfaced as ``orphan_slots`` rather than
    clamped away: clamping would render a 13-of-10 event as "10/10, nothing to
    do" while thirteen brothers each believe they are working.

    ``open_slots`` IS clamped at zero — a negative shortfall is not a thing, and
    every consumer keys "needs attention" off ``open_slots > 0``. Raw counts are
    facts and go out unclamped; derived judgements are clamped. Both are named.
    """

    target_slots: int
    assigned_slots: int
    open_slots: int
    orphan_slots: int


# Two CORRELATED SCALAR SUBQUERIES, deliberately — not a LEFT JOIN with GROUP BY.
# Each aggregate collapses to a single value before it can ever meet the other, so
# row fan-out is impossible by construction rather than by careful grouping. The
# naive one-pass join returns 65/169 for a fully-staffed mixer (5 requirement rows
# x 13 shift rows) while getting the unstaffed case right by luck — which is
# exactly how that bug reaches production unnoticed.
#
# `events` is the sole driving table, so no event can be dropped by a missing
# requirement or a missing shift.
_SELECT_WITH_FILL = """
SELECT
  e.id, e.semester_id, e.event_type_id, e.host_house_id,
  e.display_name, e.date, e.start_time, e.end_time,
  e.status, e.resync_pending, e.notes,
  s.name AS semester_name,
  et.slug AS event_type_slug,
  h.slug AS host_house_slug,
  (SELECT COALESCE(SUM(r.target_count), 0)
     FROM event_shift_requirements r
    WHERE r.event_id = e.id) AS target_slots,
  (SELECT COUNT(*)
     FROM shifts sh
    WHERE sh.event_id = e.id
      AND sh.assigned_member_id IS NOT NULL) AS assigned_slots
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


def _row_with_fill(r: sqlite3.Row) -> EventWithFill:
    """Spelled out longhand rather than delegating to ``_row``.

    ``Event`` has no fill fields and every other reader depends on that, so the
    two constructors stay separate instead of one growing optional arguments.
    """
    target = int(r["target_slots"])
    assigned = int(r["assigned_slots"])
    return EventWithFill(
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
        target_slots=target,
        assigned_slots=assigned,
        open_slots=max(target - assigned, 0),
        orphan_slots=max(assigned - target, 0),
    )


def list_for_semester_with_fill(
    conn: sqlite3.Connection,
    semester_id: int,
    *,
    status: str | None = None,
) -> list[EventWithFill]:
    """Every event in a semester with its staffing rollup, in ONE statement.

    Sibling of :func:`list_for_semester`; the calendar needs 43 events and their
    fill in one round trip, and the per-event loop the dashboard uses would be 87.

    ``ORDER BY ... e.id`` is load-bearing, not decoration: several events can
    share a date and ``start_time`` is NULL on every FA26 row, so without the
    final tiebreak SQLite is free to return same-date events in a different order
    each time and the calendar's cells would shuffle between reloads.
    """
    if status is not None:
        rows = conn.execute(
            f"{_SELECT_WITH_FILL} WHERE e.semester_id = ? AND e.status = ? "
            "ORDER BY e.date, e.start_time, e.id",
            (semester_id, status),
        ).fetchall()
    else:
        rows = conn.execute(
            f"{_SELECT_WITH_FILL} WHERE e.semester_id = ? "
            "ORDER BY e.date, e.start_time, e.id",
            (semester_id,),
        ).fetchall()
    return [_row_with_fill(r) for r in rows]


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
