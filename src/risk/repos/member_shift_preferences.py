"""Per-entity repository for ``member_shift_preferences``.

One row is one steer: "this member would rather not work this shift type",
optionally narrowed to a weekday and/or an event type. See migration 0023 for
why these are rows of their own rather than unavailability windows.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MemberShiftPreference:
    id: int
    member_id: int
    member_slug: str
    member_display_name: str
    shift_type_id: int
    shift_type_slug: str
    weekday: int | None
    """Python's Monday=0..Sunday=6, matching ``unavailability.repeats_weekday``.
    NULL means every day."""
    event_type_id: int | None
    event_type_slug: str | None
    is_hard: bool
    reason: str | None

    def describe(self) -> str:
        """One line a chair can read back, e.g. 'no door on Fri at a krush'."""
        days = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
        bits = [f"no {self.shift_type_slug}"]
        if self.weekday is not None:
            bits.append(f"on {days[self.weekday]}")
        if self.event_type_slug is not None:
            bits.append(f"at a {self.event_type_slug}")
        return " ".join(bits) + ("" if self.is_hard else " (soft)")


_SELECT = """
SELECT p.id, p.member_id, m.slug AS member_slug, m.display_name AS member_display_name,
       p.shift_type_id, st.slug AS shift_type_slug, p.weekday,
       p.event_type_id, et.slug AS event_type_slug, p.is_hard, p.reason
FROM member_shift_preferences p
JOIN members m ON m.id = p.member_id
JOIN shift_types st ON st.id = p.shift_type_id
LEFT JOIN event_types et ON et.id = p.event_type_id
"""


def _row(r: sqlite3.Row) -> MemberShiftPreference:
    return MemberShiftPreference(
        id=r["id"],
        member_id=r["member_id"],
        member_slug=r["member_slug"],
        member_display_name=r["member_display_name"],
        shift_type_id=r["shift_type_id"],
        shift_type_slug=r["shift_type_slug"],
        weekday=r["weekday"],
        event_type_id=r["event_type_id"],
        event_type_slug=r["event_type_slug"],
        is_hard=bool(r["is_hard"]),
        reason=r["reason"],
    )


def add(
    conn: sqlite3.Connection,
    *,
    member_id: int,
    semester_id: int,
    shift_type_id: int,
    weekday: int | None = None,
    event_type_id: int | None = None,
    is_hard: bool = False,
    reason: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO member_shift_preferences
          (member_id, semester_id, shift_type_id, weekday, event_type_id, is_hard, reason)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (member_id, semester_id, shift_type_id, weekday, event_type_id, int(is_hard), reason),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def remove(conn: sqlite3.Connection, *, preference_id: int) -> int:
    return conn.execute(
        "DELETE FROM member_shift_preferences WHERE id = ?", (preference_id,)
    ).rowcount


def list_for_semester(
    conn: sqlite3.Connection, *, semester_id: int, member_id: int | None = None
) -> list[MemberShiftPreference]:
    sql = f"{_SELECT} WHERE p.semester_id = ?"
    params: list[object] = [semester_id]
    if member_id is not None:
        sql += " AND p.member_id = ?"
        params.append(member_id)
    sql += " ORDER BY m.display_name, st.slug"
    return [_row(r) for r in conn.execute(sql, params)]


def matching(
    conn: sqlite3.Connection,
    *,
    semester_id: int,
    shift_type_id: int,
    weekday: int,
    event_type_id: int,
) -> tuple[set[int], set[int]]:
    """``(soft_member_ids, hard_member_ids)`` steered off this exact slot.

    A NULL ``weekday`` or ``event_type_id`` on the row means "any", so the
    comparison is ``IS NULL OR =``. Narrowings are ANDed: a row carrying both
    matches only when both hold.
    """
    rows = conn.execute(
        """
        SELECT member_id, is_hard FROM member_shift_preferences
        WHERE semester_id = ?
          AND shift_type_id = ?
          AND (weekday IS NULL OR weekday = ?)
          AND (event_type_id IS NULL OR event_type_id = ?)
        """,
        (semester_id, shift_type_id, weekday, event_type_id),
    ).fetchall()
    soft = {int(r["member_id"]) for r in rows if not r["is_hard"]}
    hard = {int(r["member_id"]) for r in rows if r["is_hard"]}
    return soft - hard, hard
