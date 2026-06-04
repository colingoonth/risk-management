"""Per-entity repository for ``shift_types``."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ShiftType:
    id: int
    slug: str
    display_name: str
    requires_ritual_cert: bool


def _row(r: sqlite3.Row) -> ShiftType:
    return ShiftType(
        id=r["id"],
        slug=r["slug"],
        display_name=r["display_name"],
        requires_ritual_cert=bool(r["requires_ritual_cert"]),
    )


def insert(
    conn: sqlite3.Connection,
    *,
    slug: str,
    display_name: str,
    requires_ritual_cert: bool = False,
) -> int:
    cur = conn.execute(
        "INSERT INTO shift_types (slug, display_name, requires_ritual_cert) VALUES (?, ?, ?)",
        (slug, display_name, int(requires_ritual_cert)),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def list_all(conn: sqlite3.Connection) -> list[ShiftType]:
    rows = conn.execute("SELECT * FROM shift_types ORDER BY slug").fetchall()
    return [_row(r) for r in rows]


def get_by_slug(conn: sqlite3.Connection, slug: str) -> ShiftType | None:
    row = conn.execute("SELECT * FROM shift_types WHERE slug = ?", (slug,)).fetchone()
    return _row(row) if row else None
