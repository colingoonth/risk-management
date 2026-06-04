"""Per-entity repository for ``member_house_assignments``."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MemberHouseAssignment:
    id: int
    member_id: int
    house_id: int
    semester_id: int
    member_slug: str
    house_slug: str
    semester_name: str
    starts_on: str | None
    ends_on: str | None


_SELECT_JOINED = """
SELECT
  mha.id, mha.member_id, mha.house_id, mha.semester_id,
  m.slug AS member_slug,
  h.slug AS house_slug,
  s.name AS semester_name,
  mha.starts_on, mha.ends_on
FROM member_house_assignments mha
JOIN members m ON m.id = mha.member_id
JOIN houses h ON h.id = mha.house_id
JOIN semesters s ON s.id = mha.semester_id
"""


def _row(r: sqlite3.Row) -> MemberHouseAssignment:
    return MemberHouseAssignment(
        id=r["id"],
        member_id=r["member_id"],
        house_id=r["house_id"],
        semester_id=r["semester_id"],
        member_slug=r["member_slug"],
        house_slug=r["house_slug"],
        semester_name=r["semester_name"],
        starts_on=r["starts_on"],
        ends_on=r["ends_on"],
    )


def set_assignment(
    conn: sqlite3.Connection,
    *,
    member_id: int,
    house_id: int,
    semester_id: int,
    starts_on: str | None = None,
    ends_on: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO member_house_assignments
          (member_id, house_id, semester_id, starts_on, ends_on)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (member_id, semester_id) DO UPDATE SET
          house_id = excluded.house_id,
          starts_on = excluded.starts_on,
          ends_on = excluded.ends_on
        """,
        (member_id, house_id, semester_id, starts_on, ends_on),
    )


def clear_assignment(conn: sqlite3.Connection, *, member_id: int, semester_id: int) -> int:
    cur = conn.execute(
        "DELETE FROM member_house_assignments WHERE member_id = ? AND semester_id = ?",
        (member_id, semester_id),
    )
    return cur.rowcount


def list_for_semester(conn: sqlite3.Connection, semester_id: int) -> list[MemberHouseAssignment]:
    rows = conn.execute(
        f"{_SELECT_JOINED} WHERE mha.semester_id = ? ORDER BY h.slug, m.slug",
        (semester_id,),
    ).fetchall()
    return [_row(r) for r in rows]


def get_for_member_in_semester(
    conn: sqlite3.Connection, *, member_id: int, semester_id: int
) -> MemberHouseAssignment | None:
    row = conn.execute(
        f"{_SELECT_JOINED} WHERE mha.member_id = ? AND mha.semester_id = ?",
        (member_id, semester_id),
    ).fetchone()
    return _row(row) if row else None
