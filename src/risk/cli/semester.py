"""``risk semester ...`` subcommands."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from typing import Annotated, Any

import typer

from risk.cli.output import (
    OutputMode,
    emit_error,
    emit_success,
    semesters_table,
)
from risk.db.connection import connect, resolve_db_path, transaction
from risk.db.schema import ensure_schema
from risk.repos import semesters as semesters_repo

app = typer.Typer(no_args_is_help=True, help="Manage academic semesters.")


def _mode_from_ctx(ctx: typer.Context) -> OutputMode:
    obj: dict[str, Any] = ctx.obj or {}
    mode = obj.get("output_mode", OutputMode.HUMAN)
    assert isinstance(mode, OutputMode)
    return mode


def _conn(ctx: typer.Context) -> sqlite3.Connection:
    obj: dict[str, Any] = ctx.obj or {}
    db_path = resolve_db_path(obj.get("db_path"))
    conn = connect(db_path)
    ensure_schema(conn)
    return conn


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
    mode = _mode_from_ctx(ctx)
    conn = _conn(ctx)
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
    mode = _mode_from_ctx(ctx)
    conn = _conn(ctx)
    rows = semesters_repo.list_all(conn)
    emit_success([asdict(r) for r in rows], mode=mode, table=semesters_table(rows))
