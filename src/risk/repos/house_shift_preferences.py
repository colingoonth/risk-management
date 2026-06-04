"""Per-entity repository for ``house_shift_preferences`` (Layer 2 of the merge stack).

History rows are written by SQL triggers; reads come from this module.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HouseShiftPref:
    house_id: int
    event_type_id: int
    shift_type_id: int
    house_slug: str
    event_type_slug: str
    shift_type_slug: str
    min_count: int
    target_count: int


@dataclass(frozen=True, slots=True)
class HousePrefHistoryRow:
    id: int
    house_id: int
    event_type_id: int
    shift_type_id: int
    min_count: int | None
    target_count: int | None
    changed_at: str
    change_kind: str


def _row(r: sqlite3.Row) -> HouseShiftPref:
    return HouseShiftPref(
        house_id=r["house_id"],
        event_type_id=r["event_type_id"],
        shift_type_id=r["shift_type_id"],
        house_slug=r["house_slug"],
        event_type_slug=r["event_type_slug"],
        shift_type_slug=r["shift_type_slug"],
        min_count=r["min_count"],
        target_count=r["target_count"],
    )


def _hist(r: sqlite3.Row) -> HousePrefHistoryRow:
    return HousePrefHistoryRow(
        id=r["id"],
        house_id=r["house_id"],
        event_type_id=r["event_type_id"],
        shift_type_id=r["shift_type_id"],
        min_count=r["min_count"],
        target_count=r["target_count"],
        changed_at=r["changed_at"],
        change_kind=r["change_kind"],
    )


def upsert(
    conn: sqlite3.Connection,
    *,
    house_id: int,
    event_type_id: int,
    shift_type_id: int,
    min_count: int,
    target_count: int,
) -> None:
    conn.execute(
        """
        INSERT INTO house_shift_preferences
          (house_id, event_type_id, shift_type_id, min_count, target_count)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (house_id, event_type_id, shift_type_id) DO UPDATE SET
          min_count = excluded.min_count,
          target_count = excluded.target_count
        """,
        (house_id, event_type_id, shift_type_id, min_count, target_count),
    )


def delete(
    conn: sqlite3.Connection,
    *,
    house_id: int,
    event_type_id: int,
    shift_type_id: int,
) -> int:
    cur = conn.execute(
        """
        DELETE FROM house_shift_preferences
        WHERE house_id = ? AND event_type_id = ? AND shift_type_id = ?
        """,
        (house_id, event_type_id, shift_type_id),
    )
    return cur.rowcount


def list_for_house(conn: sqlite3.Connection, house_id: int) -> list[HouseShiftPref]:
    rows = conn.execute(
        """
        SELECT
          p.house_id, p.event_type_id, p.shift_type_id,
          h.slug AS house_slug,
          et.slug AS event_type_slug,
          st.slug AS shift_type_slug,
          p.min_count, p.target_count
        FROM house_shift_preferences p
        JOIN houses h ON h.id = p.house_id
        JOIN event_types et ON et.id = p.event_type_id
        JOIN shift_types st ON st.id = p.shift_type_id
        WHERE p.house_id = ?
        ORDER BY et.slug, st.slug
        """,
        (house_id,),
    ).fetchall()
    return [_row(r) for r in rows]


def history_for_house(conn: sqlite3.Connection, house_id: int) -> list[HousePrefHistoryRow]:
    rows = conn.execute(
        """
        SELECT * FROM house_shift_preferences_history
        WHERE house_id = ?
        ORDER BY changed_at DESC, id DESC
        """,
        (house_id,),
    ).fetchall()
    return [_hist(r) for r in rows]
