"""Per-entity repository for ``groupme_inbound`` — messages read out of GroupMe.

Two invariants live here and nowhere else:

**A message lands exactly once.** :func:`insert_if_new` is an ``ON CONFLICT DO
NOTHING`` against the ``groupme_message_id`` unique index, and it reports which
of the two things happened by returning the new row id or ``None``. The poller
calls it for every message in every page it fetches, including pages it has
already seen after a restart, and the count of rows in this table does not move.

**"Not yet forwarded" is a NULL, not a flag.** ``forwarded_at IS NULL`` is the
delivery queue that :mod:`risk.services.groupme_forward` drains, and
:func:`mark_forwarded` is guarded on that NULL so a double-run cannot restamp a
message that was already appended to the feed.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

TRIAGE_STATES: frozenset[str] = frozenset({"urgent", "noted", "handled"})
"""What the chair has done about a message.

NULL is the fourth state and the default: untouched. It is not in this set
because "no value" is not a value — every caller that clears triage passes
``None`` explicitly.
"""


@dataclass(frozen=True, slots=True)
class InboundMessage:
    id: int
    groupme_message_id: str
    group_slug: str
    sender_user_id: str | None
    sender_name: str
    text: str
    created_at: str
    received_at: str
    forwarded_at: str | None
    triage: str | None
    triage_note: str | None

    @property
    def is_forwarded(self) -> bool:
        return self.forwarded_at is not None


def _row(r: sqlite3.Row) -> InboundMessage:
    return InboundMessage(
        id=r["id"],
        groupme_message_id=r["groupme_message_id"],
        group_slug=r["group_slug"],
        sender_user_id=r["sender_user_id"],
        sender_name=r["sender_name"],
        text=r["text"],
        created_at=r["created_at"],
        received_at=r["received_at"],
        forwarded_at=r["forwarded_at"],
        triage=r["triage"],
        triage_note=r["triage_note"],
    )


def insert_if_new(
    conn: sqlite3.Connection,
    *,
    groupme_message_id: str,
    group_slug: str,
    sender_name: str,
    text: str,
    created_at: str,
    received_at: str,
    sender_user_id: str | None = None,
) -> int | None:
    """Store a message, or do nothing if we already have it.

    Returns the new row id, or ``None`` when the message was already present.

    ``ON CONFLICT DO NOTHING`` rather than a SELECT-then-INSERT: the poller and
    a hand-run ``risk-forwarder`` can be inside the same 120-second window, and
    a check-then-act would let both of them past the check. The unique index
    decides, once, in the database.

    ``rowcount`` is what distinguishes the two outcomes. ``lastrowid`` is not —
    on a suppressed conflict SQLite leaves it holding whatever the connection
    inserted last, which for a page of messages is the PREVIOUS message and
    would read as a successful insert.
    """
    if not groupme_message_id:
        raise ValueError("a message needs a groupme_message_id")
    if not group_slug:
        raise ValueError("a message needs a group_slug")
    cur = conn.execute(
        """
        INSERT INTO groupme_inbound (
            groupme_message_id, group_slug, sender_user_id, sender_name,
            text, created_at, received_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(groupme_message_id) DO NOTHING
        """,
        (
            groupme_message_id,
            group_slug,
            sender_user_id,
            sender_name,
            text,
            created_at,
            received_at,
        ),
    )
    if cur.rowcount != 1:
        return None
    assert cur.lastrowid is not None
    return cur.lastrowid


def get_by_id(conn: sqlite3.Connection, message_id: int) -> InboundMessage | None:
    row = conn.execute("SELECT * FROM groupme_inbound WHERE id = ?", (message_id,)).fetchone()
    return _row(row) if row else None


def get_by_groupme_id(
    conn: sqlite3.Connection, groupme_message_id: str
) -> InboundMessage | None:
    row = conn.execute(
        "SELECT * FROM groupme_inbound WHERE groupme_message_id = ?",
        (groupme_message_id,),
    ).fetchone()
    return _row(row) if row else None


def list_recent(
    conn: sqlite3.Connection,
    *,
    limit: int = 50,
    triage: str | None = None,
    group_slug: str | None = None,
) -> list[InboundMessage]:
    """Newest first — this feeds a screen, and the newest message is the one
    somebody is asking about.

    ``triage`` filters to one state; there is deliberately no way to ask for
    "untouched" here, because the untouched ones are the majority and are what
    an unfiltered call already puts at the top.
    """
    if limit <= 0:
        raise ValueError("limit must be positive")
    clauses: list[str] = []
    params: list[object] = []
    if triage is not None:
        if triage not in TRIAGE_STATES:
            raise ValueError(f"triage must be one of {sorted(TRIAGE_STATES)}, got {triage!r}")
        clauses.append("triage = ?")
        params.append(triage)
    if group_slug is not None:
        clauses.append("group_slug = ?")
        params.append(group_slug)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT * FROM groupme_inbound
        {where}
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [_row(r) for r in rows]


def list_unforwarded(conn: sqlite3.Connection, *, limit: int = 25) -> list[InboundMessage]:
    """The delivery queue, OLDEST first.

    Opposite order to :func:`list_recent` on purpose. That one feeds a screen
    where the newest line belongs at the top; this one feeds a file that is
    appended to, where the newest line belongs LAST. A backlog replayed
    newest-first would read as a conversation running backwards.
    """
    if limit <= 0:
        raise ValueError("limit must be positive")
    rows = conn.execute(
        """
        SELECT * FROM groupme_inbound
        WHERE forwarded_at IS NULL
        ORDER BY created_at ASC, id ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [_row(r) for r in rows]


def count_unforwarded(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM groupme_inbound WHERE forwarded_at IS NULL"
    ).fetchone()
    return int(row["n"])


def mark_forwarded(conn: sqlite3.Connection, *, message_id: int, forwarded_at: str) -> int:
    """Stamp a message as delivered to the feed.

    Guarded on ``forwarded_at IS NULL`` so re-running the forwarder is a no-op
    returning 0 rather than rewriting when a message was first seen.
    """
    cur = conn.execute(
        """
        UPDATE groupme_inbound
        SET forwarded_at = ?
        WHERE id = ? AND forwarded_at IS NULL
        """,
        (forwarded_at, message_id),
    )
    return cur.rowcount


def set_triage(
    conn: sqlite3.Connection,
    *,
    message_id: int,
    triage: str | None,
    triage_note: str | None = None,
) -> int:
    """Record what the chair did about a message. ``None`` puts it back to untouched."""
    if triage is not None and triage not in TRIAGE_STATES:
        raise ValueError(f"triage must be one of {sorted(TRIAGE_STATES)}, got {triage!r}")
    if triage is None and triage_note is not None:
        raise ValueError("a triage note needs a triage state")
    cur = conn.execute(
        "UPDATE groupme_inbound SET triage = ?, triage_note = ? WHERE id = ?",
        (triage, triage_note, message_id),
    )
    return cur.rowcount
