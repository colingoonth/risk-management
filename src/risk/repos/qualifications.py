"""Per-entity repository for ``qualifications`` (the lookup table).

Qualifications gate which members may fill which shift types. ``dj`` is the only
gate that exists — two people in the chapter can actually DJ (see
``shift_type_required_qualification``, seeded in migration 0012).

``over-21`` is NOT a gate. It is informational data carried on 41 members from
the roster load: the 21+ list is about who can purchase alcohol, not who may
work the bar. Migration 0014 removed the bar requirement it was briefly wired
to. Do not treat holding ``over-21`` as a permission.

This module is the lookup half; the member × qualification × semester join lives
in ``member_qualifications``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Qualification:
    id: int
    slug: str
    display_name: str


def _row(r: sqlite3.Row) -> Qualification:
    return Qualification(
        id=r["id"],
        slug=r["slug"],
        display_name=r["display_name"],
    )


def list_all(conn: sqlite3.Connection) -> list[Qualification]:
    rows = conn.execute("SELECT * FROM qualifications ORDER BY slug").fetchall()
    return [_row(r) for r in rows]


def get_by_slug(conn: sqlite3.Connection, slug: str) -> Qualification | None:
    row = conn.execute("SELECT * FROM qualifications WHERE slug = ?", (slug,)).fetchone()
    return _row(row) if row else None
