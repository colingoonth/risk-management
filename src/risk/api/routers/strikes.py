"""Strikes + threshold consequences."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from risk.api.deps import get_conn
from risk.api.errors import service_errors
from risk.api.routers._util import resolve_semester
from risk.api.schemas import NumberedStrikeOut, PendingConsequenceOut, StrikeIn
from risk.db.connection import transaction
from risk.repos import members as members_repo
from risk.repos import pending_consequences as pc_repo
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
