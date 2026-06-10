"""``risk config role ...`` — chair-managed roles + soft-exclude flags."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from typing import Annotated

import typer

from risk.cli._common import mode_from_ctx, open_conn
from risk.cli.output import emit_error, emit_success, simple_lookup_table
from risk.db.connection import transaction
from risk.repos import roles as repo

app = typer.Typer(help="Manage roles + their auto-assign exclusion behavior.")


@app.command("add")
def add(
    ctx: typer.Context,
    slug: Annotated[str, typer.Argument(help="Role slug, e.g. 'risk_chair', 'dj'.")],
    display_name: Annotated[str, typer.Option("--display-name")],
    automation_key: Annotated[
        str | None,
        typer.Option(
            "--automation-key",
            help="Stable key used by --allow flag (e.g. 'dj', 'risk-chair').",
        ),
    ] = None,
    default_excluded: Annotated[
        bool,
        typer.Option("--default-excluded", help="Hard-exclude by default from auto-assign."),
    ] = False,
    soft: Annotated[
        bool,
        typer.Option("--exclude-soft", help="Soft-exclude (can be overridden by --allow)."),
    ] = False,
) -> None:
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    try:
        with transaction(conn):
            repo.insert(
                conn,
                slug=slug,
                display_name=display_name,
                automation_key=automation_key,
                default_excluded=default_excluded,
                soft=soft,
            )
    except sqlite3.IntegrityError:
        emit_error(
            "role.integrity",
            f"A role with slug {slug!r} already exists — run 'risk config role list' "
            f"to see existing roles.",
            mode=mode,
        )
        return
    role = repo.get_by_slug(conn, slug)
    assert role is not None
    emit_success(
        asdict(role),
        mode=mode,
        table=simple_lookup_table(
            "Roles",
            [role],
            extra_cols=(
                ("Automation key", "automation_key"),
                ("Hard excl.", "default_excluded_from_assignment"),
                ("Soft", "exclude_is_soft"),
            ),
        ),
    )


@app.command("list")
def list_(ctx: typer.Context) -> None:
    rows = repo.list_all(open_conn(ctx))
    emit_success(
        [asdict(r) for r in rows],
        mode=mode_from_ctx(ctx),
        table=simple_lookup_table(
            "Roles",
            rows,
            extra_cols=(
                ("Automation key", "automation_key"),
                ("Hard excl.", "default_excluded_from_assignment"),
                ("Soft", "exclude_is_soft"),
            ),
        ),
    )


@app.command("rename")
def rename(
    ctx: typer.Context,
    slug: Annotated[str, typer.Argument(help="Role slug to rename.")],
    display_name: Annotated[str, typer.Option("--display-name")],
) -> None:
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    with transaction(conn):
        affected = repo.rename(conn, slug=slug, display_name=display_name)
    if affected == 0:
        emit_error("role.not_found", f"No role with slug {slug!r}.", mode=mode)
        return
    role = repo.get_by_slug(conn, slug)
    assert role is not None
    emit_success(asdict(role), mode=mode)
