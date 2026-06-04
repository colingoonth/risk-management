"""Per-entity repository for ``members`` (+ alias resolution)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Member:
    id: int
    slug: str
    display_name: str
    status_slug: str
    class_year: int | None
    notes: str | None


@dataclass(frozen=True, slots=True)
class MemberAlias:
    id: int
    member_id: int
    alias: str
    source: str | None
    created_at: str


_SELECT_WITH_STATUS = """
SELECT
  m.id, m.slug, m.display_name, ms.slug AS status_slug,
  m.class_year, m.notes
FROM members m
JOIN member_statuses ms ON ms.id = m.status_id
"""


def _row(r: sqlite3.Row) -> Member:
    return Member(
        id=r["id"],
        slug=r["slug"],
        display_name=r["display_name"],
        status_slug=r["status_slug"],
        class_year=r["class_year"],
        notes=r["notes"],
    )


def insert(
    conn: sqlite3.Connection,
    *,
    slug: str,
    display_name: str,
    status_id: int,
    class_year: int | None = None,
    notes: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO members
          (slug, display_name, status_id, class_year, notes)
        VALUES (?, ?, ?, ?, ?)
        """,
        (slug, display_name, status_id, class_year, notes),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def list_all(conn: sqlite3.Connection) -> list[Member]:
    rows = conn.execute(f"{_SELECT_WITH_STATUS} ORDER BY m.slug").fetchall()
    return [_row(r) for r in rows]


def list_by_status(conn: sqlite3.Connection, status_slug: str) -> list[Member]:
    rows = conn.execute(
        f"{_SELECT_WITH_STATUS} WHERE ms.slug = ? ORDER BY m.slug",
        (status_slug,),
    ).fetchall()
    return [_row(r) for r in rows]


def get_by_slug(conn: sqlite3.Connection, slug: str) -> Member | None:
    row = conn.execute(f"{_SELECT_WITH_STATUS} WHERE m.slug = ?", (slug,)).fetchone()
    return _row(row) if row else None


def get_by_id(conn: sqlite3.Connection, member_id: int) -> Member | None:
    row = conn.execute(f"{_SELECT_WITH_STATUS} WHERE m.id = ?", (member_id,)).fetchone()
    return _row(row) if row else None


def add_alias(
    conn: sqlite3.Connection, *, member_id: int, alias: str, source: str | None = None
) -> int:
    cur = conn.execute(
        "INSERT INTO member_aliases (member_id, alias, source) VALUES (?, ?, ?)",
        (member_id, alias, source),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def list_aliases(conn: sqlite3.Connection, member_id: int) -> list[MemberAlias]:
    rows = conn.execute(
        "SELECT * FROM member_aliases WHERE member_id = ? ORDER BY created_at",
        (member_id,),
    ).fetchall()
    return [
        MemberAlias(
            id=r["id"],
            member_id=r["member_id"],
            alias=r["alias"],
            source=r["source"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


def resolve(conn: sqlite3.Connection, key: str) -> Member | None:
    """Resolve ``key`` to a member: int id, exact slug, or registered alias."""
    if key.isdigit():
        m = get_by_id(conn, int(key))
        if m is not None:
            return m
    m = get_by_slug(conn, key)
    if m is not None:
        return m
    row = conn.execute(
        f"{_SELECT_WITH_STATUS} JOIN member_aliases ma ON ma.member_id = m.id WHERE ma.alias = ?",
        (key,),
    ).fetchone()
    return _row(row) if row else None


def update_status(conn: sqlite3.Connection, *, member_id: int, status_id: int) -> int:
    cur = conn.execute(
        "UPDATE members SET status_id = ? WHERE id = ?",
        (status_id, member_id),
    )
    return cur.rowcount
