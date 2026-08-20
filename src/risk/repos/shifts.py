"""Per-entity repository for ``shifts``."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Shift:
    id: int
    event_id: int
    shift_type_id: int
    shift_type_slug: str
    slot_index: int
    assigned_member_id: int | None
    assigned_member_slug: str | None
    effective_pledge_mode_id: int | None
    effective_pledge_mode_slug: str | None
    status: str
    assigned_at: str | None


_SELECT_JOINED = """
SELECT
  s.id, s.event_id, s.shift_type_id, st.slug AS shift_type_slug,
  s.slot_index, s.assigned_member_id, m.slug AS assigned_member_slug,
  s.effective_pledge_mode_id, pm.slug AS effective_pledge_mode_slug,
  s.status, s.assigned_at
FROM shifts s
JOIN shift_types st ON st.id = s.shift_type_id
LEFT JOIN members m ON m.id = s.assigned_member_id
LEFT JOIN pledge_modes pm ON pm.id = s.effective_pledge_mode_id
"""


def _row(r: sqlite3.Row) -> Shift:
    return Shift(
        id=r["id"],
        event_id=r["event_id"],
        shift_type_id=r["shift_type_id"],
        shift_type_slug=r["shift_type_slug"],
        slot_index=r["slot_index"],
        assigned_member_id=r["assigned_member_id"],
        assigned_member_slug=r["assigned_member_slug"],
        effective_pledge_mode_id=r["effective_pledge_mode_id"],
        effective_pledge_mode_slug=r["effective_pledge_mode_slug"],
        status=r["status"],
        assigned_at=r["assigned_at"],
    )


def insert_open(
    conn: sqlite3.Connection,
    *,
    event_id: int,
    shift_type_id: int,
    slot_index: int,
) -> int:
    """Insert an open (unassigned) slot."""
    cur = conn.execute(
        """
        INSERT INTO shifts (event_id, shift_type_id, slot_index, status)
        VALUES (?, ?, ?, 'open')
        """,
        (event_id, shift_type_id, slot_index),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def assign(
    conn: sqlite3.Connection,
    *,
    shift_id: int,
    member_id: int,
    effective_pledge_mode_id: int,
    assigned_at: str,
) -> int:
    """Assign a member to an existing open shift slot.

    Sets status='assigned', snapshots ``effective_pledge_mode_id``, and stamps
    ``assigned_at`` with the caller-provided value. ADR-009 requires this to
    be the event date (not wall-clock now) so the fairness tiebreaker stays
    pinned to ``event.date`` across reruns and cross-event orderings.
    """
    cur = conn.execute(
        """
        UPDATE shifts
        SET assigned_member_id = ?,
            effective_pledge_mode_id = ?,
            status = 'assigned',
            assigned_at = ?
        WHERE id = ? AND status = 'open'
        """,
        (member_id, effective_pledge_mode_id, assigned_at, shift_id),
    )
    return cur.rowcount


def unassign(conn: sqlite3.Connection, *, shift_id: int) -> int:
    cur = conn.execute(
        """
        UPDATE shifts
        SET assigned_member_id = NULL,
            effective_pledge_mode_id = NULL,
            status = 'open',
            assigned_at = NULL
        WHERE id = ?
        """,
        (shift_id,),
    )
    return cur.rowcount


def list_for_event(conn: sqlite3.Connection, event_id: int) -> list[Shift]:
    rows = conn.execute(
        f"{_SELECT_JOINED} WHERE s.event_id = ? ORDER BY st.slug, s.slot_index",
        (event_id,),
    ).fetchall()
    return [_row(r) for r in rows]


def list_filtered(
    conn: sqlite3.Connection,
    *,
    member_id: int | None = None,
    event_id: int | None = None,
    semester_id: int | None = None,
    status: str | None = None,
) -> list[Shift]:
    """Filtered list for ``risk shift list``. Joins event for semester filter + date ordering."""
    clauses: list[str] = []
    params: list[object] = []
    if member_id is not None:
        clauses.append("s.assigned_member_id = ?")
        params.append(member_id)
    if event_id is not None:
        clauses.append("s.event_id = ?")
        params.append(event_id)
    if semester_id is not None:
        clauses.append("ev.semester_id = ?")
        params.append(semester_id)
    if status is not None:
        clauses.append("s.status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = (
        f"{_SELECT_JOINED} "
        "JOIN events ev ON ev.id = s.event_id "
        f"{where} "
        "ORDER BY ev.date, st.slug, s.slot_index"
    )
    rows = conn.execute(sql, params).fetchall()
    return [_row(r) for r in rows]


def get_by_id(conn: sqlite3.Connection, shift_id: int) -> Shift | None:
    row = conn.execute(f"{_SELECT_JOINED} WHERE s.id = ?", (shift_id,)).fetchone()
    return _row(row) if row else None


def event_id_of(conn: sqlite3.Connection, shift_id: int) -> int | None:
    row = conn.execute("SELECT event_id FROM shifts WHERE id = ?", (shift_id,)).fetchone()
    return int(row["event_id"]) if row else None


def open_slots_for_event(
    conn: sqlite3.Connection, *, event_id: int, shift_type_id: int
) -> list[Shift]:
    rows = conn.execute(
        f"{_SELECT_JOINED} "
        f"WHERE s.event_id = ? AND s.shift_type_id = ? AND s.status = 'open' "
        f"ORDER BY s.slot_index",
        (event_id, shift_type_id),
    ).fetchall()
    return [_row(r) for r in rows]


def count_assignments_in_semester_through_date(
    conn: sqlite3.Connection, *, member_id: int, semester_id: int, on_or_before: str
) -> int:
    """Count ROTATION shifts for ``member_id`` in ``semester_id``, on or before
    ``on_or_before``.

    Used by fairness to compute "shifts so far" pinned to event.date (ADR-009).

    Two kinds of shift are deliberately excluded, because the chapter does not
    consider either of them a turn in the rotation:

    ``counts_toward_tally = 0`` — DJ. A different job, not risk work. Counting
    it let the two qualified members absorb all 43 DJ nights, price themselves
    out of every other pool by roughly the sixth party, and then top the shift
    ledger having stood no risk shifts at all. See migration 0016.

    Shifts serving a strike. A make-up shift is a penalty, so counting it would
    refund the penalty: the extra work would push the member down the queue and
    take a rotation shift off him, leaving his season load unchanged and the
    strike costing him nothing. ``strikes.shift_id`` is the link, and it is what
    that column was declared for.

    Both exclusions are joins rather than slug tests so the rule lives in data.
    NOT EXISTS rather than a LEFT JOIN because a shift could in principle serve
    more than one strike, and a join would then count the row twice — in the one
    direction that silently under-counts the member's real load.
    """
    row = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM shifts s
        JOIN events e ON e.id = s.event_id
        JOIN shift_types st ON st.id = s.shift_type_id
        WHERE s.assigned_member_id = ?
          AND e.semester_id = ?
          AND e.date <= ?
          AND st.counts_toward_tally = 1
          AND NOT EXISTS (SELECT 1 FROM strikes k WHERE k.shift_id = s.id)
        """,
        (member_id, semester_id, on_or_before),
    ).fetchone()
    return int(row["n"]) if row else 0


def count_of_shift_type_in_semester_through_date(
    conn: sqlite3.Connection,
    *,
    member_id: int,
    semester_id: int,
    shift_type_id: int,
    on_or_before: str,
) -> int:
    """How many times ``member_id`` has worked ONE shift type this semester.

    The rotation key for gated shift types, and it is not optional.

    Removing DJ from the fairness tally has a consequence that is easy to miss:
    the tally was the only thing alternating the two DJs. Once DJ nights stop
    counting, both DJs stay tied on every other key for the whole term, and a
    stable sort hands every single one of the 43 nights to whichever of them
    sorts first — verified, 43 to 0. That is strictly worse than the bug it was
    meant to fix.

    Ranking the gated pool by its own type's count restores the alternation
    without putting DJ back into the general tally. Unlike the tally, this
    counts every shift of the type including strike make-ups: the question here
    is "whose turn is it to DJ", and a night spent DJing is a night spent DJing
    whatever else it also settled.
    """
    row = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM shifts s
        JOIN events e ON e.id = s.event_id
        WHERE s.assigned_member_id = ?
          AND e.semester_id = ?
          AND s.shift_type_id = ?
          AND e.date <= ?
        """,
        (member_id, semester_id, shift_type_id, on_or_before),
    ).fetchone()
    return int(row["n"]) if row else 0


def last_assigned_at(conn: sqlite3.Connection, member_id: int) -> str | None:
    """Most-recent ``assigned_at`` across ALL semesters for ``member_id``.

    Intentionally global, not semester-scoped — the tiebreaker carries
    cross-semester memory so a member who finished last semester with three
    back-to-back shifts isn't picked first the very first event of this one.
    """
    row = conn.execute(
        """
        SELECT MAX(assigned_at) AS last
        FROM shifts
        WHERE assigned_member_id = ?
        """,
        (member_id,),
    ).fetchone()
    return row["last"] if row and row["last"] is not None else None


def delete_open_for_event(conn: sqlite3.Connection, event_id: int) -> int:
    """Remove all open (unassigned) slots for an event. Used by --reassign."""
    cur = conn.execute(
        "DELETE FROM shifts WHERE event_id = ? AND status = 'open'",
        (event_id,),
    )
    return cur.rowcount
