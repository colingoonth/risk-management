"""``risk unavailability ...`` subcommands (Phase 6)."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from typing import Annotated

import typer

from risk.cli._common import mode_from_ctx, open_conn
from risk.cli.output import OutputMode, attr_table, emit_error, emit_success
from risk.db.connection import transaction
from risk.repos import members as members_repo
from risk.repos import semesters as semesters_repo
from risk.repos import unavailability as repo

app = typer.Typer(help="Manage member unavailability windows.")


def _resolve_semester(conn: sqlite3.Connection, mode: OutputMode, semester_name: str | None) -> int:
    if semester_name is not None:
        sem = semesters_repo.get_by_name(conn, semester_name)
        if sem is None:
            emit_error("semester.not_found", f"No semester named {semester_name!r}.", mode=mode)
        else:
            return sem.id
    current = semesters_repo.get_current(conn)
    if current is None:
        emit_error(
            "semester.no_current",
            "No --semester given and no current semester set.",
            mode=mode,
        )
    assert current is not None
    return current.id


@app.command("add")
def add(
    ctx: typer.Context,
    member: Annotated[str, typer.Argument(help="Member slug, id, or alias.")],
    starts: Annotated[str, typer.Option("--starts", help="ISO YYYY-MM-DD inclusive.")],
    ends: Annotated[str, typer.Option("--ends", help="ISO YYYY-MM-DD inclusive.")],
    starts_at: Annotated[
        str | None,
        typer.Option("--starts-at", help="HH:MM. Omit both times for an ALL-DAY window."),
    ] = None,
    ends_at: Annotated[str | None, typer.Option("--ends-at", help="HH:MM.")] = None,
    weekday: Annotated[
        int | None,
        typer.Option(
            "--repeats-weekday",
            help="0=Mon..6=Sun. Only that weekday within the range, not every day.",
        ),
    ] = None,
    soft: Annotated[
        bool,
        typer.Option(
            "--soft",
            help="A preference, not a conflict: assign only if the slot would go unfilled.",
        ),
    ] = False,
    reason: Annotated[str | None, typer.Option("--reason")] = None,
    semester: Annotated[
        str | None, typer.Option("--semester", help="Defaults to current semester.")
    ] = None,
) -> None:
    """Add an unavailability window for a member.

    The time and recurrence columns have existed since migration 0012 and were
    unreachable from any shipped surface until now — so every window the chair
    could actually record was all-day, which is far blunter than most real
    conflicts. "Practice Tuesdays 6-9pm" recorded as an all-day block costs the
    member every setup crew he could have worked that afternoon.

    Examples:
        risk unavailability add first-last --starts 2026-09-19 --ends 2026-09-19
        risk unavailability add first-last --starts 2026-09-17 --ends 2026-09-18 \
            --starts-at 20:00 --ends-at 23:59 --soft --reason "night before a meet"
        risk unavailability add first-last --starts 2026-08-25 --ends 2026-12-05 \
            --repeats-weekday 1 --starts-at 18:00 --ends-at 21:00 --reason "practice"
    """
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    m = members_repo.resolve(conn, member)
    if m is None:
        emit_error("member.not_found", f"No member matching {member!r}.", mode=mode)
        return
    sem_id = _resolve_semester(conn, mode, semester)
    try:
        with transaction(conn):
            win_id = repo.insert(
                conn,
                member_id=m.id,
                semester_id=sem_id,
                starts_on=starts,
                ends_on=ends,
                starts_at_time=starts_at,
                ends_at_time=ends_at,
                repeats_weekday=weekday,
                is_soft=soft,
                reason=reason,
            )
    except sqlite3.IntegrityError as exc:
        exc_str = str(exc)
        if "unavailability_member_semester_window" in exc_str or "UNIQUE" in exc_str.upper():
            emit_error(
                "unavailability.duplicate",
                f"An unavailability window already exists for {m.slug} from {starts} to {ends}.",
                mode=mode,
            )
        else:
            emit_error(
                "unavailability.integrity",
                f"Cannot add unavailability window for {m.slug}: {exc_str}",
                mode=mode,
            )
        return
    win = repo.get_by_id(conn, win_id)
    assert win is not None
    emit_success({"unavailability": asdict(win)}, mode=mode)


@app.command("list")
def list_windows(
    ctx: typer.Context,
    member: Annotated[str | None, typer.Option("--member", help="Filter by member.")] = None,
    semester: Annotated[
        str | None, typer.Option("--semester", help="Defaults to current semester.")
    ] = None,
) -> None:
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    sem_id = _resolve_semester(conn, mode, semester)
    if member is not None:
        m = members_repo.resolve(conn, member)
        if m is None:
            emit_error("member.not_found", f"No member matching {member!r}.", mode=mode)
            return
        rows = repo.list_for_member_semester(conn, member_id=m.id, semester_id=sem_id)
    else:
        rows = repo.list_for_semester(conn, semester_id=sem_id)
    emit_success(
        {"unavailability": [asdict(r) for r in rows]},
        mode=mode,
        table=attr_table(
            f"Unavailability (semester={sem_id})",
            rows,
            # Times and recurrence are shown because omitting them was a
            # MISREPORT, not just a gap: a row meaning "busy Thursdays 6-11pm"
            # rendered identically to "gone from September to December", and a
            # chair reading that would have written the member off for the term.
            cols=(
                ("ID", "id"),
                ("Member", "member_slug"),
                ("Starts", "starts_on"),
                ("Ends", "ends_on"),
                ("Time", "time_label"),
                ("Repeats", "weekday_label"),
                ("Kind", "kind_label"),
                ("Reason", "reason"),
            ),
        ),
    )


@app.command("remove")
def remove(
    ctx: typer.Context,
    win_id: Annotated[int, typer.Argument(help="unavailability.id")],
) -> None:
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    try:
        with transaction(conn):
            removed = repo.delete(conn, win_id)
    except sqlite3.IntegrityError as exc:
        emit_error("unavailability.integrity", str(exc), mode=mode)
        return
    if removed == 0:
        emit_error(
            "unavailability.not_found",
            f"No unavailability window with id={win_id}.",
            mode=mode,
        )
        return
    emit_success({"removed_id": win_id}, mode=mode)
