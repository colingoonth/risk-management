"""Per-entity repository for ``groupme_identities`` — roster member ↔ GroupMe account.

One row per member, one row per GroupMe account, both enforced by the schema.
Everything about this table is a refusal to guess: see the migration header.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

CONFIDENCES: frozenset[str] = frozenset({"exact", "alias", "confirmed"})
"""How a link was made.

``exact``     the GroupMe nickname equalled the roster display name
``alias``     it equalled a registered ``member_aliases`` row
``confirmed`` a human said so

Validated here as well as by the mapper because the value can arrive off an HTTP
body or a CLI flag, and a ValueError naming the three allowed words is a better
answer than a row that no consumer knows how to read. There is deliberately no
``fuzzy`` tier — a similarity score is confident and wrong exactly when two
brothers have similar names, which is the only case where it would be used.
"""


@dataclass(frozen=True, slots=True)
class GroupMeIdentity:
    member_id: int
    groupme_user_id: str
    nickname: str | None
    confidence: str
    linked_at: str


@dataclass(frozen=True, slots=True)
class LinkedMember:
    """An identity joined to the roster row it belongs to."""

    member_id: int
    display_name: str
    member_slug: str
    groupme_user_id: str
    nickname: str | None
    confidence: str
    linked_at: str


def _row(r: sqlite3.Row) -> GroupMeIdentity:
    return GroupMeIdentity(
        member_id=r["member_id"],
        groupme_user_id=r["groupme_user_id"],
        nickname=r["nickname"],
        confidence=r["confidence"],
        linked_at=r["linked_at"],
    )


def _linked_row(r: sqlite3.Row) -> LinkedMember:
    return LinkedMember(
        member_id=r["member_id"],
        display_name=r["display_name"],
        member_slug=r["member_slug"],
        groupme_user_id=r["groupme_user_id"],
        nickname=r["nickname"],
        confidence=r["confidence"],
        linked_at=r["linked_at"],
    )


def link(
    conn: sqlite3.Connection,
    *,
    member_id: int,
    groupme_user_id: str,
    nickname: str | None,
    confidence: str,
    linked_at: str,
) -> None:
    """Link a member to a GroupMe account, replacing any previous link for him.

    Deliberately NOT an upsert on ``groupme_user_id``: re-pointing a member at a
    different account is a correction and is fine, but silently moving an
    ACCOUNT from one member to another would rewrite history for two people at
    once. That path raises ``IntegrityError`` off the unique index and the caller
    has to unlink first, which is a decision somebody makes on purpose.
    """
    if confidence not in CONFIDENCES:
        raise ValueError(f"confidence must be one of {sorted(CONFIDENCES)}, got {confidence!r}")
    if not groupme_user_id.strip():
        raise ValueError("a link needs a GroupMe user id")
    conn.execute(
        """
        INSERT INTO groupme_identities
          (member_id, groupme_user_id, nickname, confidence, linked_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(member_id) DO UPDATE SET
          groupme_user_id = excluded.groupme_user_id,
          nickname = excluded.nickname,
          confidence = excluded.confidence,
          linked_at = excluded.linked_at
        """,
        (member_id, groupme_user_id.strip(), nickname, confidence, linked_at),
    )


def update_nickname(conn: sqlite3.Connection, *, member_id: int, nickname: str | None) -> int:
    """Accept a rename without touching ``confidence`` or ``linked_at``.

    A brother renaming himself in GroupMe is not new evidence about who he is,
    so the link's provenance stays exactly as it was.
    """
    cur = conn.execute(
        "UPDATE groupme_identities SET nickname = ? WHERE member_id = ?",
        (nickname, member_id),
    )
    return cur.rowcount


def get_for_member(conn: sqlite3.Connection, member_id: int) -> GroupMeIdentity | None:
    row = conn.execute(
        "SELECT * FROM groupme_identities WHERE member_id = ?", (member_id,)
    ).fetchone()
    return _row(row) if row else None


def get_by_user_id(conn: sqlite3.Connection, groupme_user_id: str) -> GroupMeIdentity | None:
    row = conn.execute(
        "SELECT * FROM groupme_identities WHERE groupme_user_id = ?", (groupme_user_id,)
    ).fetchone()
    return _row(row) if row else None


def list_all(conn: sqlite3.Connection) -> list[GroupMeIdentity]:
    rows = conn.execute("SELECT * FROM groupme_identities ORDER BY member_id").fetchall()
    return [_row(r) for r in rows]


_SELECT_LINKED = """
SELECT
  gi.member_id, gi.groupme_user_id, gi.nickname, gi.confidence, gi.linked_at,
  m.display_name, m.slug AS member_slug
FROM groupme_identities gi
JOIN members m ON m.id = gi.member_id
"""


def list_linked(conn: sqlite3.Connection) -> list[LinkedMember]:
    rows = conn.execute(f"{_SELECT_LINKED} ORDER BY m.display_name").fetchall()
    return [_linked_row(r) for r in rows]


def list_unlinked_members(conn: sqlite3.Connection, *, status_slug: str = "active") -> list[
    tuple[int, str]
]:
    """``(member_id, display_name)`` for roster members with no GroupMe link.

    Restricted to one status because that is the question being asked — an
    alumnus with no GroupMe link is not a gap, and listing him as one buries the
    active brothers who genuinely need mapping.
    """
    rows = conn.execute(
        """
        SELECT m.id, m.display_name
        FROM members m
        JOIN member_statuses ms ON ms.id = m.status_id
        LEFT JOIN groupme_identities gi ON gi.member_id = m.id
        WHERE gi.member_id IS NULL AND ms.slug = ?
        ORDER BY m.display_name
        """,
        (status_slug,),
    ).fetchall()
    return [(int(r["id"]), str(r["display_name"])) for r in rows]


def unlink(conn: sqlite3.Connection, *, member_id: int) -> int:
    cur = conn.execute("DELETE FROM groupme_identities WHERE member_id = ?", (member_id,))
    return cur.rowcount
