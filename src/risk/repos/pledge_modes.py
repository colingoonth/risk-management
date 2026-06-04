"""Read-only repo for ``pledge_modes`` (closed set, resolver hard-codes the slugs)."""

from __future__ import annotations

import sqlite3

from risk.repos._lookups import LookupRow, get_by_slug, list_all


def list_all_modes(conn: sqlite3.Connection) -> list[LookupRow]:
    return list_all(conn, "pledge_modes")


def get(conn: sqlite3.Connection, slug: str) -> LookupRow | None:
    return get_by_slug(conn, "pledge_modes", slug)
