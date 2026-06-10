"""``risk config removal-method ...`` — soft-deleted, ADR-012."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from typing import Annotated

import typer

from risk.cli._common import mode_from_ctx, open_conn
from risk.cli.output import emit_error, emit_success, simple_lookup_table
from risk.db.connection import transaction
from risk.repos import removal_methods as repo

app = typer.Typer(help="Manage strike-removal methods (versioned via soft-delete).")


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
            "removal_method.integrity",
            f"A removal method with slug {slug!r} already exists — run "
            f"'risk config removal-method list --include-retired' to see all methods.",
            mode=mode,
        )
        return
    method = repo.get_active_by_slug(conn, slug)
    assert method is not None
    emit_success(
        asdict(method),
        mode=mode,
        table=simple_lookup_table(
            "Removal methods",
            [method],
            extra_cols=(("Deleted at", "deleted_at"),),
        ),
    )


@app.command("retire")
def retire(
    ctx: typer.Context,
    slug: Annotated[str, typer.Argument()],
) -> None:
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    with transaction(conn):
        affected = repo.retire(conn, slug)
    if affected == 0:
        emit_error(
            "removal_method.not_active", f"No active removal method with slug {slug!r}.", mode=mode
        )
        return
    emit_success({"slug": slug, "retired": True}, mode=mode)


@app.command("list")
def list_(
    ctx: typer.Context,
    include_retired: Annotated[bool, typer.Option("--include-retired")] = False,
) -> None:
    conn = open_conn(ctx)
    rows = repo.list_all_including_retired(conn) if include_retired else repo.list_active(conn)
    emit_success(
        [asdict(r) for r in rows],
        mode=mode_from_ctx(ctx),
        table=simple_lookup_table(
            "Removal methods",
            rows,
            extra_cols=(("Deleted at", "deleted_at"),),
        ),
    )
