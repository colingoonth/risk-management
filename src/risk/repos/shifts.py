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
    # The name a human reads. Every shift-bearing surface — this repo, ShiftOut,
    # GET /api/shifts, `risk shift list`, the web drawer — carried only the slug
    # until now, so anything printing a schedule showed `first-last`, and
    # an exporter had no choice but to re-derive names by splitting on '-'.
    assigned_member_display_name: str | None
    effective_pledge_mode_id: int | None
    effective_pledge_mode_slug: str | None
    status: str
    assigned_at: str | None
    serves_strike_id: int | None
    chair_set: bool = False
    """Placed by a human. A rebuild preserves it; the fill never sets it."""


_SELECT_JOINED = """
SELECT
  s.id, s.event_id, s.shift_type_id, st.slug AS shift_type_slug,
  s.slot_index, s.assigned_member_id, m.slug AS assigned_member_slug,
  m.display_name AS assigned_member_display_name,
  s.effective_pledge_mode_id, pm.slug AS effective_pledge_mode_slug,
  s.status, s.assigned_at, s.serves_strike_id, s.chair_set
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
        assigned_member_display_name=r["assigned_member_display_name"],
        effective_pledge_mode_id=r["effective_pledge_mode_id"],
        effective_pledge_mode_slug=r["effective_pledge_mode_slug"],
        status=r["status"],
        assigned_at=r["assigned_at"],
        serves_strike_id=r["serves_strike_id"],
        chair_set=bool(r["chair_set"]),
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
    serves_strike_id: int | None = None,
    chair_set: bool = False,
) -> int:
    """Assign a member to an existing open shift slot.

    Sets status='assigned', snapshots ``effective_pledge_mode_id``, and stamps
    ``assigned_at`` with the caller-provided value. ADR-009 requires this to
    be the event date (not wall-clock now) so the fairness tiebreaker stays
    pinned to ``event.date`` across reruns and cross-event orderings.

    ``serves_strike_id`` marks the assignment as a strike make-up, which keeps
    it out of the member's fairness tally — a penalty is work on top of a normal
    season, and counting it would refund it. Written here rather than in a
    follow-up UPDATE so a make-up can never briefly exist as an ordinary shift;
    the partial unique index in 0017 rejects a second shift against the same
    strike, and it must see the value at INSERT time to do that.
    """
    cur = conn.execute(
        """
        UPDATE shifts
        SET assigned_member_id = ?,
            effective_pledge_mode_id = ?,
            status = 'assigned',
            assigned_at = ?,
            serves_strike_id = ?,
            chair_set = ?
        WHERE id = ? AND status = 'open'
        """,
        (
            member_id,
            effective_pledge_mode_id,
            assigned_at,
            serves_strike_id,
            int(chair_set),
            shift_id,
        ),
    )
    return cur.rowcount


def unassign(conn: sqlite3.Connection, *, shift_id: int) -> int:
    """Free a slot, returning it to 'open'.

    Clears ``serves_strike_id`` along with the member. The strike and the person
    are one fact: a slot nobody is standing works nobody's strike off. Leaving
    the link behind would hold the strike against a shift that is now empty —
    the unique index would then refuse to place that member's make-up anywhere
    else, and the strike would silently become unservable for the rest of the
    term.
    """
    cur = conn.execute(
        """
        UPDATE shifts
        SET assigned_member_id = NULL,
            effective_pledge_mode_id = NULL,
            status = 'open',
            assigned_at = NULL,
            serves_strike_id = NULL,
            chair_set = 0
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


def rotation_effort_in_semester_through_date(
    conn: sqlite3.Connection, *, member_id: int, semester_id: int, on_or_before: str
) -> float:
    """ROTATION effort for ``member_id`` in ``semester_id``, on or before
    ``on_or_before``.

    EFFORT, not a headcount. Each shift contributes its type's
    ``effort_weight``: a setup is 0.7 of a party night, because it is a two-hour
    block the member picks in daylight rather than 20:00-23:59 sober at a party.
    So somebody working mostly setup takes more turns to reach the same quota,
    which is the whole point — it is how "give the sports guys extra in exchange
    for setup" is expressed as a rule about the JOB rather than about them.

    Used by fairness to compute "shifts so far" pinned to event.date (ADR-009).

    Two kinds of shift are deliberately excluded, because the chapter does not
    consider either of them a turn in the rotation:

    ``counts_toward_tally = 0`` — DJ. A different job, not risk work. Counting
    it let the two qualified members absorb all 43 DJ nights, price themselves
    out of every other pool by roughly the sixth party, and then top the shift
    ledger having stood no risk shifts at all. See migration 0016.

    ``serves_strike_id IS NOT NULL`` — a make-up. Counting a penalty would
    refund it: the extra work would push the member down the queue and take a
    rotation shift back off him, leaving his season load unchanged and the
    strike costing him nothing.

    Note this is NOT ``strikes.shift_id``, which points the other way — that
    column records the shift a member NO-SHOWED, the cause of a strike rather
    than its remedy (see migration 0017).

    Both exclusions read from data rather than testing a slug, so the rule is
    something the chair can inspect and change.
    """
    row = conn.execute(
        """
        SELECT COALESCE(SUM(st.effort_weight), 0.0) AS n
        FROM shifts s
        JOIN events e ON e.id = s.event_id
        JOIN shift_types st ON st.id = s.shift_type_id
        WHERE s.assigned_member_id = ?
          AND e.semester_id = ?
          AND e.date <= ?
          AND st.counts_toward_tally = 1
          AND s.serves_strike_id IS NULL
        """,
        (member_id, semester_id, on_or_before),
    ).fetchone()
    return float(row["n"]) if row else 0.0


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
