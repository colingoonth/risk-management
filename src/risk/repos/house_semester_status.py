"""Per-entity repository for ``house_semester_status``: per-(house, semester) pledge mode."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HouseSemesterStatus:
    id: int
    house_id: int
    semester_id: int
    pledge_mode_id: int
    house_slug: str
    semester_name: str
    pledge_mode_slug: str


_SELECT_JOINED = """
SELECT
  hss.id, hss.house_id, hss.semester_id, hss.pledge_mode_id,
  h.slug AS house_slug,
  s.name AS semester_name,
  pm.slug AS pledge_mode_slug
FROM house_semester_status hss
JOIN houses h ON h.id = hss.house_id
JOIN semesters s ON s.id = hss.semester_id
JOIN pledge_modes pm ON pm.id = hss.pledge_mode_id
"""


def _row(r: sqlite3.Row) -> HouseSemesterStatus:
    return HouseSemesterStatus(
        id=r["id"],
        house_id=r["house_id"],
        semester_id=r["semester_id"],
        pledge_mode_id=r["pledge_mode_id"],
        house_slug=r["house_slug"],
        semester_name=r["semester_name"],
        pledge_mode_slug=r["pledge_mode_slug"],
    )


def set_mode(
    conn: sqlite3.Connection,
    *,
    house_id: int,
    semester_id: int,
    pledge_mode_id: int,
) -> None:
    conn.execute(
        """
        INSERT INTO house_semester_status (house_id, semester_id, pledge_mode_id)
        VALUES (?, ?, ?)
        ON CONFLICT (house_id, semester_id) DO UPDATE SET pledge_mode_id = excluded.pledge_mode_id
        """,
        (house_id, semester_id, pledge_mode_id),
    )


def get(conn: sqlite3.Connection, *, house_id: int, semester_id: int) -> HouseSemesterStatus | None:
    row = conn.execute(
        f"{_SELECT_JOINED} WHERE hss.house_id = ? AND hss.semester_id = ?",
        (house_id, semester_id),
    ).fetchone()
    return _row(row) if row else None


def list_for_semester(conn: sqlite3.Connection, semester_id: int) -> list[HouseSemesterStatus]:
    rows = conn.execute(
        f"{_SELECT_JOINED} WHERE hss.semester_id = ? ORDER BY h.slug",
        (semester_id,),
    ).fetchall()
    return [_row(r) for r in rows]
