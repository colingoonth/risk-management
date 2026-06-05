"""Per-entity repository for ``strike_removals`` and the link table (Phase 5)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StrikeRemoval:
    id: int
    member_id: int
    removal_method_id: int
    performed_on: str
    performed_by_member_id: int | None
    notes: str | None
    removal_method_slug: str


_SELECT_JOINED = """
SELECT
  sr.id, sr.member_id, sr.removal_method_id, sr.performed_on,
  sr.performed_by_member_id, sr.notes,
  rm.slug AS removal_method_slug
FROM strike_removals sr
JOIN removal_methods rm ON rm.id = sr.removal_method_id
"""


def _row(r: sqlite3.Row) -> StrikeRemoval:
    return StrikeRemoval(
        id=r["id"],
        member_id=r["member_id"],
        removal_method_id=r["removal_method_id"],
        performed_on=r["performed_on"],
        performed_by_member_id=r["performed_by_member_id"],
        notes=r["notes"],
        removal_method_slug=r["removal_method_slug"],
    )


def insert(
    conn: sqlite3.Connection,
    *,
    member_id: int,
    removal_method_id: int,
    performed_on: str,
    performed_by_member_id: int | None = None,
    notes: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO strike_removals
          (member_id, removal_method_id, performed_on, performed_by_member_id, notes)
        VALUES (?, ?, ?, ?, ?)
        """,
        (member_id, removal_method_id, performed_on, performed_by_member_id, notes),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def link_strike(conn: sqlite3.Connection, *, removal_id: int, strike_id: int) -> None:
    conn.execute(
        "INSERT INTO strike_removal_links (strike_removal_id, strike_id) VALUES (?, ?)",
        (removal_id, strike_id),
    )


def get_by_id(conn: sqlite3.Connection, removal_id: int) -> StrikeRemoval | None:
    row = conn.execute(
        f"{_SELECT_JOINED} WHERE sr.id = ?", (removal_id,)
    ).fetchone()
    return _row(row) if row else None


def list_for_member(conn: sqlite3.Connection, *, member_id: int) -> list[StrikeRemoval]:
    rows = conn.execute(
        f"{_SELECT_JOINED} WHERE sr.member_id = ? ORDER BY sr.performed_on, sr.id",
        (member_id,),
    ).fetchall()
    return [_row(r) for r in rows]


def list_linked_strike_ids(conn: sqlite3.Connection, removal_id: int) -> list[int]:
    rows = conn.execute(
        "SELECT strike_id FROM strike_removal_links WHERE strike_removal_id = ? ORDER BY strike_id",
        (removal_id,),
    ).fetchall()
    return [int(r["strike_id"]) for r in rows]
