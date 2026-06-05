"""Per-entity repository for ``pending_consequences`` (Phase 5).

Per ADR-007 these are the THRESHOLD-triggered chair-owed actions
(extra_shift, probation, expulsion_review), not the strike-issuance
categories. Distinct table from `strike_categories`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PendingConsequence:
    id: int
    member_id: int
    semester_id: int
    triggering_strike_id: int
    kind: str
    state: str
    created_at: str
    resolved_at: str | None


def _row(r: sqlite3.Row) -> PendingConsequence:
    return PendingConsequence(
        id=r["id"],
        member_id=r["member_id"],
        semester_id=r["semester_id"],
        triggering_strike_id=r["triggering_strike_id"],
        kind=r["kind"],
        state=r["state"],
        created_at=r["created_at"],
        resolved_at=r["resolved_at"],
    )


def insert(
    conn: sqlite3.Connection,
    *,
    member_id: int,
    semester_id: int,
    triggering_strike_id: int,
    kind: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO pending_consequences
          (member_id, semester_id, triggering_strike_id, kind)
        VALUES (?, ?, ?, ?)
        """,
        (member_id, semester_id, triggering_strike_id, kind),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def get_by_id(conn: sqlite3.Connection, pc_id: int) -> PendingConsequence | None:
    row = conn.execute(
        "SELECT * FROM pending_consequences WHERE id = ?", (pc_id,)
    ).fetchone()
    return _row(row) if row else None


def list_for_member_semester(
    conn: sqlite3.Connection,
    *,
    member_id: int,
    semester_id: int,
    state: str | None = None,
) -> list[PendingConsequence]:
    if state is None:
        sql = """
        SELECT * FROM pending_consequences
        WHERE member_id = ? AND semester_id = ?
        ORDER BY created_at, id
        """
        rows = conn.execute(sql, (member_id, semester_id)).fetchall()
    else:
        sql = """
        SELECT * FROM pending_consequences
        WHERE member_id = ? AND semester_id = ? AND state = ?
        ORDER BY created_at, id
        """
        rows = conn.execute(sql, (member_id, semester_id, state)).fetchall()
    return [_row(r) for r in rows]


def list_pending_for_member(
    conn: sqlite3.Connection, *, member_id: int
) -> list[PendingConsequence]:
    """Pending threshold-consequences across all semesters for one member."""
    rows = conn.execute(
        """
        SELECT * FROM pending_consequences
        WHERE member_id = ? AND state = 'pending'
        ORDER BY semester_id, created_at, id
        """,
        (member_id,),
    ).fetchall()
    return [_row(r) for r in rows]


def list_all_with_state(
    conn: sqlite3.Connection, *, state: str | None = None
) -> list[PendingConsequence]:
    if state is None:
        rows = conn.execute(
            "SELECT * FROM pending_consequences ORDER BY semester_id, member_id, created_at, id"
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM pending_consequences WHERE state = ?
            ORDER BY semester_id, member_id, created_at, id
            """,
            (state,),
        ).fetchall()
    return [_row(r) for r in rows]


def resolve(conn: sqlite3.Connection, pc_id: int, *, new_state: str) -> int:
    """Transition a pending row to served/waived/carried_forward."""
    cur = conn.execute(
        """
        UPDATE pending_consequences
        SET state = ?, resolved_at = datetime('now')
        WHERE id = ? AND state = 'pending'
        """,
        (new_state, pc_id),
    )
    return cur.rowcount


def get_for_kind(
    conn: sqlite3.Connection, *, member_id: int, semester_id: int, kind: str
) -> PendingConsequence | None:
    row = conn.execute(
        """
        SELECT * FROM pending_consequences
        WHERE member_id = ? AND semester_id = ? AND kind = ?
        """,
        (member_id, semester_id, kind),
    ).fetchone()
    return _row(row) if row else None
