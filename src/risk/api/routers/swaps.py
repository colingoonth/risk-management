"""Swap requests."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from risk.api.deps import get_conn
from risk.api.errors import service_errors
from risk.api.routers._util import resolve_semester
from risk.api.schemas import SwapRequestIn, SwapRequestOut
from risk.db.connection import transaction
from risk.repos import events as events_repo
from risk.repos import members as members_repo
from risk.repos import shifts as shifts_repo
from risk.repos import swap_requests as sr_repo
from risk.services import swaps as swaps_svc

router = APIRouter(prefix="/swaps", tags=["swaps"])


@router.get("", response_model=list[SwapRequestOut])
def list_swaps(
    state: str | None = None, conn: sqlite3.Connection = Depends(get_conn)
) -> list[SwapRequestOut]:
    return [SwapRequestOut.model_validate(s) for s in sr_repo.list_all(conn, state=state)]


@router.post("", response_model=SwapRequestOut, status_code=201)
def request_swap(
    body: SwapRequestIn,
    semester: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
) -> SwapRequestOut:
    with service_errors():
        sem = resolve_semester(conn, semester)
        from_shift = shifts_repo.get_by_id(conn, body.from_shift_id)
        if from_shift is None:
            raise LookupError(f"shift {body.from_shift_id} not found")
        if from_shift.assigned_member_id is None:
            raise ValueError(f"shift {body.from_shift_id} has no assigned member to swap")
        counterparty_id: int | None = None
        if body.counterparty_member_slug:
            cp = members_repo.resolve(conn, body.counterparty_member_slug)
            if cp is None:
                raise LookupError(f"member {body.counterparty_member_slug!r} not found")
            counterparty_id = cp.id
        with transaction(conn):
            req_id = swaps_svc.request_swap(
                conn,
                semester_id=sem.id,
                from_shift_id=body.from_shift_id,
                initiator_member_id=from_shift.assigned_member_id,
                to_shift_id=body.to_shift_id,
                counterparty_member_id=counterparty_id,
            )
    created = sr_repo.get_by_id(conn, req_id)
    assert created is not None
    return SwapRequestOut.model_validate(created)


@router.post("/{req_id}/accept", response_model=SwapRequestOut)
def accept_swap(req_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> SwapRequestOut:
    with service_errors():
        req = sr_repo.get_by_id(conn, req_id)
        if req is None:
            raise LookupError(f"swap request {req_id} not found")
        # assigned_at is pinned to the from-shift's event date (ADR-009).
        from_event = events_repo.get_by_id(
            conn, shifts_repo.event_id_of(conn, req.from_shift_id) or -1
        )
        if from_event is None:
            raise LookupError(f"event for shift {req.from_shift_id} not found")
        with transaction(conn):
            swaps_svc.accept_swap(conn, request_id=req_id, assigned_at=from_event.date)
    return _reload(conn, req_id)


@router.post("/{req_id}/reject", response_model=SwapRequestOut)
def reject_swap(req_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> SwapRequestOut:
    with service_errors(), transaction(conn):
        swaps_svc.reject_swap(conn, request_id=req_id)
    return _reload(conn, req_id)


@router.post("/{req_id}/cancel", response_model=SwapRequestOut)
def cancel_swap(req_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> SwapRequestOut:
    with service_errors(), transaction(conn):
        swaps_svc.cancel_swap(conn, request_id=req_id)
    return _reload(conn, req_id)


def _reload(conn: sqlite3.Connection, req_id: int) -> SwapRequestOut:
    req = sr_repo.get_by_id(conn, req_id)
    if req is None:
        raise LookupError(f"swap request {req_id} not found")
    return SwapRequestOut.model_validate(req)
