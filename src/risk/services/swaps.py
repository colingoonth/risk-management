"""Swap-request workflow (Phase 6).

Three swap shapes the chair can ratify on accept:

  A. Trade. Both shifts have a current assignee. On accept the two members
     swap shifts. Both shifts stay `assigned` with the trade partner's slot.
  B. Reassign to open slot. `to_shift` is open. On accept, the initiator
     moves to `to_shift`; `from_shift` becomes plain open. The swap fact is
     preserved on the `swap_requests` row (state=accepted, from_shift_id,
     initiator_member_id), not on the shifts table — the partial unique
     index `shifts_one_assignment` makes retaining the initiator as a
     bookkeeping assignee impossible for same-(event,shift_type) moves.
  C. Counterparty takeover. `to_shift` is None; `counterparty_member` is set.
     On accept the from_shift's assignee changes from initiator to
     counterparty in place. Status stays `assigned`; the swap_requests row
     records the audit fact.

For trades (A) we park both shifts to NULL first to free the
`shifts_one_assignment` partial unique index before reassigning either side.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from risk.repos import shifts as shifts_repo
from risk.repos import swap_requests as sr_repo


@dataclass(frozen=True, slots=True)
class SwapAcceptResult:
    request_id: int
    shape: str
    from_shift_id: int
    to_shift_id: int | None
    new_from_assignee_member_id: int | None
    new_to_assignee_member_id: int | None


def request_swap(
    conn: sqlite3.Connection,
    *,
    semester_id: int,
    from_shift_id: int,
    initiator_member_id: int,
    to_shift_id: int | None = None,
    counterparty_member_id: int | None = None,
) -> int:
    """Open a swap request. Validates initiator currently holds `from_shift`."""
    from_shift = _shift_or_none(conn, from_shift_id)
    if from_shift is None:
        raise LookupError(f"from_shift {from_shift_id} does not exist")
    if from_shift.assigned_member_id != initiator_member_id:
        raise ValueError(
            f"initiator {initiator_member_id} is not assigned to from_shift {from_shift_id}"
        )
    if to_shift_id is None and counterparty_member_id is None:
        raise ValueError("either to_shift_id or counterparty_member_id must be given")
    if counterparty_member_id is not None and counterparty_member_id == initiator_member_id:
        raise ValueError("cannot swap a shift to its current holder")
    # Guard: reject a second open request for the same from_shift.
    existing = conn.execute(
        "SELECT id FROM swap_requests WHERE from_shift_id = ? AND state = 'open'",
        (from_shift_id,),
    ).fetchone()
    if existing is not None:
        raise ValueError(
            f"An open swap request already exists for shift {from_shift_id}"
            " — run `risk swap list` to see pending swaps."
        )
    return sr_repo.insert(
        conn,
        semester_id=semester_id,
        from_shift_id=from_shift_id,
        initiator_member_id=initiator_member_id,
        to_shift_id=to_shift_id,
        counterparty_member_id=counterparty_member_id,
    )


def accept_swap(conn: sqlite3.Connection, *, request_id: int, assigned_at: str) -> SwapAcceptResult:
    """Apply the swap and mark the request accepted.

    `assigned_at` should be the from-shift event date (ADR-009: fairness
    tiebreaker pinned to event.date, not wall-clock).
    """
    req = sr_repo.get_by_id(conn, request_id)
    if req is None:
        raise LookupError(f"swap request {request_id} not found")
    if req.state != "open":
        raise ValueError(f"swap request {request_id} is {req.state}, not open")

    from_shift = _shift_or_raise(conn, req.from_shift_id, label="from_shift")
    if from_shift.assigned_member_id != req.initiator_member_id:
        raise ValueError("from_shift no longer assigned to initiator — swap stale")

    if req.to_shift_id is not None:
        to_shift = _shift_or_raise(conn, req.to_shift_id, label="to_shift")
        if to_shift.assigned_member_id is not None:
            result = _apply_trade(
                conn,
                req=req,
                from_shift=from_shift,
                to_shift=to_shift,
                assigned_at=assigned_at,
            )
        else:
            result = _apply_reassign_to_open(
                conn,
                req=req,
                from_shift=from_shift,
                to_shift=to_shift,
                assigned_at=assigned_at,
            )
    else:
        if req.counterparty_member_id is None:
            raise ValueError("no to_shift and no counterparty — invalid swap")
        result = _apply_counterparty_takeover(
            conn,
            req=req,
            from_shift=from_shift,
            assigned_at=assigned_at,
        )

    updated = sr_repo.update_state(
        conn, req_id=request_id, new_state="accepted", expected_current="open"
    )
    if updated == 0:
        raise RuntimeError("swap request changed state mid-accept")
    return result


def reject_swap(conn: sqlite3.Connection, *, request_id: int) -> None:
    updated = sr_repo.update_state(
        conn, req_id=request_id, new_state="rejected", expected_current="open"
    )
    if updated == 0:
        req = sr_repo.get_by_id(conn, request_id)
        if req is None:
            raise LookupError(f"swap request {request_id} not found")
        raise ValueError(f"swap request {request_id} is {req.state}, not open")


def cancel_swap(conn: sqlite3.Connection, *, request_id: int) -> None:
    updated = sr_repo.update_state(
        conn, req_id=request_id, new_state="cancelled", expected_current="open"
    )
    if updated == 0:
        req = sr_repo.get_by_id(conn, request_id)
        if req is None:
            raise LookupError(f"swap request {request_id} not found")
        raise ValueError(f"swap request {request_id} is {req.state}, not open")


def _apply_trade(
    conn: sqlite3.Connection,
    *,
    req: sr_repo.SwapRequest,
    from_shift: shifts_repo.Shift,
    to_shift: shifts_repo.Shift,
    assigned_at: str,
) -> SwapAcceptResult:
    """Two-member trade. Park BOTH shifts first to free the partial unique
    index, then reassign each side to the trade partner.
    """
    other_member_id = to_shift.assigned_member_id
    assert other_member_id is not None
    conn.execute(
        "UPDATE shifts SET assigned_member_id = NULL, status = 'open' WHERE id IN (?, ?)",
        (from_shift.id, to_shift.id),
    )
    conn.execute(
        """
        UPDATE shifts
        SET assigned_member_id = ?, status = 'assigned', assigned_at = ?
        WHERE id = ?
        """,
        (other_member_id, assigned_at, from_shift.id),
    )
    conn.execute(
        """
        UPDATE shifts
        SET assigned_member_id = ?, status = 'assigned', assigned_at = ?
        WHERE id = ?
        """,
        (req.initiator_member_id, assigned_at, to_shift.id),
    )
    return SwapAcceptResult(
        request_id=req.id,
        shape="trade",
        from_shift_id=from_shift.id,
        to_shift_id=to_shift.id,
        new_from_assignee_member_id=other_member_id,
        new_to_assignee_member_id=req.initiator_member_id,
    )


def _apply_reassign_to_open(
    conn: sqlite3.Connection,
    *,
    req: sr_repo.SwapRequest,
    from_shift: shifts_repo.Shift,
    to_shift: shifts_repo.Shift,
    assigned_at: str,
) -> SwapAcceptResult:
    """Initiator moves to an open slot. from_shift becomes plain open; the
    swap fact is preserved on the swap_requests row."""
    assert from_shift.effective_pledge_mode_id is not None
    conn.execute(
        "UPDATE shifts SET assigned_member_id = NULL, status = 'open' WHERE id = ?",
        (from_shift.id,),
    )
    conn.execute(
        """
        UPDATE shifts
        SET assigned_member_id = ?,
            status = 'assigned',
            effective_pledge_mode_id = COALESCE(effective_pledge_mode_id, ?),
            assigned_at = ?
        WHERE id = ?
        """,
        (
            req.initiator_member_id,
            from_shift.effective_pledge_mode_id,
            assigned_at,
            to_shift.id,
        ),
    )
    return SwapAcceptResult(
        request_id=req.id,
        shape="reassign_to_open",
        from_shift_id=from_shift.id,
        to_shift_id=to_shift.id,
        new_from_assignee_member_id=None,
        new_to_assignee_member_id=req.initiator_member_id,
    )


def _apply_counterparty_takeover(
    conn: sqlite3.Connection,
    *,
    req: sr_repo.SwapRequest,
    from_shift: shifts_repo.Shift,
    assigned_at: str,
) -> SwapAcceptResult:
    """Counterparty takes over from_shift in place."""
    assert req.counterparty_member_id is not None
    conn.execute(
        """
        UPDATE shifts
        SET assigned_member_id = ?, assigned_at = ?
        WHERE id = ?
        """,
        (req.counterparty_member_id, assigned_at, from_shift.id),
    )
    return SwapAcceptResult(
        request_id=req.id,
        shape="counterparty_takeover",
        from_shift_id=from_shift.id,
        to_shift_id=None,
        new_from_assignee_member_id=req.counterparty_member_id,
        new_to_assignee_member_id=None,
    )


def _shift_or_raise(conn: sqlite3.Connection, shift_id: int, *, label: str) -> shifts_repo.Shift:
    s = shifts_repo.get_by_id(conn, shift_id)
    if s is None:
        raise LookupError(f"{label} {shift_id} does not exist")
    return s


def _shift_or_none(conn: sqlite3.Connection, shift_id: int) -> shifts_repo.Shift | None:
    return shifts_repo.get_by_id(conn, shift_id)
