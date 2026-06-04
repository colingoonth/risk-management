"""Per-entity repository for ``roles``."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Role:
    id: int
    slug: str
    display_name: str
    automation_key: str | None
    default_excluded_from_assignment: bool
    exclude_is_soft: bool


def _row(r: sqlite3.Row) -> Role:
    return Role(
        id=r["id"],
        slug=r["slug"],
        display_name=r["display_name"],
        automation_key=r["automation_key"],
        default_excluded_from_assignment=bool(r["default_excluded_from_assignment"]),
        exclude_is_soft=bool(r["exclude_is_soft"]),
    )


def insert(
    conn: sqlite3.Connection,
    *,
    slug: str,
    display_name: str,
    automation_key: str | None = None,
    default_excluded: bool = False,
    soft: bool = False,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO roles
          (slug, display_name, automation_key,
           default_excluded_from_assignment, exclude_is_soft)
        VALUES (?, ?, ?, ?, ?)
        """,
        (slug, display_name, automation_key, int(default_excluded), int(soft)),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def list_all(conn: sqlite3.Connection) -> list[Role]:
    rows = conn.execute("SELECT * FROM roles ORDER BY slug").fetchall()
    return [_row(r) for r in rows]


def get_by_slug(conn: sqlite3.Connection, slug: str) -> Role | None:
    row = conn.execute("SELECT * FROM roles WHERE slug = ?", (slug,)).fetchone()
    return _row(row) if row else None


def rename(conn: sqlite3.Connection, *, slug: str, display_name: str) -> int:
    """Edit display_name only; slug is immutable post-creation (ADR-011)."""
    cur = conn.execute(
        "UPDATE roles SET display_name = ? WHERE slug = ?",
        (display_name, slug),
    )
    return cur.rowcount


def get_soft_excluded(conn: sqlite3.Connection) -> list[Role]:
    """Roles eligible for ``--allow ROLE`` overrides."""
    rows = conn.execute(
        """
        SELECT * FROM roles
        WHERE exclude_is_soft = 1
          AND default_excluded_from_assignment = 1
          AND automation_key IS NOT NULL
        ORDER BY slug
        """
    ).fetchall()
    return [_row(r) for r in rows]
