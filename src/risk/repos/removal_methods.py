"""Per-entity repository for ``removal_methods`` (soft-delete versioned, ADR-012)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RemovalMethod:
    id: int
    slug: str
    display_name: str
    deleted_at: str | None


def _row(r: sqlite3.Row) -> RemovalMethod:
    return RemovalMethod(
        id=r["id"],
        slug=r["slug"],
        display_name=r["display_name"],
        deleted_at=r["deleted_at"],
    )


def insert(conn: sqlite3.Connection, *, slug: str, display_name: str) -> int:
    cur = conn.execute(
        "INSERT INTO removal_methods (slug, display_name) VALUES (?, ?)",
        (slug, display_name),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def list_active(conn: sqlite3.Connection) -> list[RemovalMethod]:
    rows = conn.execute(
        "SELECT * FROM removal_methods WHERE deleted_at IS NULL ORDER BY slug"
    ).fetchall()
    return [_row(r) for r in rows]


def list_all_including_retired(conn: sqlite3.Connection) -> list[RemovalMethod]:
    rows = conn.execute(
        "SELECT * FROM removal_methods ORDER BY deleted_at IS NULL DESC, slug"
    ).fetchall()
    return [_row(r) for r in rows]


def get_active_by_slug(conn: sqlite3.Connection, slug: str) -> RemovalMethod | None:
    row = conn.execute(
        "SELECT * FROM removal_methods WHERE slug = ? AND deleted_at IS NULL",
        (slug,),
    ).fetchone()
    return _row(row) if row else None


def retire(conn: sqlite3.Connection, slug: str) -> int:
    cur = conn.execute(
        "UPDATE removal_methods SET deleted_at = datetime('now') WHERE slug = ? AND deleted_at IS NULL",
        (slug,),
    )
    return cur.rowcount
