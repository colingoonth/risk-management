"""``risk note ...`` — the chair's notes, from the shell.

The app is where Colin writes these. This exists so they can be READ and closed
without one, which is the half that matters when a rebuild is being driven from
a terminal: the notes are the input to that rebuild, and having to alt-tab to a
GUI to find out what the input is defeats the point of having them in the
database at all.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Annotated

import typer

from risk.cli._common import mode_from_ctx, open_conn
from risk.cli.output import OutputMode, attr_table, emit_error, emit_success
from risk.db.connection import transaction
from risk.repos import chair_notes as repo
from risk.repos import semesters as semesters_repo

app = typer.Typer(help="Chair notes — things the schedule can't work out on its own.")


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
    body: Annotated[str, typer.Argument(help="The note.")],
    standing: Annotated[
        bool,
        typer.Option(
            "--standing",
            help="A rule that applies to EVERY rebuild until retired, not a one-off.",
        ),
    ] = False,
    from_claude: Annotated[
        bool,
        typer.Option("--from-claude", help="File this as a question back to the chair."),
    ] = False,
    semester: Annotated[
        str | None, typer.Option("--semester", help="Defaults to the current semester.")
    ] = None,
) -> None:
    """Write a note.

    Example:
        risk note add "Nico can't do Oct 10, family thing"
        risk note add "Never two Etas on one setup crew" --standing
    """
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    semester_id = _resolve_semester(conn, mode, semester)
    try:
        with transaction(conn):
            note_id = repo.insert(
                conn,
                semester_id=semester_id,
                body=body,
                kind="standing" if standing else "one_off",
                author="claude" if from_claude else "chair",
            )
    except ValueError as exc:
        emit_error("note.invalid", str(exc), mode=mode)
        return
    created = repo.get_by_id(conn, note_id)
    assert created is not None
    emit_success(asdict(created), mode=mode)


@app.command("list")
def list_notes(
    ctx: typer.Context,
    open_only: Annotated[bool, typer.Option("--open", help="Only notes that still apply.")] = False,
    standing: Annotated[bool, typer.Option("--standing", help="Only standing rules.")] = False,
    semester: Annotated[
        str | None, typer.Option("--semester", help="Defaults to the current semester.")
    ] = None,
) -> None:
    """List notes, still-applies first.

    Example:
        risk note list --open
    """
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    semester_id = _resolve_semester(conn, mode, semester)
    rows = repo.list_for_semester(
        conn,
        semester_id,
        open_only=open_only,
        kind="standing" if standing else None,
    )
    emit_success(
        {"notes": [asdict(n) for n in rows]},
        mode=mode,
        table=attr_table(
            f"Chair notes (semester={semester_id})",
            rows,
            cols=(
                ("ID", "id"),
                ("Kind", "kind"),
                ("By", "author"),
                ("Added", "created_at"),
                ("Note", "body"),
                ("Closed", "closed_at"),
                ("Outcome", "closed_note"),
            ),
        ),
    )


@app.command("close")
def close(
    ctx: typer.Context,
    note_id: Annotated[int, typer.Argument(help="Note ID.")],
    outcome: Annotated[
        str | None, typer.Option("--outcome", help="What you actually did about it.")
    ] = None,
) -> None:
    """Mark a one-off done, or retire a standing rule.

    Recording the outcome is worth the extra words: the note says what was
    asked, and three months later the question is what was done.
    """
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    existing = repo.get_by_id(conn, note_id)
    if existing is None:
        emit_error("note.not_found", f"No note {note_id}.", mode=mode)
        return
    with transaction(conn):
        changed = repo.close(
            conn,
            note_id=note_id,
            closed_at=datetime.now(UTC).isoformat(timespec="seconds"),
            closed_note=outcome,
        )
    if changed == 0:
        emit_error(
            "note.already_closed",
            f"Note {note_id} was already closed on {existing.closed_at}.",
            mode=mode,
        )
        return
    updated = repo.get_by_id(conn, note_id)
    assert updated is not None
    emit_success(asdict(updated), mode=mode)


@app.command("reopen")
def reopen(
    ctx: typer.Context,
    note_id: Annotated[int, typer.Argument(help="Note ID.")],
) -> None:
    """Put a note back in force."""
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    if repo.get_by_id(conn, note_id) is None:
        emit_error("note.not_found", f"No note {note_id}.", mode=mode)
        return
    with transaction(conn):
        repo.reopen(conn, note_id=note_id)
    updated = repo.get_by_id(conn, note_id)
    assert updated is not None
    emit_success(asdict(updated), mode=mode)
