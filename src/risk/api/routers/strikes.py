"""Strikes + threshold consequences."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from risk.api.deps import get_conn
from risk.api.errors import service_errors
from risk.api.routers._util import resolve_semester
from risk.api.schemas import (
    NumberedStrikeOut,
    PendingConsequenceOut,
    RemovalMethodOut,
    StrikeIn,
    StrikeRemovalIn,
    StrikeRemovalOut,
)
from risk.db.connection import transaction
from risk.repos import members as members_repo
from risk.repos import pending_consequences as pc_repo
from risk.repos import removal_methods as removal_methods_repo
from risk.repos import strikes as strikes_repo
from risk.services import strike_state

router = APIRouter(tags=["strikes"])


def _member_id(conn: sqlite3.Connection, slug: str) -> int:
    member = members_repo.resolve(conn, slug)
    if member is None:
        raise LookupError(f"member {slug!r} not found")
    return member.id


@router.get("/strikes", response_model=list[NumberedStrikeOut])
def list_strikes(
    member: str,
    semester: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
) -> list[NumberedStrikeOut]:
    with service_errors():
        sem = resolve_semester(conn, semester)
        mid = _member_id(conn, member)
    rows = strikes_repo.list_numbered_for_member_semester(
        conn, member_id=mid, semester_id=sem.id
    )
    return [NumberedStrikeOut.model_validate(r) for r in rows]


@router.post("/strikes", response_model=NumberedStrikeOut, status_code=201)
def issue_strike(
    body: StrikeIn,
    semester: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
) -> NumberedStrikeOut:
    with service_errors():
        sem = resolve_semester(conn, semester)
        mid = _member_id(conn, body.member_slug)
        with transaction(conn):
            result = strike_state.issue_strike(
                conn,
                member_id=mid,
                semester_id=sem.id,
                issued_on=body.issued_on,
                reason=body.reason,
            )
    issued = strikes_repo.get_by_id(conn, result.strike_id)
    assert issued is not None
    # Re-read the numbered view so the response carries the derived strike_number.
    numbered = strikes_repo.list_numbered_for_member_semester(
        conn, member_id=mid, semester_id=sem.id
    )
    match = next((n for n in numbered if n.id == result.strike_id), None)
    assert match is not None
    return NumberedStrikeOut.model_validate(match)


@router.get("/removal-methods", response_model=list[RemovalMethodOut])
def list_removal_methods(
    conn: sqlite3.Connection = Depends(get_conn),
) -> list[RemovalMethodOut]:
    return [
        RemovalMethodOut.model_validate(m) for m in removal_methods_repo.list_active(conn)
    ]


@router.post("/strikes/remove", response_model=StrikeRemovalOut)
def remove_strikes(
    body: StrikeRemovalIn,
    conn: sqlite3.Connection = Depends(get_conn),
) -> StrikeRemovalOut:
    with service_errors():
        sem = resolve_semester(conn, body.semester)
        mid = _member_id(conn, body.member_slug)
        method = removal_methods_repo.get_active_by_slug(conn, body.removal_method_slug)
        if method is None:
            raise LookupError(f"removal method {body.removal_method_slug!r} not found")
        performed_by = (
            _member_id(conn, body.performed_by_slug)
            if body.performed_by_slug is not None
            else None
        )
        # Guard cross-semester removal: apply_removal derives semester from the
        # strikes it closes and does NOT verify they share one. Constrain every
        # requested id to this member+semester before touching the service.
        owned = {
            n.id
            for n in strikes_repo.list_numbered_for_member_semester(
                conn, member_id=mid, semester_id=sem.id
            )
        }
        stray = [sid for sid in body.strike_ids if sid not in owned]
        if stray:
            raise ValueError(
                f"strike ids {stray} are not open strikes for {body.member_slug!r} "
                f"in semester {sem.name!r}"
            )
        with transaction(conn):
            result = strike_state.apply_removal(
                conn,
                member_id=mid,
                removal_method_id=method.id,
                performed_on=body.performed_on,
                strike_ids=body.strike_ids,
                performed_by_member_id=performed_by,
                notes=body.notes,
            )
    return StrikeRemovalOut.model_validate(result)


@router.get("/consequences", response_model=list[PendingConsequenceOut])
def list_consequences(
    state: str | None = "pending", conn: sqlite3.Connection = Depends(get_conn)
) -> list[PendingConsequenceOut]:
    rows = pc_repo.list_all_with_state(conn, state=state)
    return [PendingConsequenceOut.model_validate(r) for r in rows]


@router.post("/consequences/{pc_id}/resolve", response_model=PendingConsequenceOut)
def resolve_consequence(
    pc_id: int,
    new_state: str = "served",
    conn: sqlite3.Connection = Depends(get_conn),
) -> PendingConsequenceOut:
    with service_errors():
        with transaction(conn):
            affected = pc_repo.resolve(conn, pc_id, new_state=new_state)
        if affected == 0:
            raise LookupError(f"no pending consequence #{pc_id} to resolve")
    updated = pc_repo.get_by_id(conn, pc_id)
    assert updated is not None
    return PendingConsequenceOut.model_validate(updated)
