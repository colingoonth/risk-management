"""Shared shape for tiny ``(slug, display_name)`` lookup tables.

Used for ``pledge_modes``, ``shift_types``, ``event_types``,
``strike_categories``, ``serving_methods``. Each gets its own thin wrapper
in a sibling module so call sites import a typed function.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LookupRow:
    id: int
    slug: str
    display_name: str


def _row(r: sqlite3.Row) -> LookupRow:
    return LookupRow(id=r["id"], slug=r["slug"], display_name=r["display_name"])


def insert(conn: sqlite3.Connection, table: str, *, slug: str, display_name: str) -> int:
    cur = conn.execute(
        f"INSERT INTO {table} (slug, display_name) VALUES (?, ?)",  # noqa: S608 — table is hard-coded caller-side
        (slug, display_name),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def list_all(conn: sqlite3.Connection, table: str) -> list[LookupRow]:
    rows = conn.execute(f"SELECT * FROM {table} ORDER BY slug").fetchall()  # noqa: S608
    return [_row(r) for r in rows]


def get_by_slug(conn: sqlite3.Connection, table: str, slug: str) -> LookupRow | None:
    row = conn.execute(f"SELECT * FROM {table} WHERE slug = ?", (slug,)).fetchone()  # noqa: S608
    return _row(row) if row else None
