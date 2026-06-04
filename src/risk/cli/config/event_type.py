"""``risk config event-type ...`` — event types, allow-list, defaults (Layer 3)."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from typing import Annotated

import typer

from risk.cli._common import mode_from_ctx, open_conn
from risk.cli.output import attr_table, emit_error, emit_success, simple_lookup_table
from risk.db.connection import transaction
from risk.repos import event_type_shift_defaults as defaults_repo
from risk.repos import event_types as repo
from risk.repos import shift_types as stypes_repo

app = typer.Typer(help="Manage event types + their default shift requirements.")


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
    except sqlite3.IntegrityError as exc:
        emit_error("event_type.integrity", str(exc), mode=mode)
        return
    et = repo.get_by_slug(conn, slug)
    assert et is not None
    emit_success(asdict(et), mode=mode, table=simple_lookup_table("Event types", [et]))


@app.command("list")
def list_(ctx: typer.Context) -> None:
    rows = repo.list_all(open_conn(ctx))
    emit_success(
        [asdict(r) for r in rows],
        mode=mode_from_ctx(ctx),
        table=simple_lookup_table("Event types", rows),
    )


@app.command("allow-shift-type")
def allow_shift_type(
    ctx: typer.Context,
    event_type: Annotated[str, typer.Argument()],
    shift_type: Annotated[str, typer.Argument()],
) -> None:
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    et = repo.get_by_slug(conn, event_type)
    st = stypes_repo.get_by_slug(conn, shift_type)
    if et is None or st is None:
        emit_error("not_found", "event-type or shift-type slug not found.", mode=mode)
        return
    with transaction(conn):
        repo.allow_shift_type(conn, event_type_id=et.id, shift_type_id=st.id)
    emit_success(
        {"event_type": event_type, "shift_type": shift_type, "allowed": True},
        mode=mode,
    )


@app.command("disallow-shift-type")
def disallow_shift_type(
    ctx: typer.Context,
    event_type: Annotated[str, typer.Argument()],
    shift_type: Annotated[str, typer.Argument()],
) -> None:
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    et = repo.get_by_slug(conn, event_type)
    st = stypes_repo.get_by_slug(conn, shift_type)
    if et is None or st is None:
        emit_error("not_found", "event-type or shift-type slug not found.", mode=mode)
        return
    with transaction(conn):
        repo.disallow_shift_type(conn, event_type_id=et.id, shift_type_id=st.id)
    emit_success(
        {"event_type": event_type, "shift_type": shift_type, "allowed": False},
        mode=mode,
    )


@app.command("set-default")
def set_default(
    ctx: typer.Context,
    event_type: Annotated[str, typer.Argument()],
    shift_type: Annotated[str, typer.Argument()],
    min_count: Annotated[int, typer.Option("--min")],
    target_count: Annotated[int, typer.Option("--target")],
) -> None:
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    et = repo.get_by_slug(conn, event_type)
    st = stypes_repo.get_by_slug(conn, shift_type)
    if et is None or st is None:
        emit_error("not_found", "event-type or shift-type slug not found.", mode=mode)
        return
    try:
        with transaction(conn):
            defaults_repo.upsert(
                conn,
                event_type_id=et.id,
                shift_type_id=st.id,
                min_count=min_count,
                target_count=target_count,
            )
    except sqlite3.IntegrityError as exc:
        emit_error("default.integrity", str(exc), mode=mode)
        return
    rows = defaults_repo.list_for_event_type(conn, et.id)
    emit_success(
        [asdict(r) for r in rows],
        mode=mode,
        table=attr_table(
            f"Defaults for {et.slug}",
            rows,
            cols=(
                ("Shift type", "shift_type_slug"),
                ("Min", "min_count"),
                ("Target", "target_count"),
            ),
        ),
    )


@app.command("clear-default")
def clear_default(
    ctx: typer.Context,
    event_type: Annotated[str, typer.Argument()],
    shift_type: Annotated[str, typer.Argument()],
) -> None:
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    et = repo.get_by_slug(conn, event_type)
    st = stypes_repo.get_by_slug(conn, shift_type)
    if et is None or st is None:
        emit_error("not_found", "event-type or shift-type slug not found.", mode=mode)
        return
    with transaction(conn):
        deleted = defaults_repo.delete(conn, event_type_id=et.id, shift_type_id=st.id)
    emit_success({"deleted": deleted}, mode=mode)


@app.command("show")
def show(
    ctx: typer.Context,
    event_type: Annotated[str, typer.Argument()],
) -> None:
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    et = repo.get_by_slug(conn, event_type)
    if et is None:
        emit_error("event_type.not_found", f"No event type with slug {event_type!r}.", mode=mode)
        return
    allowed = repo.list_allowed_shift_types(conn, et.id)
    defaults = defaults_repo.list_for_event_type(conn, et.id)
    emit_success(
        {
            "event_type": asdict(et),
            "allowed_shift_types": allowed,
            "defaults": [asdict(d) for d in defaults],
        },
        mode=mode,
    )
