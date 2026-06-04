"""Read-only repo for ``member_statuses`` (chair edits via raw SQL if needed)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MemberStatus:
    id: int
    slug: str
    display_name: str
    excludes_from_assignment: bool


def _row(r: sqlite3.Row) -> MemberStatus:
    return MemberStatus(
        id=r["id"],
        slug=r["slug"],
        display_name=r["display_name"],
        excludes_from_assignment=bool(r["excludes_from_assignment"]),
    )


def list_all(conn: sqlite3.Connection) -> list[MemberStatus]:
    rows = conn.execute("SELECT * FROM member_statuses ORDER BY slug").fetchall()
    return [_row(r) for r in rows]


def get_by_slug(conn: sqlite3.Connection, slug: str) -> MemberStatus | None:
    row = conn.execute("SELECT * FROM member_statuses WHERE slug = ?", (slug,)).fetchone()
    return _row(row) if row else None
