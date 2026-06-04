"""``risk semester ...`` subcommands."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from typing import Annotated

import typer

from risk.cli._common import mode_from_ctx, open_conn
from risk.cli.output import emit_error, emit_success, semesters_table
from risk.db.connection import transaction
from risk.repos import house_semester_status as hss_repo
from risk.repos import houses as houses_repo
from risk.repos import pledge_modes as pmode_repo
from risk.repos import semesters as semesters_repo

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
    except sqlite3.IntegrityError as exc:
        emit_error("semester.integrity", str(exc), mode=mode)
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
