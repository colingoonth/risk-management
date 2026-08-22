"""Shift listing (filtered) — drives the swap-create picker."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from risk.api.deps import get_conn
from risk.api.errors import service_errors
from risk.api.routers._util import resolve_semester
from risk.api.schemas import ShiftAssignIn, ShiftAssignOut, ShiftOut
from risk.db.connection import transaction
from risk.repos import members as members_repo
from risk.repos import shifts as shifts_repo
from risk.services import manual_assign as manual_svc

router = APIRouter(prefix="/shifts", tags=["shifts"])

_VALID_STATUSES = {"open", "assigned", "completed", "no_show", "swapped"}


@router.get("", response_model=list[ShiftOut])
def list_shifts(
    member: str | None = None,
    semester: str | None = None,
    status: str | None = None,
    event: int | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
) -> list[ShiftOut]:
    if status is not None and status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"unknown shift status {status!r}; expected one of {sorted(_VALID_STATUSES)}",
        )
    with service_errors():
        member_id: int | None = None
        if member is not None:
            resolved = members_repo.resolve(conn, member)
            if resolved is None:
                raise LookupError(f"member {member!r} not found")
            member_id = resolved.id
        semester_id = resolve_semester(conn, semester).id if semester is not None else None
    rows = shifts_repo.list_filtered(
        conn,
        member_id=member_id,
        event_id=event,
        semester_id=semester_id,
        status=status,
    )
    return [ShiftOut.model_validate(s) for s in rows]


@router.post("/{shift_id}/assign", response_model=ShiftAssignOut)
def assign_shift(
    shift_id: int, body: ShiftAssignIn, conn: sqlite3.Connection = Depends(get_conn)
) -> ShiftAssignOut:
    """Put a member on a slot by hand and mark it chair-set.

    Marked so a rebuild preserves it — the chair knows things the solver cannot
    re-derive. Every check the auto-fill applies is applied here too; ``force``
    records the assignment anyway and returns the objections as warnings rather
    than swallowing them.
    """
    with service_errors(), transaction(conn):
        result = manual_svc.assign(
            conn, shift_id=shift_id, member_key=body.member_slug, force=body.force
        )
    return ShiftAssignOut.model_validate(result)


@router.post("/{shift_id}/unassign", response_model=ShiftOut)
def unassign_shift(shift_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> ShiftOut:
    """Empty a slot, leaving it open for the next fill."""
    with service_errors(), transaction(conn):
        manual_svc.unassign(conn, shift_id=shift_id)
    updated = shifts_repo.get_by_id(conn, shift_id)
    assert updated is not None
    return ShiftOut.model_validate(updated)
