"""Per-group members who must remain in a GroupMe chat without a shift.

This is deliberately a join table keyed by ``(group_slug, member_id)``. Being
permanent in the standalone setup/cleanup group says nothing about whether the
same member should remain in the Risk parent group.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PermanentMember:
    group_slug: str
    member_id: int
    display_name: str


def _row(row: sqlite3.Row) -> PermanentMember:
    return PermanentMember(
        group_slug=row["group_slug"],
        member_id=row["member_id"],
        display_name=row["display_name"],
    )


def add(conn: sqlite3.Connection, *, group_slug: str, member_id: int) -> None:
    """Make one roster member permanent in one named group.

    Repeating the same declaration is harmless, which keeps local database seed
    scripts replay-safe.
    """
    if not group_slug.strip():
        raise ValueError("a permanent membership needs a group slug")
    conn.execute(
        """
        INSERT INTO groupme_permanent_members (group_slug, member_id)
        VALUES (?, ?)
        ON CONFLICT(group_slug, member_id) DO NOTHING
        """,
        (group_slug.strip(), member_id),
    )


def remove(conn: sqlite3.Connection, *, group_slug: str, member_id: int) -> int:
    cur = conn.execute(
        "DELETE FROM groupme_permanent_members WHERE group_slug = ? AND member_id = ?",
        (group_slug, member_id),
    )
    return cur.rowcount


def list_for_group(conn: sqlite3.Connection, group_slug: str) -> list[PermanentMember]:
    rows = conn.execute(
        """
        SELECT p.group_slug, p.member_id, m.display_name
        FROM groupme_permanent_members p
        JOIN members m ON m.id = p.member_id
        WHERE p.group_slug = ?
        ORDER BY m.display_name, p.member_id
        """,
        (group_slug,),
    ).fetchall()
    return [_row(row) for row in rows]


def member_ids_for_group(conn: sqlite3.Connection, group_slug: str) -> frozenset[int]:
    return frozenset(member.member_id for member in list_for_group(conn, group_slug))
