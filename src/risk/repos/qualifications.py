"""Per-entity repository for ``qualifications`` (the lookup table).

Qualifications gate which members may fill which shift types — ``over-21`` for
bar, ``dj`` for the DJ slot (see ``shift_type_required_qualification``, seeded
in migration 0012). This module is the lookup half; the member × qualification
× semester join lives in ``member_qualifications``.
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
