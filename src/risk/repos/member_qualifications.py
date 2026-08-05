"""Per-entity repository for ``member_qualifications`` (member × qual × semester).

Deliberately semester-scoped: a member who turns 21 in October is not
retroactively qualified for September's bar shifts, and the DJ job changing
hands does not rewrite who was qualified last term. That is why granting is a
normal operation with a CLI rather than one-off setup data.

The table has no surrogate key — its primary key is the triple itself — so
grant/revoke work on the triple directly.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MemberQualificationRow:
    member_id: int
    qualification_id: int
    semester_id: int
    member_slug: str
    qualification_slug: str
    qualification_display_name: str
    semester_name: str


_SELECT_JOINED = """
SELECT
  mq.member_id, mq.qualification_id, mq.semester_id,
  m.slug AS member_slug,
  q.slug AS qualification_slug,
  q.display_name AS qualification_display_name,
  s.name AS semester_name
FROM member_qualifications mq
JOIN members m ON m.id = mq.member_id
JOIN qualifications q ON q.id = mq.qualification_id
JOIN semesters s ON s.id = mq.semester_id
"""


def _row(r: sqlite3.Row) -> MemberQualificationRow:
    return MemberQualificationRow(
        member_id=r["member_id"],
        qualification_id=r["qualification_id"],
        semester_id=r["semester_id"],
        member_slug=r["member_slug"],
        qualification_slug=r["qualification_slug"],
        qualification_display_name=r["qualification_display_name"],
        semester_name=r["semester_name"],
    )


def grant(
    conn: sqlite3.Connection,
    *,
    member_id: int,
    qualification_id: int,
    semester_id: int,
) -> int:
    """Idempotent: re-granting the same triple is a no-op, not an error."""
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO member_qualifications
          (member_id, qualification_id, semester_id)
        VALUES (?, ?, ?)
        """,
        (member_id, qualification_id, semester_id),
    )
    return cur.rowcount


def revoke(
    conn: sqlite3.Connection,
    *,
    member_id: int,
    qualification_id: int,
    semester_id: int,
) -> int:
    cur = conn.execute(
        """
        DELETE FROM member_qualifications
        WHERE member_id = ? AND qualification_id = ? AND semester_id = ?
        """,
        (member_id, qualification_id, semester_id),
    )
    return cur.rowcount


def list_for_member_in_semester(
    conn: sqlite3.Connection, *, member_id: int, semester_id: int
) -> list[MemberQualificationRow]:
    rows = conn.execute(
        f"{_SELECT_JOINED} WHERE mq.member_id = ? AND mq.semester_id = ? ORDER BY q.slug",
        (member_id, semester_id),
    ).fetchall()
    return [_row(r) for r in rows]


def list_for_member(conn: sqlite3.Connection, member_id: int) -> list[MemberQualificationRow]:
    rows = conn.execute(
        f"{_SELECT_JOINED} WHERE mq.member_id = ? ORDER BY s.starts_on, q.slug",
        (member_id,),
    ).fetchall()
    return [_row(r) for r in rows]


def list_for_semester(
    conn: sqlite3.Connection, *, semester_id: int, qualification_slug: str | None = None
) -> list[MemberQualificationRow]:
    if qualification_slug is None:
        rows = conn.execute(
            f"{_SELECT_JOINED} WHERE mq.semester_id = ? ORDER BY m.slug, q.slug",
            (semester_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            f"{_SELECT_JOINED} WHERE mq.semester_id = ? AND q.slug = ? ORDER BY m.slug",
            (semester_id, qualification_slug),
        ).fetchall()
    return [_row(r) for r in rows]


def member_ids_with(
    conn: sqlite3.Connection, *, semester_id: int, qualification_slug: str
) -> set[int]:
    """Member ids holding ``qualification_slug`` in ``semester_id``.

    Shaped for the eligibility filter that will gate bar/dj shift types.
    """
    rows = conn.execute(
        """
        SELECT mq.member_id
        FROM member_qualifications mq
        JOIN qualifications q ON q.id = mq.qualification_id
        WHERE mq.semester_id = ? AND q.slug = ?
        """,
        (semester_id, qualification_slug),
    ).fetchall()
    return {r["member_id"] for r in rows}
