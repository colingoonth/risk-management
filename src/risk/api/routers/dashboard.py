"""Actions-led dashboard aggregate — 'what needs my attention?'."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from risk.api.deps import get_conn
from risk.api.errors import service_errors
from risk.api.routers._util import resolve_semester
from risk.api.schemas import (
    DashboardOut,
    MemberLoadOut,
    PendingConsequenceOut,
    SwapRequestOut,
    UnfilledEventOut,
)
from risk.repos import event_shift_requirements as req_repo
from risk.repos import events as events_repo
from risk.repos import members as members_repo
from risk.repos import pending_consequences as pc_repo
from risk.repos import shifts as shifts_repo
from risk.repos import swap_requests as sr_repo

# How many of the lightest-loaded members to surface on the dashboard.
_MEMBERS_NEEDING_LIMIT = 10

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(
    semester: str | None = None, conn: sqlite3.Connection = Depends(get_conn)
) -> DashboardOut:
    with service_errors():
        sem = resolve_semester(conn, semester)

    events = [e for e in events_repo.list_for_semester(conn, sem.id) if e.status != "cancelled"]

    unfilled: list[UnfilledEventOut] = []
    resync_pending_count = 0
    for ev in events:
        if ev.resync_pending:
            resync_pending_count += 1
        total = sum(r.target_count for r in req_repo.list_for_event(conn, ev.id))
        assigned = sum(
            1 for s in shifts_repo.list_for_event(conn, ev.id) if s.assigned_member_id is not None
        )
        open_slots = total - assigned
        if open_slots > 0:
            unfilled.append(
                UnfilledEventOut(
                    event_id=ev.id,
                    display_name=ev.display_name,
                    date=ev.date,
                    event_type_slug=ev.event_type_slug,
                    host_house_slug=ev.host_house_slug,
                    open_slots=open_slots,
                    total_slots=total,
                    resync_pending=ev.resync_pending,
                )
            )
    unfilled.sort(key=lambda u: (u.date, u.display_name))

    # "Who hasn't worked?" — active members ranked by shift count ascending.
    counts: dict[int, int] = {}
    for s in shifts_repo.list_filtered(conn, semester_id=sem.id):
        if s.assigned_member_id is not None:
            counts[s.assigned_member_id] = counts.get(s.assigned_member_id, 0) + 1
    active = members_repo.list_by_status(conn, "active")
    loads = sorted(
        (
            MemberLoadOut(
                member_slug=m.slug,
                display_name=m.display_name,
                shift_count=counts.get(m.id, 0),
            )
            for m in active
        ),
        key=lambda ml: (ml.shift_count, ml.member_slug),
    )

    pending_swaps = [
        SwapRequestOut.model_validate(s) for s in sr_repo.list_all(conn, state="open")
    ]
    pending_consequences = [
        PendingConsequenceOut.model_validate(c)
        for c in pc_repo.list_all_with_state(conn, state="pending")
    ]

    return DashboardOut(
        semester_name=sem.name,
        unfilled_events=unfilled,
        pending_swaps=pending_swaps,
        members_needing_shifts=loads[:_MEMBERS_NEEDING_LIMIT],
        pending_consequences=pending_consequences,
        resync_pending_count=resync_pending_count,
    )
