"""Per-entity repository for ``unavailability`` (Phase 6)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class UnavailabilityWindow:
    id: int
    member_id: int
    semester_id: int
    starts_on: str
    ends_on: str
    reason: str | None
    member_slug: str
    # v2 (migration 0012). Times are HH:MM or both NULL, which means ALL DAY.
    starts_at_time: str | None = None
    ends_at_time: str | None = None
    # 0=Monday .. 6=Sunday. NULL means the one-off range starts_on..ends_on
    # inclusive. Set means only that weekday within the range.
    repeats_weekday: int | None = None


_SELECT_JOINED = """
SELECT
  u.id, u.member_id, u.semester_id, u.starts_on, u.ends_on, u.reason,
  u.starts_at_time, u.ends_at_time, u.repeats_weekday,
  m.slug AS member_slug
FROM unavailability u
JOIN members m ON m.id = u.member_id
"""


def _row(r: sqlite3.Row) -> UnavailabilityWindow:
    return UnavailabilityWindow(
        id=r["id"],
        member_id=r["member_id"],
        semester_id=r["semester_id"],
        starts_on=r["starts_on"],
        ends_on=r["ends_on"],
        reason=r["reason"],
        member_slug=r["member_slug"],
        starts_at_time=r["starts_at_time"],
        ends_at_time=r["ends_at_time"],
        repeats_weekday=r["repeats_weekday"],
    )


def insert(
    conn: sqlite3.Connection,
    *,
    member_id: int,
    semester_id: int,
    starts_on: str,
    ends_on: str,
    reason: str | None = None,
    starts_at_time: str | None = None,
    ends_at_time: str | None = None,
    repeats_weekday: int | None = None,
) -> int:
    """Insert an unavailability window.

    Omitting both times means all day. The table CHECKs that the times are
    either both present or both absent — a half-specified window is always a
    data bug, not a shorthand.
    """
    cur = conn.execute(
        """
        INSERT INTO unavailability
          (member_id, semester_id, starts_on, ends_on, reason,
           starts_at_time, ends_at_time, repeats_weekday)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            member_id,
            semester_id,
            starts_on,
            ends_on,
            reason,
            starts_at_time,
            ends_at_time,
            repeats_weekday,
        ),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def get_by_id(conn: sqlite3.Connection, win_id: int) -> UnavailabilityWindow | None:
    row = conn.execute(f"{_SELECT_JOINED} WHERE u.id = ?", (win_id,)).fetchone()
    return _row(row) if row else None


def list_for_member_semester(
    conn: sqlite3.Connection, *, member_id: int, semester_id: int
) -> list[UnavailabilityWindow]:
    rows = conn.execute(
        f"{_SELECT_JOINED} WHERE u.member_id = ? AND u.semester_id = ? "
        "ORDER BY u.starts_on, u.id",
        (member_id, semester_id),
    ).fetchall()
    return [_row(r) for r in rows]


def list_for_semester(
    conn: sqlite3.Connection, *, semester_id: int
) -> list[UnavailabilityWindow]:
    rows = conn.execute(
        f"{_SELECT_JOINED} WHERE u.semester_id = ? ORDER BY u.starts_on, u.member_id",
        (semester_id,),
    ).fetchall()
    return [_row(r) for r in rows]


def delete(conn: sqlite3.Connection, win_id: int) -> int:
    cur = conn.execute("DELETE FROM unavailability WHERE id = ?", (win_id,))
    return cur.rowcount


def member_ids_unavailable_on(
    conn: sqlite3.Connection, *, semester_id: int, date: str
) -> set[int]:
    """Member ids with an unavailability window covering ``date`` (inclusive).

    A one-off row (``repeats_weekday IS NULL``) covers every day in its range. A
    recurring row covers only the days in that range whose weekday matches, so
    "busy every Thursday, all semester" blocks Thursday parties and leaves the
    Friday ones alone. Same rule as ``services.availability._applies_on``; the
    two must agree, because this is the coarse filter in front of it.

    SQLite's ``strftime('%w')`` counts from Sunday=0 and ``repeats_weekday``
    stores Python's Monday=0, hence the ``+ 6) % 7`` rotation. A ``date`` that
    is not a parseable ISO date makes ``strftime`` return NULL, which matches no
    recurring row — one-off rows still compare as plain strings, exactly as
    before.

    NOTE: this is date granularity, so a row carrying ``starts_at_time`` /
    ``ends_at_time`` still blocks the whole day here. Narrowing that needs the
    per-shift-type window from ``services.availability`` and is a separate piece
    of work; blocking the day is the conservative direction in the meantime.
    """
    rows = conn.execute(
        """
        SELECT DISTINCT member_id FROM unavailability
        WHERE semester_id = ?
          AND starts_on <= ? AND ends_on >= ?
          AND (
            repeats_weekday IS NULL
            OR repeats_weekday = (CAST(strftime('%w', ?) AS INTEGER) + 6) % 7
          )
        """,
        (semester_id, date, date, date),
    ).fetchall()
    return {int(r["member_id"]) for r in rows}
