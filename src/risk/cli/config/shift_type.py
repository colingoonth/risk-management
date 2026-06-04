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
    requires_ritual_cert: Annotated[bool, typer.Option("--requires-ritual-cert")] = False,
) -> None:
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    try:
        with transaction(conn):
            repo.insert(
                conn,
                slug=slug,
                display_name=display_name,
                requires_ritual_cert=requires_ritual_cert,
            )
    except sqlite3.IntegrityError as exc:
        emit_error("shift_type.integrity", str(exc), mode=mode)
        return
    st = repo.get_by_slug(conn, slug)
    assert st is not None
    emit_success(
        asdict(st),
        mode=mode,
        table=simple_lookup_table(
            "Shift types",
            [st],
            extra_cols=(("Ritual cert", "requires_ritual_cert"),),
        ),
    )


@app.command("list")
def list_(ctx: typer.Context) -> None:
    rows = repo.list_all(open_conn(ctx))
    emit_success(
        [asdict(r) for r in rows],
        mode=mode_from_ctx(ctx),
        table=simple_lookup_table(
            "Shift types",
            rows,
            extra_cols=(("Ritual cert", "requires_ritual_cert"),),
        ),
    )
