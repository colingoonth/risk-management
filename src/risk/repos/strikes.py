"""Per-entity repository for ``strikes`` (Phase 5)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Strike:
    id: int
    member_id: int
    semester_id: int
    shift_id: int | None
    issued_on: str
    reason: str
    carried_from_strike_id: int | None
    closed_at: str | None


@dataclass(frozen=True, slots=True)
class NumberedStrike:
    """Open strike with its derived `strike_number` from `v_strike_numbers`."""

    id: int
    member_id: int
    semester_id: int
    strike_number: int
    issued_on: str
    reason: str


def _row(r: sqlite3.Row) -> Strike:
    return Strike(
        id=r["id"],
        member_id=r["member_id"],
        semester_id=r["semester_id"],
        shift_id=r["shift_id"],
        issued_on=r["issued_on"],
        reason=r["reason"],
        carried_from_strike_id=r["carried_from_strike_id"],
        closed_at=r["closed_at"],
    )


def _numbered(r: sqlite3.Row) -> NumberedStrike:
    return NumberedStrike(
        id=r["id"],
        member_id=r["member_id"],
        semester_id=r["semester_id"],
        strike_number=r["strike_number"],
        issued_on=r["issued_on"],
        reason=r["reason"],
    )


def insert(
    conn: sqlite3.Connection,
    *,
    member_id: int,
    semester_id: int,
    issued_on: str,
    reason: str,
    shift_id: int | None = None,
    carried_from_strike_id: int | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO strikes
          (member_id, semester_id, shift_id, issued_on, reason, carried_from_strike_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (member_id, semester_id, shift_id, issued_on, reason, carried_from_strike_id),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def get_by_id(conn: sqlite3.Connection, strike_id: int) -> Strike | None:
    row = conn.execute("SELECT * FROM strikes WHERE id = ?", (strike_id,)).fetchone()
    return _row(row) if row else None


def list_for_member_semester(
    conn: sqlite3.Connection,
    *,
    member_id: int,
    semester_id: int,
    include_closed: bool = False,
) -> list[Strike]:
    if include_closed:
        sql = "SELECT * FROM strikes WHERE member_id = ? AND semester_id = ? ORDER BY issued_on, id"
        rows = conn.execute(sql, (member_id, semester_id)).fetchall()
    else:
        sql = """
        SELECT * FROM strikes
        WHERE member_id = ? AND semester_id = ? AND closed_at IS NULL
        ORDER BY issued_on, id
        """
        rows = conn.execute(sql, (member_id, semester_id)).fetchall()
    return [_row(r) for r in rows]


def list_numbered_for_member_semester(
    conn: sqlite3.Connection, *, member_id: int, semester_id: int
) -> list[NumberedStrike]:
    """Open strikes with their derived `strike_number` from `v_strike_numbers`."""
    rows = conn.execute(
        """
        SELECT id, member_id, semester_id, strike_number, issued_on, reason
        FROM v_strike_numbers
        WHERE member_id = ? AND semester_id = ?
        ORDER BY strike_number
        """,
        (member_id, semester_id),
    ).fetchall()
    return [_numbered(r) for r in rows]


def count_active(conn: sqlite3.Connection, *, member_id: int, semester_id: int) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS n FROM strikes
        WHERE member_id = ? AND semester_id = ? AND closed_at IS NULL
        """,
        (member_id, semester_id),
    ).fetchone()
    return int(row["n"])


def count_total_in_semester(
    conn: sqlite3.Connection, *, member_id: int, semester_id: int
) -> int:
    """Total strikes ever issued in this semester (open + closed)."""
    row = conn.execute(
        """
        SELECT COUNT(*) AS n FROM strikes
        WHERE member_id = ? AND semester_id = ?
        """,
        (member_id, semester_id),
    ).fetchone()
    return int(row["n"])


def close(conn: sqlite3.Connection, strike_id: int, *, closed_at: str) -> int:
    cur = conn.execute(
        "UPDATE strikes SET closed_at = ? WHERE id = ? AND closed_at IS NULL",
        (closed_at, strike_id),
    )
    return cur.rowcount


def get_strike_by_number(
    conn: sqlite3.Connection, *, member_id: int, semester_id: int, strike_number: int
) -> NumberedStrike | None:
    row = conn.execute(
        """
        SELECT id, member_id, semester_id, strike_number, issued_on, reason
        FROM v_strike_numbers
        WHERE member_id = ? AND semester_id = ? AND strike_number = ?
        """,
        (member_id, semester_id, strike_number),
    ).fetchone()
    return _numbered(row) if row else None
