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
    except sqlite3.IntegrityError:
        emit_error(
            "house.integrity",
            f"A house with slug {slug!r} already exists — run 'risk config house list' "
            f"to see existing houses.",
            mode=mode,
        )
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
    house: Annotated[str, typer.Argument(help="House slug (e.g. axid, zta, kd).")],
    event_type: Annotated[str, typer.Argument(help="Event type slug.")],
    shift_type: Annotated[str, typer.Argument(help="Shift type slug.")],
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
    except sqlite3.IntegrityError:
        emit_error(
            "house_pref.integrity",
            f"Could not save shift preference for house {house!r} — check that the "
            f"event type and shift type exist and that counts are valid.",
            mode=mode,
        )
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
    house: Annotated[str, typer.Argument(help="House slug.")],
    event_type: Annotated[str, typer.Argument(help="Event type slug.")],
    shift_type: Annotated[str, typer.Argument(help="Shift type slug.")],
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
    with transaction(conn):
        deleted = prefs_repo.delete(conn, house_id=h.id, event_type_id=et.id, shift_type_id=st.id)
    emit_success({"deleted": deleted}, mode=mode)


@app.command("list-prefs")
def list_prefs(
    ctx: typer.Context,
    house: Annotated[str, typer.Argument(help="House slug.")],
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


@app.command("revert-prefs")
def revert_prefs(
    ctx: typer.Context,
    house: Annotated[str, typer.Argument(help="House slug.")],
    to: Annotated[
        str,
        typer.Option(
            "--to",
            help="Timestamp to restore to (matches house_shift_preferences_history.changed_at).",
        ),
    ],
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show planned changes only.")] = False,
) -> None:
    """Restore a house's shift preferences to their state at ``--to``."""
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    h = houses_repo.get_by_slug(conn, house)
    if h is None:
        emit_error("house.not_found", f"No house with slug {house!r}.", mode=mode)
        return
    snapshot = prefs_repo.state_at(conn, house_id=h.id, at_timestamp=to)
    current = {(p.event_type_id, p.shift_type_id): p for p in prefs_repo.list_for_house(conn, h.id)}
    target_keys = {(et, st) for et, st, mn, _tg in snapshot if mn is not None}
    to_upsert = [(et, st, mn, tg) for et, st, mn, tg in snapshot if mn is not None]
    to_delete = [k for k in current if k not in target_keys]
    plan = {
        "upsert_count": len(to_upsert),
        "delete_count": len(to_delete),
        "upserts": [
            {"event_type_id": et, "shift_type_id": st, "min_count": mn, "target_count": tg}
            for et, st, mn, tg in to_upsert
        ],
        "deletes": [{"event_type_id": et, "shift_type_id": st} for et, st in to_delete],
    }
    if dry_run:
        emit_success({"plan": plan, "dry_run": True}, mode=mode)
        return
    try:
        with transaction(conn):
            for et, st, mn, tg in to_upsert:
                assert mn is not None and tg is not None
                prefs_repo.upsert(
                    conn,
                    house_id=h.id,
                    event_type_id=et,
                    shift_type_id=st,
                    min_count=mn,
                    target_count=tg,
                )
            for et, st in to_delete:
                prefs_repo.delete(conn, house_id=h.id, event_type_id=et, shift_type_id=st)
    except sqlite3.IntegrityError:
        emit_error(
            "house.revert_integrity",
            f"Revert failed due to a constraint conflict while restoring preferences for "
            f"house {house!r}. The house state may be partially updated — revert again or "
            f"run 'risk config house list-prefs {house}' to inspect.",
            mode=mode,
        )
        return
    emit_success({"plan": plan, "applied": True}, mode=mode)


@app.command("pref-history")
def pref_history(
    ctx: typer.Context,
    house: Annotated[str, typer.Argument(help="House slug.")],
) -> None:
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    h = houses_repo.get_by_slug(conn, house)
    if h is None:
        emit_error("house.not_found", f"No house with slug {house!r}.", mode=mode)
        return
    rows = prefs_repo.history_for_house(conn, h.id)
    emit_success([asdict(r) for r in rows], mode=mode)
