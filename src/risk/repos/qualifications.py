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


def required_for_shift_type(conn: sqlite3.Connection, shift_type_id: int) -> list[Qualification]:
    """Qualifications a member must hold to work ``shift_type_id``.

    Empty for every shift type but ``dj``. The primary key on
    ``shift_type_required_qualification`` is the pair, so a shift type may carry
    more than one requirement; callers treat the list as a conjunction — hold
    all of them or you cannot work the slot.

    An empty list means ungated, NOT "unknown". A shift type with no row has no
    requirement, which is the normal case, so this deliberately does not raise
    the way ``shift_type_windows`` does.
    """
    rows = conn.execute(
        """
        SELECT q.id, q.slug, q.display_name
        FROM shift_type_required_qualification r
        JOIN qualifications q ON q.id = r.qualification_id
        WHERE r.shift_type_id = ?
        ORDER BY q.slug
        """,
        (shift_type_id,),
    ).fetchall()
    return [_row(r) for r in rows]


def shift_type_ids_with_requirements(conn: sqlite3.Connection) -> set[int]:
    """Shift type ids that are gated on at least one qualification.

    Lets a caller ask "is this type scarce?" without a query per shift type.
    """
    rows = conn.execute(
        "SELECT DISTINCT shift_type_id FROM shift_type_required_qualification"
    ).fetchall()
    return {int(r["shift_type_id"]) for r in rows}
