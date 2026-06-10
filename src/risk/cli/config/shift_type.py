"""``risk config shift-type ...`` — manage shift types."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from typing import Annotated

import typer

from risk.cli._common import mode_from_ctx, open_conn
from risk.cli.output import emit_error, emit_success, simple_lookup_table
from risk.db.connection import transaction
from risk.repos import shift_types as repo

app = typer.Typer(help="Manage shift types.")


@app.command("add")
def add(
    ctx: typer.Context,
    slug: Annotated[str, typer.Argument()],
    display_name: Annotated[str, typer.Option("--display-name")],
) -> None:
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    try:
        with transaction(conn):
            repo.insert(conn, slug=slug, display_name=display_name)
    except sqlite3.IntegrityError:
        emit_error(
            "shift_type.integrity",
            f"A shift type with slug {slug!r} already exists — run "
            f"'risk config shift-type list' to see existing shift types.",
            mode=mode,
        )
        return
    st = repo.get_by_slug(conn, slug)
    assert st is not None
    emit_success(
        asdict(st),
        mode=mode,
        table=simple_lookup_table("Shift types", [st]),
    )


@app.command("list")
def list_(ctx: typer.Context) -> None:
    rows = repo.list_all(open_conn(ctx))
    emit_success(
        [asdict(r) for r in rows],
        mode=mode_from_ctx(ctx),
        table=simple_lookup_table("Shift types", rows),
    )
