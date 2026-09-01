"""Per-entity repository for ``groupme_groups`` — the slug → GroupMe id address book.

The real ids never appear in this repository. They are seeded into the chair's
local database (``risk groupme seed``) and resolved here, so every other module
can talk about ``risk-friday`` and stay publishable.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

PARENT_SLUG: str = "risk-parent"
"""The Risk group the day topics hang off. Topics have no membership of their
own — adds and removes are done here — so this slug is load-bearing."""

SETUP_GROUP_SLUG: str = "setup-cleanup"
"""The setup/cleanup group. Registered with ``parent_slug=None`` and
``weekday=None`` — it is a real group with its own membership, never a topic.

Crew posts are still labelled and selected by the PARTY date. Weekday topics
under this slug are optional: register them and each party's crews post into
that weekday's topic, leave them out and every crew posts into the group
itself. Both are correct; the topics only reduce noise.
"""

ROSTER_SOURCE_SLUG: str = "roster-source"
"""The chapter-wide Announcements group. Read-only, and only for identity
mapping: it is the one place every brother's GroupMe account is visible."""


@dataclass(frozen=True, slots=True)
class GroupMeGroup:
    id: int
    slug: str
    groupme_id: str
    parent_slug: str | None
    weekday: int | None
    label: str

    @property
    def is_topic(self) -> bool:
        return self.parent_slug is not None


def _row(r: sqlite3.Row) -> GroupMeGroup:
    return GroupMeGroup(
        id=r["id"],
        slug=r["slug"],
        groupme_id=r["groupme_id"],
        parent_slug=r["parent_slug"],
        weekday=r["weekday"],
        label=r["label"],
    )


def upsert(
    conn: sqlite3.Connection,
    *,
    slug: str,
    groupme_id: str,
    label: str,
    parent_slug: str | None = None,
    weekday: int | None = None,
) -> int:
    """Register (or re-point) one group. Keyed on ``slug``.

    Re-pointing is the normal case, not an edge case: a chapter that rebuilds
    its Risk group in January keeps the same slugs and gets new ids, and making
    that an UPDATE rather than a second row means nothing downstream has to
    decide which of two ``risk-friday`` rows is the live one.
    """
    if not slug.strip():
        raise ValueError("a group needs a slug")
    if not groupme_id.strip():
        raise ValueError(f"group {slug!r} needs a GroupMe id")
    if not label.strip():
        raise ValueError(f"group {slug!r} needs a label")
    if weekday is not None and not 1 <= weekday <= 7:
        raise ValueError(f"weekday must be 1 (Mon) through 7 (Sun), got {weekday!r}")
    conn.execute(
        """
        INSERT INTO groupme_groups (slug, groupme_id, parent_slug, weekday, label)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(slug) DO UPDATE SET
          groupme_id = excluded.groupme_id,
          parent_slug = excluded.parent_slug,
          weekday = excluded.weekday,
          label = excluded.label
        """,
        (slug.strip(), groupme_id.strip(), parent_slug, weekday, label.strip()),
    )
    row = conn.execute("SELECT id FROM groupme_groups WHERE slug = ?", (slug.strip(),)).fetchone()
    assert row is not None
    return int(row["id"])


def get_by_slug(conn: sqlite3.Connection, slug: str) -> GroupMeGroup | None:
    row = conn.execute("SELECT * FROM groupme_groups WHERE slug = ?", (slug,)).fetchone()
    return _row(row) if row else None


def list_all(conn: sqlite3.Connection) -> list[GroupMeGroup]:
    rows = conn.execute("SELECT * FROM groupme_groups ORDER BY slug").fetchall()
    return [_row(r) for r in rows]


def list_topics(conn: sqlite3.Connection, *, parent_slug: str = PARENT_SLUG) -> list[GroupMeGroup]:
    """Day topics under a parent, weekday-ordered (NULL weekdays last)."""
    rows = conn.execute(
        """
        SELECT * FROM groupme_groups
        WHERE parent_slug = ?
        ORDER BY (weekday IS NULL), weekday, slug
        """,
        (parent_slug,),
    ).fetchall()
    return [_row(r) for r in rows]


def get_topic_for_weekday(
    conn: sqlite3.Connection, *, weekday: int, parent_slug: str = PARENT_SLUG
) -> GroupMeGroup | None:
    """The topic a given weekday's party posts into, or ``None`` if there isn't one.

    ``None`` is a real answer and callers must report it rather than fall back to
    the parent group. A Monday dage announced into the parent chat reaches
    everybody in the chapter's Risk group including the twelve people who are not
    working it, which is precisely the noise the topics exist to stop.
    """
    row = conn.execute(
        "SELECT * FROM groupme_groups WHERE parent_slug = ? AND weekday = ?",
        (parent_slug, weekday),
    ).fetchone()
    return _row(row) if row else None


def delete(conn: sqlite3.Connection, *, slug: str) -> int:
    cur = conn.execute("DELETE FROM groupme_groups WHERE slug = ?", (slug,))
    return cur.rowcount
