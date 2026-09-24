"""Chair notes — the things the schedule cannot infer.

The input side of the loop. Colin writes what he knows that the database does
not ("Sam is out Oct 10"), the next rebuild acts on it, and the note carries the
record of what was done.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from fastapi import APIRouter, Depends

from risk.api.deps import get_conn
from risk.api.errors import service_errors
from risk.api.routers._util import resolve_semester
from risk.api.schemas import (
    ChairNoteBodyIn,
    ChairNoteCloseIn,
    ChairNoteIn,
    ChairNoteOut,
)
from risk.db.connection import transaction
from risk.repos import chair_notes as notes_repo

router = APIRouter(prefix="/notes", tags=["notes"])


@router.get("", response_model=list[ChairNoteOut])
def list_notes(
    semester: str | None = None,
    open_only: bool = False,
    kind: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
) -> list[ChairNoteOut]:
    with service_errors():
        sem = resolve_semester(conn, semester)
        rows = notes_repo.list_for_semester(conn, sem.id, open_only=open_only, kind=kind)
    return [ChairNoteOut.model_validate(n) for n in rows]


@router.post("", response_model=ChairNoteOut, status_code=201)
def create_note(
    body: ChairNoteIn,
    semester: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
) -> ChairNoteOut:
    with service_errors():
        sem = resolve_semester(conn, semester)
        with transaction(conn):
            note_id = notes_repo.insert(
                conn,
                semester_id=sem.id,
                body=body.body,
                kind=body.kind,
                author=body.author,
            )
    created = notes_repo.get_by_id(conn, note_id)
    assert created is not None
    return ChairNoteOut.model_validate(created)


@router.post("/{note_id}/close", response_model=ChairNoteOut)
def close_note(
    note_id: int,
    body: ChairNoteCloseIn,
    conn: sqlite3.Connection = Depends(get_conn),
) -> ChairNoteOut:
    """Mark a one-off done, or retire a standing rule.

    Stamped with wall-clock UTC rather than an event date: unlike an assignment,
    this records when a human did something, and there is no schedule date it
    could sensibly be pinned to.
    """
    with service_errors():
        existing = notes_repo.get_by_id(conn, note_id)
        if existing is None:
            raise LookupError(f"note {note_id} not found")
        with transaction(conn):
            notes_repo.close(
                conn,
                note_id=note_id,
                closed_at=datetime.now(UTC).isoformat(timespec="seconds"),
                closed_note=body.closed_note,
            )
    updated = notes_repo.get_by_id(conn, note_id)
    assert updated is not None
    return ChairNoteOut.model_validate(updated)


@router.post("/{note_id}/reopen", response_model=ChairNoteOut)
def reopen_note(note_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> ChairNoteOut:
    with service_errors():
        existing = notes_repo.get_by_id(conn, note_id)
        if existing is None:
            raise LookupError(f"note {note_id} not found")
        with transaction(conn):
            notes_repo.reopen(conn, note_id=note_id)
    updated = notes_repo.get_by_id(conn, note_id)
    assert updated is not None
    return ChairNoteOut.model_validate(updated)


@router.put("/{note_id}", response_model=ChairNoteOut)
def edit_note(
    note_id: int, body: ChairNoteBodyIn, conn: sqlite3.Connection = Depends(get_conn)
) -> ChairNoteOut:
    with service_errors():
        existing = notes_repo.get_by_id(conn, note_id)
        if existing is None:
            raise LookupError(f"note {note_id} not found")
        with transaction(conn):
            notes_repo.update_body(conn, note_id=note_id, body=body.body)
    updated = notes_repo.get_by_id(conn, note_id)
    assert updated is not None
    return ChairNoteOut.model_validate(updated)


@router.delete("/{note_id}", status_code=204)
def delete_note(note_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> None:
    with service_errors():
        existing = notes_repo.get_by_id(conn, note_id)
        if existing is None:
            raise LookupError(f"note {note_id} not found")
        with transaction(conn):
            notes_repo.delete(conn, note_id=note_id)
