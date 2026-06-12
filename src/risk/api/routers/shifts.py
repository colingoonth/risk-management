"""Shift listing (filtered) — drives the swap-create picker."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from risk.api.deps import get_conn
from risk.api.errors import service_errors
from risk.api.routers._util import resolve_semester
from risk.api.schemas import ShiftOut
from risk.repos import members as members_repo
from risk.repos import shifts as shifts_repo

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
