"""``risk semester ...`` subcommands."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from datetime import date as _date
from typing import Annotated

import typer

from risk.cli._common import mode_from_ctx, open_conn
from risk.cli.output import OutputMode, emit_error, emit_success, semesters_table
from risk.db.connection import transaction
from risk.repos import house_semester_status as hss_repo
from risk.repos import houses as houses_repo
from risk.repos import pledge_modes as pmode_repo
from risk.repos import semesters as semesters_repo
from risk.services import semester_archive as archive_svc

app = typer.Typer(help="Manage academic semesters.")


@app.command("add")
def add(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Semester name, e.g. 'FA25', 'SP26'.")],
    starts: Annotated[str, typer.Option("--starts", help="Start date YYYY-MM-DD.")],
    ends: Annotated[str, typer.Option("--ends", help="End date YYYY-MM-DD.")],
    pledge_takeover: Annotated[
        str | None,
        typer.Option("--pledge-takeover", help="Pledge takeover start date YYYY-MM-DD."),
    ] = None,
) -> None:
    """Create a new semester."""
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    try:
        with transaction(conn):
            sem_id = semesters_repo.insert(
                conn,
                name=name,
                starts_on=starts,
                ends_on=ends,
                pledge_takeover_starts_on=pledge_takeover,
            )
    except sqlite3.IntegrityError:
        emit_error(
            "semester.integrity",
            f"A semester named {name!r} already exists.",
            mode=mode,
        )
        return
    sem = semesters_repo.get_by_name(conn, name)
    assert sem is not None
    emit_success(asdict(sem) | {"created_id": sem_id}, mode=mode, table=semesters_table([sem]))


@app.command("list")
def list_(ctx: typer.Context) -> None:
    """List all semesters."""
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    rows = semesters_repo.list_all(conn)
    emit_success([asdict(r) for r in rows], mode=mode, table=semesters_table(rows))


@app.command("set-current")
def set_current(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Name of the semester to become current.")],
) -> None:
    """Atomically demote the previous current semester and promote ``name``."""
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    try:
        with transaction(conn):
            semesters_repo.set_current(conn, name)
    except LookupError as exc:
        emit_error("semester.not_found", str(exc), mode=mode)
        return
    sem = semesters_repo.get_by_name(conn, name)
    assert sem is not None
    emit_success(asdict(sem), mode=mode, table=semesters_table([sem]))


@app.command("set-house-mode")
def set_house_mode(
    ctx: typer.Context,
    semester_name: Annotated[str, typer.Argument(help="Semester name.")],
    house: Annotated[str, typer.Option("--house")],
    mode_slug: Annotated[
        str,
        typer.Option(
            "--mode",
            help="Pledge mode slug (normal | pledge_takeover_full | pledge_takeover_partial).",
        ),
    ],
) -> None:
    out = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    sem = semesters_repo.get_by_name(conn, semester_name)
    h = houses_repo.get_by_slug(conn, house)
    pmode = pmode_repo.get(conn, mode_slug)
    if sem is None:
        emit_error("semester.not_found", f"No semester named {semester_name!r}.", mode=out)
        return
    if h is None:
        emit_error("house.not_found", f"No house with slug {house!r}.", mode=out)
        return
    if pmode is None:
        emit_error("pledge_mode.not_found", f"No pledge mode with slug {mode_slug!r}.", mode=out)
        return
    with transaction(conn):
        hss_repo.set_mode(conn, house_id=h.id, semester_id=sem.id, pledge_mode_id=pmode.id)
    row = hss_repo.get(conn, house_id=h.id, semester_id=sem.id)
    assert row is not None
    emit_success(asdict(row), mode=out)


@app.command("resync-all")
def resync_all(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Semester to resync.")],
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run", help="Report event IDs that would be resynced; do not write."
        ),
    ] = False,
) -> None:
    """Re-pull merged shift requirements for every non-terminal event in a semester."""
    from risk.services import shift_requirements as sr_svc

    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    sem = semesters_repo.get_by_name(conn, name)
    if sem is None:
        emit_error("semester.not_found", f"No semester named {name!r}.", mode=mode)
        return
    if dry_run:
        event_ids = [
            int(r["id"])
            for r in conn.execute(
                """
                SELECT id FROM events
                WHERE semester_id = ?
                  AND status NOT IN ('completed', 'cancelled')
                ORDER BY date, id
                """,
                (sem.id,),
            ).fetchall()
        ]
        emit_success(
            {"would_resync_event_ids": event_ids, "count": len(event_ids), "dry_run": True},
            mode=mode,
        )
        return
    try:
        with transaction(conn):
            result = sr_svc.resync_semester(conn, semester_id=sem.id)
    except sqlite3.IntegrityError:
        emit_error(
            "semester.resync_integrity",
            f"Resync failed due to a constraint conflict in semester {name!r}. "
            f"Try running 'risk semester list' to confirm the semester state.",
            mode=mode,
        )
        return
    emit_success(
        {
            "semester": sem.name,
            "event_count": len(result),
            "rows_rewritten_per_event": result,
        },
        mode=mode,
    )


@app.command("archive")
def archive(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Semester to archive.")],
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Auto-dispose blockers (close strikes / carry pcs / cancel swaps).",
        ),
    ] = False,
    carry_to: Annotated[
        str | None,
        typer.Option(
            "--carry-to",
            help="Target semester for carry-forward strikes (else they close).",
        ),
    ] = None,
    on: Annotated[
        str | None,
        typer.Option("--on", help="Archive date (YYYY-MM-DD); defaults to today."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Run the validator only; do not mutate."),
    ] = False,
) -> None:
    """Validate, optionally bulk-dispose, then archive a semester."""
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    sem = semesters_repo.get_by_name(conn, name)
    if sem is None:
        emit_error("semester.not_found", f"No semester named {name!r}.", mode=mode)
        return
    if sem.archived_at is not None:
        emit_error(
            "semester.already_archived",
            f"Semester {name!r} archived at {sem.archived_at}.",
            mode=mode,
        )
        return
    carry_to_id: int | None = None
    if carry_to is not None:
        ct = semesters_repo.get_by_name(conn, carry_to)
        if ct is None:
            emit_error(
                "semester.not_found",
                f"Carry-to semester {carry_to!r} not found.",
                mode=mode,
            )
            return
        carry_to_id = ct.id

    archived_at = on if on is not None else _date.today().isoformat()
    report = archive_svc.validate(conn, semester_id=sem.id)

    if dry_run:
        emit_success(
            {"report": asdict(report), "would_archive_at": archived_at},
            mode=mode,
        )
        return

    try:
        with transaction(conn):
            result = archive_svc.archive(
                conn,
                semester_id=sem.id,
                archived_at=archived_at,
                force=force,
                carry_to_semester_id=carry_to_id,
            )
    except ValueError as exc:
        emit_error("semester.archive_blocked", str(exc), mode=mode)
        return
    except (LookupError, RuntimeError) as exc:
        emit_error("semester.archive_failed", str(exc), mode=mode)
        return
    if mode is OutputMode.HUMAN:
        from rich.table import Table as _Table
        tbl = _Table.grid(padding=(0, 2))
        tbl.add_column(style="bold green")
        tbl.add_column()
        tbl.add_row(f"Archived semester {name}", "")
        tbl.add_row("  Strikes closed:", str(result.strikes_closed))
        tbl.add_row("  Strikes carried forward:", str(result.strikes_carried_forward))
        tbl.add_row("  Consequences carried:", str(result.consequences_carried_forward))
        tbl.add_row("  Swaps cancelled:", str(result.swaps_cancelled))
        emit_success(
            {"report": asdict(report), "result": asdict(result)},
            mode=mode,
            table=tbl,
        )
    else:
        emit_success(
            {"report": asdict(report), "result": asdict(result)},
            mode=mode,
        )


@app.command("unarchive")
def unarchive(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Semester to unarchive.")],
) -> None:
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    sem = semesters_repo.get_by_name(conn, name)
    if sem is None:
        emit_error("semester.not_found", f"No semester named {name!r}.", mode=mode)
        return
    try:
        with transaction(conn):
            archive_svc.unarchive(conn, semester_id=sem.id)
    except (LookupError, ValueError) as exc:
        emit_error("semester.unarchive_failed", str(exc), mode=mode)
        return
    sem_after = semesters_repo.get_by_name(conn, name)
    assert sem_after is not None
    emit_success(asdict(sem_after), mode=mode)
