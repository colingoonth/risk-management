"""Shared router helpers."""

from __future__ import annotations

import sqlite3

from risk.repos import semesters as semesters_repo


def resolve_semester(conn: sqlite3.Connection, name: str | None) -> semesters_repo.Semester:
    """Resolve ``name`` to a semester, or fall back to the current one.

    Raises ``LookupError`` (→ 404) when the named semester is missing or when no
    name is given and no current semester is set.
    """
    if name is not None:
        sem = semesters_repo.get_by_name(conn, name)
        if sem is None:
            raise LookupError(f"semester {name!r} not found")
        return sem
    current = semesters_repo.get_current(conn)
    if current is None:
        raise LookupError("no semester specified and no current semester set")
    return current
