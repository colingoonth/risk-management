"""``risk shift ...`` subcommands — discoverability for shift IDs needed by
``strike issue --shift`` and ``swap request --from-shift/--to-shift``."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

import typer

from risk.cli._common import mode_from_ctx, open_conn
from risk.cli.output import attr_table, emit_error, emit_success
from risk.repos import events as events_repo
from risk.repos import members as members_repo
from risk.repos import semesters as semesters_repo
from risk.repos import shifts as repo

app = typer.Typer(help="List and inspect event shifts.")


@app.command("list")
def list_(
    ctx: typer.Context,
    member: Annotated[
        str | None, typer.Option("--member", help="Filter by member slug, id, or alias.")
    ] = None,
    event: Annotated[
        str | None,
        typer.Option("--event", help="Filter by event id or display_name in current semester."),
    ] = None,
    semester: Annotated[
        str | None,
        typer.Option("--semester", help="Filter by semester name (defaults to current)."),
    ] = None,
    status: Annotated[
        str | None,
        typer.Option("--status", help="Filter by shift status: open / assigned / completed."),
    ] = None,
) -> None:
    """List shifts with optional filters. Defaults to the current semester."""
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)

    member_id: int | None = None
    if member is not None:
        m = members_repo.resolve(conn, member)
        if m is None:
            emit_error("member.not_found", f"Could not resolve member {member!r}.", mode=mode)
            return
        member_id = m.id

    event_id: int | None = None
    if event is not None:
        ev = events_repo.resolve(conn, event)
        if ev is None:
            emit_error("event.not_found", f"Could not resolve event {event!r}.", mode=mode)
            return
        event_id = ev.id

    semester_id: int | None = None
    if event_id is None:
        # Only constrain to a semester when not already pinned to a specific event.
        if semester is not None:
            sem = semesters_repo.get_by_name(conn, semester)
            if sem is None:
                emit_error("semester.not_found", f"No semester named {semester!r}.", mode=mode)
                return
            semester_id = sem.id
        else:
            current = semesters_repo.get_current(conn)
            if current is not None:
                semester_id = current.id

    shifts = repo.list_filtered(
        conn,
        member_id=member_id,
        event_id=event_id,
        semester_id=semester_id,
        status=status,
    )
    emit_success(
        [asdict(s) for s in shifts],
        mode=mode,
        table=attr_table(
            "Shifts",
            shifts,
            cols=(
                ("ID", "id"),
                ("Event", "event_id"),
                ("Shift", "shift_type_slug"),
                ("Slot", "slot_index"),
                ("Member", "assigned_member_slug"),
                ("Status", "status"),
            ),
        ),
    )


@app.command("show")
def show(
    ctx: typer.Context,
    shift_id: Annotated[int, typer.Argument(help="Shift id (see `risk shift list`).")],
) -> None:
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    shift = repo.get_by_id(conn, shift_id)
    if shift is None:
        emit_error("shift.not_found", f"No shift with id {shift_id}.", mode=mode)
        return
    emit_success(asdict(shift), mode=mode)
