"""Events + shifts + auto-assign workflows."""

from __future__ import annotations

import sqlite3
from datetime import date as _date

from fastapi import APIRouter, Depends, Query

from risk.api.deps import get_conn
from risk.api.errors import service_errors
from risk.api.routers._util import resolve_semester
from risk.api.schemas import (
    AutoAssignIn,
    AutoAssignOut,
    EventIn,
    EventOut,
    ProposedAssignmentOut,
    SetHostIn,
    ShiftOut,
)
from risk.db.connection import transaction
from risk.repos import event_types as etypes_repo
from risk.repos import events as events_repo
from risk.repos import houses as houses_repo
from risk.repos import roles as roles_repo
from risk.repos import shifts as shifts_repo
from risk.services import assignment as assign_svc
from risk.services import shift_requirements as reqs_svc
from risk.services.assignment import AutoAssignResult

router = APIRouter(prefix="/events", tags=["events"])


def _auto_assign_out(event_name: str, result: AutoAssignResult) -> AutoAssignOut:
    return AutoAssignOut(
        event_id=result.event_id,
        event_name=event_name,
        resolved_mode=result.resolved_mode.resolved_slug,
        configured_mode=result.resolved_mode.configured_slug,
        seed=result.seed,
        eligible_count=len(result.eligibility.eligible),
        assignments=[
            ProposedAssignmentOut(
                shift_type_slug=a.shift_type_slug,
                slot_index=a.slot_index,
                member_slug=a.member_slug,
                score=a.score,
                reason=a.reason,
            )
            for a in result.assignments
        ],
        warnings=result.warnings,
    )


def _validated_allowed_keys(conn: sqlite3.Connection, allow: list[str]) -> frozenset[str]:
    """Validate each --allow key against soft-excluded role automation keys."""
    if not allow:
        return frozenset()
    soft = {r.automation_key for r in roles_repo.get_soft_excluded(conn)}
    for key in allow:
        if key not in soft:
            raise ValueError(
                f"allowed key {key!r} is not a soft-excluded role automation_key; "
                f"known: {sorted(k for k in soft if k)}"
            )
    return frozenset(allow)


@router.get("", response_model=list[EventOut])
def list_events(
    semester: str | None = None,
    status: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
) -> list[EventOut]:
    with service_errors():
        sem = resolve_semester(conn, semester)
    rows = events_repo.list_for_semester(conn, sem.id, status=status)
    return [EventOut.model_validate(r) for r in rows]


@router.post("", response_model=EventOut, status_code=201)
def create_event(
    body: EventIn,
    semester: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
) -> EventOut:
    """Ad-hoc event creation — snapshots shift requirements from the type defaults."""
    with service_errors():
        sem = resolve_semester(conn, semester)
        et = etypes_repo.get_by_slug(conn, body.event_type_slug)
        if et is None:
            raise LookupError(f"event type {body.event_type_slug!r} not found")
        host_id: int | None = None
        if body.host_house_slug:
            house = houses_repo.get_by_slug(conn, body.host_house_slug)
            if house is None:
                raise LookupError(f"house {body.host_house_slug!r} not found")
            host_id = house.id
        try:
            _date.fromisoformat(body.date)
        except ValueError as exc:
            raise ValueError(f"invalid date {body.date!r} — use YYYY-MM-DD") from exc
        with transaction(conn):
            event_id = events_repo.insert(
                conn,
                semester_id=sem.id,
                event_type_id=et.id,
                display_name=body.display_name,
                date=body.date,
                host_house_id=host_id,
                start_time=body.start_time,
                end_time=body.end_time,
                notes=body.notes,
            )
            reqs_svc.snapshot_for_event(conn, event_id)
    created = events_repo.get_by_id(conn, event_id)
    assert created is not None
    return EventOut.model_validate(created)


@router.get("/{event_id}", response_model=EventOut)
def get_event(event_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> EventOut:
    with service_errors():
        ev = events_repo.get_by_id(conn, event_id)
        if ev is None:
            raise LookupError(f"event {event_id} not found")
    return EventOut.model_validate(ev)


@router.get("/{event_id}/shifts", response_model=list[ShiftOut])
def list_event_shifts(
    event_id: int, conn: sqlite3.Connection = Depends(get_conn)
) -> list[ShiftOut]:
    return [ShiftOut.model_validate(s) for s in shifts_repo.list_for_event(conn, event_id)]


@router.post("/{event_id}/auto-assign", response_model=AutoAssignOut)
def auto_assign_event(
    event_id: int,
    body: AutoAssignIn,
    dry_run: bool = Query(default=False),
    conn: sqlite3.Connection = Depends(get_conn),
) -> AutoAssignOut:
    with service_errors():
        ev = events_repo.get_by_id(conn, event_id)
        if ev is None:
            raise LookupError(f"event {event_id} not found")
        keys = _validated_allowed_keys(conn, body.allowed_keys)
        if dry_run:
            result = assign_svc.auto_assign(
                conn,
                event_id=event_id,
                allowed_keys=keys,
                seed=body.seed,
                reassign=body.reassign,
                commit=False,
            )
        else:
            with transaction(conn):
                result = assign_svc.auto_assign(
                    conn,
                    event_id=event_id,
                    allowed_keys=keys,
                    seed=body.seed,
                    reassign=body.reassign,
                    commit=True,
                )
    return _auto_assign_out(ev.display_name, result)


@router.post("/auto-assign-bulk", response_model=list[AutoAssignOut])
def auto_assign_bulk(
    body: AutoAssignIn,
    semester: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
) -> list[AutoAssignOut]:
    """Bulk pre-semester fill — auto-assign every non-cancelled event in the semester."""
    with service_errors():
        sem = resolve_semester(conn, semester)
        keys = _validated_allowed_keys(conn, body.allowed_keys)
        events = [
            e for e in events_repo.list_for_semester(conn, sem.id) if e.status != "cancelled"
        ]
        out: list[AutoAssignOut] = []
        with transaction(conn):
            for ev in events:
                result = assign_svc.auto_assign(
                    conn,
                    event_id=ev.id,
                    allowed_keys=keys,
                    seed=body.seed,
                    reassign=body.reassign,
                    commit=True,
                )
                out.append(_auto_assign_out(ev.display_name, result))
    return out


@router.post("/{event_id}/set-host", response_model=EventOut)
def set_host(
    event_id: int, body: SetHostIn, conn: sqlite3.Connection = Depends(get_conn)
) -> EventOut:
    """Change the host house. Flips resync_pending so the chair re-runs auto-assign."""
    with service_errors():
        ev = events_repo.get_by_id(conn, event_id)
        if ev is None:
            raise LookupError(f"event {event_id} not found")
        host_id: int | None = None
        if body.host_house_slug:
            house = houses_repo.get_by_slug(conn, body.host_house_slug)
            if house is None:
                raise LookupError(f"house {body.host_house_slug!r} not found")
            host_id = house.id
        with transaction(conn):
            events_repo.update_host(conn, event_id=event_id, host_house_id=host_id)
    updated = events_repo.get_by_id(conn, event_id)
    assert updated is not None
    return EventOut.model_validate(updated)


@router.post("/{event_id}/cancel", response_model=EventOut)
def cancel_event(event_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> EventOut:
    with service_errors():
        ev = events_repo.get_by_id(conn, event_id)
        if ev is None:
            raise LookupError(f"event {event_id} not found")
        with transaction(conn):
            events_repo.update_status(conn, event_id=event_id, status="cancelled")
    updated = events_repo.get_by_id(conn, event_id)
    assert updated is not None
    return EventOut.model_validate(updated)
