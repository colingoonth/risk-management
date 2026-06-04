"""Per-entity repository for ``member_roles`` (member × role × semester)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MemberRoleRow:
    id: int
    member_id: int
    role_id: int
    semester_id: int
    member_slug: str
    role_slug: str
    semester_name: str
    starts_on: str | None
    ends_on: str | None


_SELECT_JOINED = """
SELECT
  mr.id, mr.member_id, mr.role_id, mr.semester_id,
  m.slug AS member_slug,
  r.slug AS role_slug,
  s.name AS semester_name,
  mr.starts_on, mr.ends_on
FROM member_roles mr
JOIN members m ON m.id = mr.member_id
JOIN roles r ON r.id = mr.role_id
JOIN semesters s ON s.id = mr.semester_id
"""


def _row(r: sqlite3.Row) -> MemberRoleRow:
    return MemberRoleRow(
        id=r["id"],
        member_id=r["member_id"],
        role_id=r["role_id"],
        semester_id=r["semester_id"],
        member_slug=r["member_slug"],
        role_slug=r["role_slug"],
        semester_name=r["semester_name"],
        starts_on=r["starts_on"],
        ends_on=r["ends_on"],
    )


def set_role(
    conn: sqlite3.Connection,
    *,
    member_id: int,
    role_id: int,
    semester_id: int,
    starts_on: str | None = None,
    ends_on: str | None = None,
) -> int:
    """Idempotent: re-issuing the same triple is a no-op (UNIQUE constraint)."""
    cur = conn.execute(
        """
        INSERT INTO member_roles (member_id, role_id, semester_id, starts_on, ends_on)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (member_id, role_id, semester_id) DO UPDATE SET
          starts_on = excluded.starts_on,
          ends_on = excluded.ends_on
        """,
        (member_id, role_id, semester_id, starts_on, ends_on),
    )
    return cur.rowcount


def unset_role(
    conn: sqlite3.Connection,
    *,
    member_id: int,
    role_id: int,
    semester_id: int,
) -> int:
    cur = conn.execute(
        """
        DELETE FROM member_roles
        WHERE member_id = ? AND role_id = ? AND semester_id = ?
        """,
        (member_id, role_id, semester_id),
    )
    return cur.rowcount


def list_for_member_in_semester(
    conn: sqlite3.Connection, *, member_id: int, semester_id: int
) -> list[MemberRoleRow]:
    rows = conn.execute(
        f"{_SELECT_JOINED} WHERE mr.member_id = ? AND mr.semester_id = ? ORDER BY r.slug",
        (member_id, semester_id),
    ).fetchall()
    return [_row(r) for r in rows]


def list_for_semester(conn: sqlite3.Connection, semester_id: int) -> list[MemberRoleRow]:
    rows = conn.execute(
        f"{_SELECT_JOINED} WHERE mr.semester_id = ? ORDER BY m.slug, r.slug",
        (semester_id,),
    ).fetchall()
    return [_row(r) for r in rows]


def list_for_member(conn: sqlite3.Connection, member_id: int) -> list[MemberRoleRow]:
    rows = conn.execute(
        f"{_SELECT_JOINED} WHERE mr.member_id = ? ORDER BY s.starts_on, r.slug",
        (member_id,),
    ).fetchall()
    return [_row(r) for r in rows]
