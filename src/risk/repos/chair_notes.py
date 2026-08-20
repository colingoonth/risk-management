"""Per-entity repository for ``chair_notes``."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

KINDS: frozenset[str] = frozenset({"one_off", "standing"})
"""What a note is.

``one_off`` is actioned once and closed. ``standing`` applies to every rebuild
until it is retired — closing one means "stop applying this", NOT "done", and
conflating the two would silently drop a rule on the next fill.
"""

AUTHORS: frozenset[str] = frozenset({"chair", "claude"})
"""Who wrote it. Colin writes most; some are questions back to him."""


@dataclass(frozen=True, slots=True)
class ChairNote:
    id: int
    semester_id: int
    kind: str
    author: str
    body: str
    created_at: str
    closed_at: str | None
    closed_note: str | None

    @property
    def is_open(self) -> bool:
        return self.closed_at is None


def _row(r: sqlite3.Row) -> ChairNote:
    return ChairNote(
        id=r["id"],
        semester_id=r["semester_id"],
        kind=r["kind"],
        author=r["author"],
        body=r["body"],
        created_at=r["created_at"],
        closed_at=r["closed_at"],
        closed_note=r["closed_note"],
    )


def insert(
    conn: sqlite3.Connection,
    *,
    semester_id: int,
    body: str,
    kind: str = "one_off",
    author: str = "chair",
) -> int:
    """Write a note.

    Validated here as well as in the CHECK, because the value comes off an HTTP
    body or a CLI flag and a 400 naming the allowed values is a better answer
    than a raw IntegrityError.
    """
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {sorted(KINDS)}, got {kind!r}")
    if author not in AUTHORS:
        raise ValueError(f"author must be one of {sorted(AUTHORS)}, got {author!r}")
    if not body.strip():
        raise ValueError("a note needs a body")
    cur = conn.execute(
        """
        INSERT INTO chair_notes (semester_id, kind, author, body)
        VALUES (?, ?, ?, ?)
        """,
        (semester_id, kind, author, body.strip()),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def list_for_semester(
    conn: sqlite3.Connection,
    semester_id: int,
    *,
    open_only: bool = False,
    kind: str | None = None,
) -> list[ChairNote]:
    """Notes for a term.

    Ordered open-first, then standing rules ahead of one-offs, then newest
    first. The chair opening this page wants to see what still applies; the
    archive is the part he scrolls to, not the part he lands on.
    """
    clauses = ["semester_id = ?"]
    params: list[object] = [semester_id]
    if open_only:
        clauses.append("closed_at IS NULL")
    if kind is not None:
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {sorted(KINDS)}, got {kind!r}")
        clauses.append("kind = ?")
        params.append(kind)
    rows = conn.execute(
        f"""
        SELECT * FROM chair_notes
        WHERE {" AND ".join(clauses)}
        ORDER BY (closed_at IS NOT NULL),
                 (kind <> 'standing'),
                 created_at DESC, id DESC
        """,
        params,
    ).fetchall()
    return [_row(r) for r in rows]


def get_by_id(conn: sqlite3.Connection, note_id: int) -> ChairNote | None:
    row = conn.execute("SELECT * FROM chair_notes WHERE id = ?", (note_id,)).fetchone()
    return _row(row) if row else None


def close(
    conn: sqlite3.Connection, *, note_id: int, closed_at: str, closed_note: str | None = None
) -> int:
    """Mark a note done (one-off) or retired (standing).

    Guarded on ``closed_at IS NULL`` so re-closing is a no-op returning 0 rather
    than quietly overwriting the record of what was done the first time.
    """
    cur = conn.execute(
        """
        UPDATE chair_notes
        SET closed_at = ?, closed_note = ?
        WHERE id = ? AND closed_at IS NULL
        """,
        (closed_at, closed_note, note_id),
    )
    return cur.rowcount


def reopen(conn: sqlite3.Connection, *, note_id: int) -> int:
    """Put a note back in force.

    Clears ``closed_note`` with it: that field records what was done in response
    to the close, and leaving it attached to a reopened note would assert a
    resolution that no longer holds.
    """
    cur = conn.execute(
        """
        UPDATE chair_notes
        SET closed_at = NULL, closed_note = NULL
        WHERE id = ? AND closed_at IS NOT NULL
        """,
        (note_id,),
    )
    return cur.rowcount


def update_body(conn: sqlite3.Connection, *, note_id: int, body: str) -> int:
    if not body.strip():
        raise ValueError("a note needs a body")
    cur = conn.execute("UPDATE chair_notes SET body = ? WHERE id = ?", (body.strip(), note_id))
    return cur.rowcount


def delete(conn: sqlite3.Connection, *, note_id: int) -> int:
    cur = conn.execute("DELETE FROM chair_notes WHERE id = ?", (note_id,))
    return cur.rowcount
