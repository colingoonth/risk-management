"""``risk event ...`` subcommands."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from typing import Annotated

import typer

from risk.cli._common import mode_from_ctx, open_conn
from risk.cli.output import OutputMode, attr_table, emit_error, emit_success
from risk.db.connection import transaction
from risk.repos import event_shift_requirements as req_repo
from risk.repos import event_types as etypes_repo
from risk.repos import events as repo
from risk.repos import houses as houses_repo
from risk.repos import semesters as semesters_repo
from risk.repos import shift_types as stypes_repo
from risk.services import shift_requirements as svc

app = typer.Typer(help="Manage chapter events.")


def _resolve_semester_for_event(
    conn: sqlite3.Connection, mode: OutputMode, semester_name: str | None
) -> int:
    if semester_name is not None:
        sem = semesters_repo.get_by_name(conn, semester_name)
        if sem is None:
            emit_error("semester.not_found", f"No semester named {semester_name!r}.", mode=mode)
        else:
            return sem.id
    current = semesters_repo.get_current(conn)
    if current is None:
        emit_error(
            "semester.no_current",
            "No --semester given and no current semester set.",
            mode=mode,
        )
    assert current is not None
    return current.id


@app.command("add")
def add(
    ctx: typer.Context,
    display_name: Annotated[str, typer.Option("--name", help="Event display name.")],
    event_type: Annotated[str, typer.Option("--type", help="Event type slug.")],
    date: Annotated[str, typer.Option("--date", help="ISO YYYY-MM-DD.")],
    host: Annotated[
        str | None, typer.Option("--host", help="House slug (omit for off-site).")
    ] = None,
    start: Annotated[
        str | None, typer.Option("--start", help="HH:MM (must pair with --end).")
    ] = None,
    end: Annotated[
        str | None, typer.Option("--end", help="HH:MM (must pair with --start).")
    ] = None,
    semester: Annotated[
        str | None, typer.Option("--semester", help="Defaults to the current semester.")
    ] = None,
    notes: Annotated[str | None, typer.Option("--notes")] = None,
) -> None:
    """Create an event and snapshot its shift requirements from the 3-layer merge."""
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    sem_id = _resolve_semester_for_event(conn, mode, semester)
    et = etypes_repo.get_by_slug(conn, event_type)
    if et is None:
        emit_error("event_type.not_found", f"No event type with slug {event_type!r}.", mode=mode)
        return
    host_id = None
    if host is not None:
        h = houses_repo.get_by_slug(conn, host)
        if h is None:
            emit_error("house.not_found", f"No house with slug {host!r}.", mode=mode)
            return
        host_id = h.id

    try:
        with transaction(conn):
            event_id = repo.insert(
                conn,
                semester_id=sem_id,
                event_type_id=et.id,
                display_name=display_name,
                date=date,
                host_house_id=host_id,
                start_time=start,
                end_time=end,
                notes=notes,
            )
            svc.snapshot_for_event(conn, event_id)
    except sqlite3.IntegrityError as exc:
        emit_error("event.integrity", str(exc), mode=mode)
        return

    ev = repo.get_by_id(conn, event_id)
    reqs = req_repo.list_for_event_with_source(conn, event_id)
    assert ev is not None
    emit_success(
        {"event": asdict(ev), "requirements": [asdict(r) for r in reqs]},
        mode=mode,
        table=attr_table(
            f"Requirements snapshot for {ev.display_name}",
            reqs,
            cols=(
                ("Shift type", "shift_type_slug"),
                ("Min", "min_count"),
                ("Target", "target_count"),
                ("Source", "source_layer"),
            ),
        ),
    )


@app.command("list")
def list_(
    ctx: typer.Context,
    semester: Annotated[str | None, typer.Option("--semester")] = None,
    status: Annotated[str | None, typer.Option("--status")] = None,
) -> None:
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    sem_id = _resolve_semester_for_event(conn, mode, semester)
    rows = repo.list_for_semester(conn, sem_id, status=status)
    emit_success(
        [asdict(r) for r in rows],
        mode=mode,
        table=attr_table(
            "Events",
            rows,
            cols=(
                ("ID", "id"),
                ("Name", "display_name"),
                ("Type", "event_type_slug"),
                ("Date", "date"),
                ("Host", "host_house_slug"),
                ("Status", "status"),
                ("Resync", "resync_pending"),
            ),
        ),
    )


@app.command("show")
def show(
    ctx: typer.Context,
    event: Annotated[str, typer.Argument(help="Event id or display_name in current semester.")],
) -> None:
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    ev = repo.resolve(conn, event)
    if ev is None:
        emit_error("event.not_found", f"Could not resolve event {event!r}.", mode=mode)
        return
    reqs = req_repo.list_for_event_with_source(conn, ev.id)
    emit_success(
        {"event": asdict(ev), "requirements": [asdict(r) for r in reqs]},
        mode=mode,
        table=attr_table(
            f"Requirements for {ev.display_name}",
            reqs,
            cols=(
                ("Shift type", "shift_type_slug"),
                ("Min", "min_count"),
                ("Target", "target_count"),
                ("Source", "source_layer"),
            ),
        ),
    )


@app.command("set-shift-req")
def set_shift_req(
    ctx: typer.Context,
    event: Annotated[str, typer.Argument()],
    shift_type: Annotated[str, typer.Argument()],
    min_count: Annotated[int, typer.Option("--min")],
    target_count: Annotated[int, typer.Option("--target")],
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Skip the confirmation prompt for target=0."),
    ] = False,
) -> None:
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    ev = repo.resolve(conn, event)
    st = stypes_repo.get_by_slug(conn, shift_type)
    if ev is None:
        emit_error("event.not_found", f"Could not resolve event {event!r}.", mode=mode)
        return
    if st is None:
        emit_error("shift_type.not_found", f"No shift type with slug {shift_type!r}.", mode=mode)
        return
    if target_count == 0 and not yes:
        if mode is OutputMode.HUMAN:
            confirmed = typer.confirm(
                f"target=0 explicitly suppresses {st.slug} on event {ev.display_name}. Continue?",
                default=False,
            )
            if not confirmed:
                emit_error("aborted", "User cancelled.", mode=mode, exit_code=130)
                return
        else:
            emit_error(
                "confirm_required",
                "target=0 suppresses this shift type; re-run with --yes to confirm.",
                mode=mode,
                exit_code=2,
            )
            return

    try:
        with transaction(conn):
            svc.apply_manual_override(
                conn,
                event_id=ev.id,
                shift_type_id=st.id,
                min_count=min_count,
                target_count=target_count,
            )
    except sqlite3.IntegrityError as exc:
        emit_error("requirement.integrity", str(exc), mode=mode)
        return
    reqs = req_repo.list_for_event_with_source(conn, ev.id)
    emit_success(
        [asdict(r) for r in reqs],
        mode=mode,
        table=attr_table(
            f"Requirements for {ev.display_name}",
            reqs,
            cols=(
                ("Shift type", "shift_type_slug"),
                ("Min", "min_count"),
                ("Target", "target_count"),
                ("Source", "source_layer"),
            ),
        ),
    )


@app.command("clear-shift-req")
def clear_shift_req(
    ctx: typer.Context,
    event: Annotated[str, typer.Argument()],
    shift_type: Annotated[str, typer.Argument()],
) -> None:
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    ev = repo.resolve(conn, event)
    st = stypes_repo.get_by_slug(conn, shift_type)
    if ev is None or st is None:
        emit_error("not_found", "event or shift-type slug not found.", mode=mode)
        return
    with transaction(conn):
        deleted = svc.clear_requirement(conn, event_id=ev.id, shift_type_id=st.id)
    emit_success({"deleted": deleted}, mode=mode)


@app.command("resync-shift-reqs")
def resync_shift_reqs(
    ctx: typer.Context,
    event: Annotated[str, typer.Argument()],
) -> None:
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    ev = repo.resolve(conn, event)
    if ev is None:
        emit_error("event.not_found", f"Could not resolve event {event!r}.", mode=mode)
        return
    with transaction(conn):
        written = svc.resync_event(conn, ev.id)
    reqs = req_repo.list_for_event_with_source(conn, ev.id)
    emit_success(
        {
            "written": [asdict(r) for r in written],
            "requirements_after": [asdict(r) for r in reqs],
        },
        mode=mode,
        table=attr_table(
            f"Requirements for {ev.display_name} (post-resync)",
            reqs,
            cols=(
                ("Shift type", "shift_type_slug"),
                ("Min", "min_count"),
                ("Target", "target_count"),
                ("Source", "source_layer"),
            ),
        ),
    )


@app.command("set-host")
def set_host(
    ctx: typer.Context,
    event: Annotated[str, typer.Argument()],
    host: Annotated[
        str | None,
        typer.Option(
            "--house", help="House slug (omit or pass '' to clear; flips resync_pending=1)."
        ),
    ] = None,
) -> None:
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    ev = repo.resolve(conn, event)
    if ev is None:
        emit_error("event.not_found", f"Could not resolve event {event!r}.", mode=mode)
        return
    host_id: int | None = None
    if host is not None and host != "":
        h = houses_repo.get_by_slug(conn, host)
        if h is None:
            emit_error("house.not_found", f"No house with slug {host!r}.", mode=mode)
            return
        host_id = h.id
    with transaction(conn):
        repo.update_host(conn, event_id=ev.id, host_house_id=host_id)
    updated = repo.get_by_id(conn, ev.id)
    assert updated is not None
    emit_success(asdict(updated), mode=mode)


@app.command("cancel")
def cancel(
    ctx: typer.Context,
    event: Annotated[str, typer.Argument()],
) -> None:
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    ev = repo.resolve(conn, event)
    if ev is None:
        emit_error("event.not_found", f"Could not resolve event {event!r}.", mode=mode)
        return
    with transaction(conn):
        repo.update_status(conn, event_id=ev.id, status="cancelled")
    emit_success({"event": ev.display_name, "status": "cancelled"}, mode=mode)
