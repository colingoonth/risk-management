"""Per-entity repository for ``swap_requests`` (Phase 6)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SwapRequest:
    id: int
    semester_id: int
    from_shift_id: int
    to_shift_id: int | None
    initiator_member_id: int
    counterparty_member_id: int | None
    state: str
    created_at: str
    resolved_at: str | None
    initiator_slug: str
    counterparty_slug: str | None


_SELECT_JOINED = """
SELECT
  sr.id, sr.semester_id, sr.from_shift_id, sr.to_shift_id,
  sr.initiator_member_id, sr.counterparty_member_id,
  sr.state, sr.created_at, sr.resolved_at,
  init.slug AS initiator_slug,
  cp.slug AS counterparty_slug
FROM swap_requests sr
JOIN members init ON init.id = sr.initiator_member_id
LEFT JOIN members cp ON cp.id = sr.counterparty_member_id
"""


def _row(r: sqlite3.Row) -> SwapRequest:
    return SwapRequest(
        id=r["id"],
        semester_id=r["semester_id"],
        from_shift_id=r["from_shift_id"],
        to_shift_id=r["to_shift_id"],
        initiator_member_id=r["initiator_member_id"],
        counterparty_member_id=r["counterparty_member_id"],
        state=r["state"],
        created_at=r["created_at"],
        resolved_at=r["resolved_at"],
        initiator_slug=r["initiator_slug"],
        counterparty_slug=r["counterparty_slug"],
    )


def insert(
    conn: sqlite3.Connection,
    *,
    semester_id: int,
    from_shift_id: int,
    initiator_member_id: int,
    to_shift_id: int | None = None,
    counterparty_member_id: int | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO swap_requests
          (semester_id, from_shift_id, to_shift_id,
           initiator_member_id, counterparty_member_id)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            semester_id,
            from_shift_id,
            to_shift_id,
            initiator_member_id,
            counterparty_member_id,
        ),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def get_by_id(conn: sqlite3.Connection, req_id: int) -> SwapRequest | None:
    row = conn.execute(f"{_SELECT_JOINED} WHERE sr.id = ?", (req_id,)).fetchone()
    return _row(row) if row else None


def list_all(
    conn: sqlite3.Connection, *, state: str | None = None
) -> list[SwapRequest]:
    if state is None:
        rows = conn.execute(
            f"{_SELECT_JOINED} ORDER BY sr.created_at DESC, sr.id DESC"
        ).fetchall()
    else:
        rows = conn.execute(
            f"{_SELECT_JOINED} WHERE sr.state = ? ORDER BY sr.created_at DESC, sr.id DESC",
            (state,),
        ).fetchall()
    return [_row(r) for r in rows]


def update_state(
    conn: sqlite3.Connection,
    *,
    req_id: int,
    new_state: str,
    expected_current: str = "open",
) -> int:
    """Transition open → accepted/rejected/cancelled with optimistic check."""
    cur = conn.execute(
        """
        UPDATE swap_requests
        SET state = ?, resolved_at = datetime('now')
        WHERE id = ? AND state = ?
        """,
        (new_state, req_id, expected_current),
    )
    return cur.rowcount
