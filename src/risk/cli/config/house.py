"""``risk config house ...`` — houses + shift preferences (Layer 2 of merge stack)."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from typing import Annotated

import typer

from risk.cli._common import mode_from_ctx, open_conn
from risk.cli.output import attr_table, emit_error, emit_success, simple_lookup_table
from risk.db.connection import transaction
from risk.repos import event_types as etypes_repo
from risk.repos import house_shift_preferences as prefs_repo
from risk.repos import houses as houses_repo
from risk.repos import shift_types as stypes_repo

app = typer.Typer(help="Manage houses + their shift preferences.")


@app.command("add")
def add(
    ctx: typer.Context,
    slug: Annotated[str, typer.Argument(help="Lowercase slug, e.g. 'main', 'annex'.")],
    display_name: Annotated[str, typer.Option("--display-name", help="Human-readable name.")],
) -> None:
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    try:
        with transaction(conn):
            houses_repo.insert(conn, slug=slug, display_name=display_name)
    except sqlite3.IntegrityError as exc:
        emit_error("house.integrity", str(exc), mode=mode)
        return
    house = houses_repo.get_by_slug(conn, slug)
    assert house is not None
    emit_success(asdict(house), mode=mode, table=simple_lookup_table("Houses", [house]))


@app.command("list")
def list_(ctx: typer.Context) -> None:
    rows = houses_repo.list_all(open_conn(ctx))
    emit_success(
        [asdict(r) for r in rows],
        mode=mode_from_ctx(ctx),
        table=simple_lookup_table("Houses", rows),
    )


@app.command("set-pref")
def set_pref(
    ctx: typer.Context,
    house: Annotated[str, typer.Argument()],
    event_type: Annotated[str, typer.Argument()],
    shift_type: Annotated[str, typer.Argument()],
    min_count: Annotated[int, typer.Option("--min", help="Minimum positions to fill.")],
    target_count: Annotated[int, typer.Option("--target", help="Target positions to fill.")],
) -> None:
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    h = houses_repo.get_by_slug(conn, house)
    et = etypes_repo.get_by_slug(conn, event_type)
    st = stypes_repo.get_by_slug(conn, shift_type)
    if h is None:
        emit_error("house.not_found", f"No house with slug {house!r}.", mode=mode)
        return
    if et is None:
        emit_error("event_type.not_found", f"No event type with slug {event_type!r}.", mode=mode)
        return
    if st is None:
        emit_error("shift_type.not_found", f"No shift type with slug {shift_type!r}.", mode=mode)
        return
    try:
        with transaction(conn):
            prefs_repo.upsert(
                conn,
                house_id=h.id,
                event_type_id=et.id,
                shift_type_id=st.id,
                min_count=min_count,
                target_count=target_count,
            )
    except sqlite3.IntegrityError as exc:
        emit_error("house_pref.integrity", str(exc), mode=mode)
        return
    rows = prefs_repo.list_for_house(conn, h.id)
    emit_success(
        [asdict(r) for r in rows],
        mode=mode,
        table=attr_table(
            f"Preferences for {h.slug}",
            rows,
            cols=(
                ("Event type", "event_type_slug"),
                ("Shift type", "shift_type_slug"),
                ("Min", "min_count"),
                ("Target", "target_count"),
            ),
        ),
    )


@app.command("clear-pref")
def clear_pref(
    ctx: typer.Context,
    house: Annotated[str, typer.Argument()],
    event_type: Annotated[str, typer.Argument()],
    shift_type: Annotated[str, typer.Argument()],
) -> None:
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    h = houses_repo.get_by_slug(conn, house)
    et = etypes_repo.get_by_slug(conn, event_type)
    st = stypes_repo.get_by_slug(conn, shift_type)
    if h is None or et is None or st is None:
        emit_error("not_found", "house, event-type, or shift-type slug not found.", mode=mode)
        return
    with transaction(conn):
        deleted = prefs_repo.delete(conn, house_id=h.id, event_type_id=et.id, shift_type_id=st.id)
    emit_success({"deleted": deleted}, mode=mode)


@app.command("list-prefs")
def list_prefs(
    ctx: typer.Context,
    house: Annotated[str, typer.Argument()],
) -> None:
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    h = houses_repo.get_by_slug(conn, house)
    if h is None:
        emit_error("house.not_found", f"No house with slug {house!r}.", mode=mode)
        return
    rows = prefs_repo.list_for_house(conn, h.id)
    emit_success(
        [asdict(r) for r in rows],
        mode=mode,
        table=attr_table(
            f"Preferences for {h.slug}",
            rows,
            cols=(
                ("Event type", "event_type_slug"),
                ("Shift type", "shift_type_slug"),
                ("Min", "min_count"),
                ("Target", "target_count"),
            ),
        ),
    )


@app.command("pref-history")
def pref_history(
    ctx: typer.Context,
    house: Annotated[str, typer.Argument()],
) -> None:
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    h = houses_repo.get_by_slug(conn, house)
    if h is None:
        emit_error("house.not_found", f"No house with slug {house!r}.", mode=mode)
        return
    rows = prefs_repo.history_for_house(conn, h.id)
    emit_success([asdict(r) for r in rows], mode=mode)
