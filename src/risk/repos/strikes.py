"""Per-entity repository for ``strikes`` (Phase 5)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Strike:
    id: int
    member_id: int
    semester_id: int
    shift_id: int | None
    issued_on: str
    reason: str
    carried_from_strike_id: int | None
    closed_at: str | None


@dataclass(frozen=True, slots=True)
class NumberedStrike:
    """Open strike with its derived `strike_number` from `v_strike_numbers`."""

    id: int
    member_id: int
    semester_id: int
    strike_number: int
    issued_on: str
    reason: str


def _row(r: sqlite3.Row) -> Strike:
    return Strike(
        id=r["id"],
        member_id=r["member_id"],
        semester_id=r["semester_id"],
        shift_id=r["shift_id"],
        issued_on=r["issued_on"],
        reason=r["reason"],
        carried_from_strike_id=r["carried_from_strike_id"],
        closed_at=r["closed_at"],
    )


def _numbered(r: sqlite3.Row) -> NumberedStrike:
    return NumberedStrike(
        id=r["id"],
        member_id=r["member_id"],
        semester_id=r["semester_id"],
        strike_number=r["strike_number"],
        issued_on=r["issued_on"],
        reason=r["reason"],
    )


def insert(
    conn: sqlite3.Connection,
    *,
    member_id: int,
    semester_id: int,
    issued_on: str,
    reason: str,
    shift_id: int | None = None,
    carried_from_strike_id: int | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO strikes
          (member_id, semester_id, shift_id, issued_on, reason, carried_from_strike_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (member_id, semester_id, shift_id, issued_on, reason, carried_from_strike_id),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def get_by_id(conn: sqlite3.Connection, strike_id: int) -> Strike | None:
    row = conn.execute("SELECT * FROM strikes WHERE id = ?", (strike_id,)).fetchone()
    return _row(row) if row else None


def list_for_member_semester(
    conn: sqlite3.Connection,
    *,
    member_id: int,
    semester_id: int,
    include_closed: bool = False,
) -> list[Strike]:
    if include_closed:
        sql = "SELECT * FROM strikes WHERE member_id = ? AND semester_id = ? ORDER BY issued_on, id"
        rows = conn.execute(sql, (member_id, semester_id)).fetchall()
    else:
        sql = """
        SELECT * FROM strikes
        WHERE member_id = ? AND semester_id = ? AND closed_at IS NULL
        ORDER BY issued_on, id
        """
        rows = conn.execute(sql, (member_id, semester_id)).fetchall()
    return [_row(r) for r in rows]


def list_numbered_for_member_semester(
    conn: sqlite3.Connection, *, member_id: int, semester_id: int
) -> list[NumberedStrike]:
    """Open strikes with their derived `strike_number` from `v_strike_numbers`."""
    rows = conn.execute(
        """
        SELECT id, member_id, semester_id, strike_number, issued_on, reason
        FROM v_strike_numbers
        WHERE member_id = ? AND semester_id = ?
        ORDER BY strike_number
        """,
        (member_id, semester_id),
    ).fetchall()
    return [_numbered(r) for r in rows]


def count_active(conn: sqlite3.Connection, *, member_id: int, semester_id: int) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS n FROM strikes
        WHERE member_id = ? AND semester_id = ? AND closed_at IS NULL
        """,
        (member_id, semester_id),
    ).fetchone()
    return int(row["n"])


def count_total_in_semester(conn: sqlite3.Connection, *, member_id: int, semester_id: int) -> int:
    """Total strikes ever issued in this semester (open + closed)."""
    row = conn.execute(
        """
        SELECT COUNT(*) AS n FROM strikes
        WHERE member_id = ? AND semester_id = ?
        """,
        (member_id, semester_id),
    ).fetchone()
    return int(row["n"])


def close(conn: sqlite3.Connection, strike_id: int, *, closed_at: str) -> int:
    cur = conn.execute(
        "UPDATE strikes SET closed_at = ? WHERE id = ? AND closed_at IS NULL",
        (closed_at, strike_id),
    )
    return cur.rowcount


def get_strike_by_number(
    conn: sqlite3.Connection, *, member_id: int, semester_id: int, strike_number: int
) -> NumberedStrike | None:
    row = conn.execute(
        """
        SELECT id, member_id, semester_id, strike_number, issued_on, reason
        FROM v_strike_numbers
        WHERE member_id = ? AND semester_id = ? AND strike_number = ?
        """,
        (member_id, semester_id, strike_number),
    ).fetchone()
    return _numbered(row) if row else None


@dataclass(frozen=True, slots=True)
class StrikeDebt:
    """An open strike with no make-up shift placed against it yet."""

    strike_id: int
    member_id: int
    member_slug: str
    display_name: str
    issued_on: str
    reason: str


def list_unserved(conn: sqlite3.Connection, *, semester_id: int) -> list[StrikeDebt]:
    """Open strikes in this semester that nobody has been rostered to work off.

    "Unserved" is the conjunction of two things, and both are needed. A strike
    is open (``closed_at IS NULL``) and no shift claims it
    (``shifts.serves_strike_id``). Testing only the first would re-place a
    make-up on every run until the chair got round to closing the strike, which
    would double-book the member and, worse, quietly consume a second party's
    slot. Testing only the second would keep placing make-ups for strikes that
    were forgiven or worked off some other way — the removal ledger already has
    four such methods, only one of which is a shift.

    Members whose STATUS excludes them are filtered out: an alumnus cannot work
    a shift, and rostering one would leave a hole nobody fills.

    Hard-excluded ROLES are deliberately NOT filtered, which is the one place in
    this codebase where that filter is skipped. Colin's ruling: the exemption
    spares an officer the rotation, not a penalty he earned. The social chair
    carrying a strike works it off and appears in the ledger with one make-up
    shift and no rotation shifts, his exemption reason still printed beside it.

    Oldest first, so the earliest offence is worked off first. ``id`` breaks the
    tie because the spring sheet dates a whole chapter's strikes to the same
    night, and without a stable second key the order would drift between runs.
    """
    rows = conn.execute(
        """
        SELECT k.id, k.member_id, k.issued_on, k.reason,
               m.slug AS member_slug, m.display_name
        FROM strikes k
        JOIN members m ON m.id = k.member_id
        JOIN member_statuses ms ON ms.id = m.status_id
        WHERE k.semester_id = ?
          AND k.closed_at IS NULL
          AND ms.excludes_from_assignment = 0
          AND NOT EXISTS (
                SELECT 1 FROM shifts s WHERE s.serves_strike_id = k.id
              )
        ORDER BY k.issued_on, k.id
        """,
        (semester_id,),
    ).fetchall()
    return [
        StrikeDebt(
            strike_id=r["id"],
            member_id=r["member_id"],
            member_slug=r["member_slug"],
            display_name=r["display_name"],
            issued_on=r["issued_on"],
            reason=r["reason"],
        )
        for r in rows
    ]


def count_makeup_slots_owed(conn: sqlite3.Connection, *, semester_id: int) -> int:
    """Slots this semester's strikes will consume, placed or not.

    Feeds the quota denominator. Make-up shifts are penalties owed on top of a
    normal season, so they are not part of anyone's target — but they ARE real
    bodies staffing real parties, so the rotation has that much less to absorb.

    Counts placed and unplaced separately rather than just counting open
    strikes, because the two can diverge: a strike closed after its make-up was
    rostered still has a shift standing against it, and dropping that from the
    count would inflate every target by work that is already covered.
    """
    row = conn.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM shifts s
             JOIN events e ON e.id = s.event_id
            WHERE e.semester_id = ? AND s.serves_strike_id IS NOT NULL) AS placed,
          (SELECT COUNT(*) FROM strikes k
            WHERE k.semester_id = ? AND k.closed_at IS NULL
              AND NOT EXISTS (SELECT 1 FROM shifts s2 WHERE s2.serves_strike_id = k.id)
          ) AS outstanding
        """,
        (semester_id, semester_id),
    ).fetchone()
    return int(row["placed"]) + int(row["outstanding"])
