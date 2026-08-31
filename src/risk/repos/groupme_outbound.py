"""Per-entity repository for ``groupme_outbound`` — the send-once ledger.

Read the migration header for why this table exists. The short version: a
deterministic ``source_guid`` only buys about a minute of de-duplication from
GroupMe, and the failures that matter (a crash, a LaunchAgent restart, a double
click, two processes racing) are all longer than a minute.

The one rule this module enforces is that a row is RESERVED BEFORE the HTTP
call. Every function here is a small transaction-safe step of that protocol;
none of them make network calls.
"""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from dataclasses import dataclass

STATES: frozenset[str] = frozenset({"pending", "sent", "unknown", "failed"})

_GUID_NAMESPACE = uuid.UUID("6f2d0f14-1e2b-4f6a-9a71-3f2c9a4c7c11")
"""Fixed namespace for deterministic source guids. Arbitrary but constant — the
value only has to never change, because changing it would make every previously
sent announcement look unsent."""


@dataclass(frozen=True, slots=True)
class OutboundRow:
    id: int
    event_id: int
    destination_slug: str
    content_version: str
    source_guid: str
    state: str
    attempts: int
    reserved_at: str
    settled_at: str | None
    message_id: str | None
    last_error: str | None

    @property
    def is_settled(self) -> bool:
        return self.state in ("sent", "failed")


def _row(r: sqlite3.Row) -> OutboundRow:
    return OutboundRow(
        id=r["id"],
        event_id=r["event_id"],
        destination_slug=r["destination_slug"],
        content_version=r["content_version"],
        source_guid=r["source_guid"],
        state=r["state"],
        attempts=r["attempts"],
        reserved_at=r["reserved_at"],
        settled_at=r["settled_at"],
        message_id=r["message_id"],
        last_error=r["last_error"],
    )


def content_version(text: str, mentions: tuple[tuple[str, int, int], ...]) -> str:
    """Hash the thing that will actually be sent.

    Text AND mentions, because two messages can read identically and tag
    different people — a re-linked identity changes who is pinged without
    changing a character of the body, and that is a new announcement, not a
    retry of the old one.

    Field-separated with a character that cannot occur in the parts, so
    ``("ab", "c")`` and ``("a", "bc")`` cannot collide into one version.
    """
    parts = [text, *(f"{uid}\x1f{off}\x1f{length}" for uid, off, length in mentions)]
    joined = "\x1e".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def source_guid_for(*, event_id: int, destination_slug: str, version: str) -> str:
    """Deterministic guid for one (event, destination, content) triple.

    Derived rather than random so that a row re-reserved after a ``failed``
    attempt carries the SAME guid — GroupMe's own minute-long de-duplication is
    then a second line of defence behind the ledger instead of being defeated by
    a fresh guid on every try.
    """
    return str(uuid.uuid5(_GUID_NAMESPACE, f"{event_id}\x1f{destination_slug}\x1f{version}"))


def get(
    conn: sqlite3.Connection, *, event_id: int, destination_slug: str, content_version: str
) -> OutboundRow | None:
    row = conn.execute(
        """
        SELECT * FROM groupme_outbound
        WHERE event_id = ? AND destination_slug = ? AND content_version = ?
        """,
        (event_id, destination_slug, content_version),
    ).fetchone()
    return _row(row) if row else None


def get_by_guid(conn: sqlite3.Connection, source_guid: str) -> OutboundRow | None:
    row = conn.execute(
        "SELECT * FROM groupme_outbound WHERE source_guid = ?", (source_guid,)
    ).fetchone()
    return _row(row) if row else None


def reserve(
    conn: sqlite3.Connection,
    *,
    event_id: int,
    destination_slug: str,
    content_version: str,
    reserved_at: str,
) -> OutboundRow:
    """Claim the right to send this exact message. Caller supplies the transaction.

    Inserts a ``pending`` row, or bumps an existing ``failed`` one back to
    ``pending`` and increments its attempt count. Any other existing state is
    returned untouched and the CALLER decides — this function never decides on
    its own that something is safe to re-send.
    """
    guid = source_guid_for(
        event_id=event_id, destination_slug=destination_slug, version=content_version
    )
    conn.execute(
        """
        INSERT INTO groupme_outbound
          (event_id, destination_slug, content_version, source_guid, state,
           attempts, reserved_at)
        VALUES (?, ?, ?, ?, 'pending', 1, ?)
        ON CONFLICT(event_id, destination_slug, content_version) DO UPDATE SET
          state = 'pending',
          attempts = groupme_outbound.attempts + 1,
          reserved_at = excluded.reserved_at,
          last_error = NULL
        WHERE groupme_outbound.state = 'failed'
        """,
        (event_id, destination_slug, content_version, guid, reserved_at),
    )
    existing = get(
        conn,
        event_id=event_id,
        destination_slug=destination_slug,
        content_version=content_version,
    )
    assert existing is not None
    return existing


def mark_sent(
    conn: sqlite3.Connection, *, row_id: int, message_id: str | None, settled_at: str
) -> int:
    cur = conn.execute(
        """
        UPDATE groupme_outbound
        SET state = 'sent', message_id = ?, settled_at = ?, last_error = NULL
        WHERE id = ?
        """,
        (message_id, settled_at, row_id),
    )
    return cur.rowcount


def mark_failed(conn: sqlite3.Connection, *, row_id: int, error: str, settled_at: str) -> int:
    """Definitely not delivered. Retryable — ``reserve`` will pick it back up."""
    cur = conn.execute(
        "UPDATE groupme_outbound SET state = 'failed', last_error = ?, settled_at = ? WHERE id = ?",
        (error, settled_at, row_id),
    )
    return cur.rowcount


def mark_unknown(conn: sqlite3.Connection, *, row_id: int, error: str) -> int:
    """Delivery is not knowable from here.

    ``settled_at`` stays NULL: this row is not settled, and nothing may retry it
    until a human (or ``reconcile``, reading the chat back) establishes which
    way it went.
    """
    cur = conn.execute(
        "UPDATE groupme_outbound SET state = 'unknown', last_error = ? WHERE id = ?",
        (error, row_id),
    )
    return cur.rowcount


def list_unsettled(conn: sqlite3.Connection) -> list[OutboundRow]:
    """Everything awaiting reconciliation: ``unknown`` plus stale ``pending``.

    A ``pending`` row that outlived its process is indistinguishable from an
    ``unknown`` one — the request may have left. Both land here and neither is
    retried automatically.
    """
    rows = conn.execute(
        """
        SELECT * FROM groupme_outbound
        WHERE state IN ('pending', 'unknown')
        ORDER BY reserved_at, id
        """
    ).fetchall()
    return [_row(r) for r in rows]


def list_for_event(conn: sqlite3.Connection, event_id: int) -> list[OutboundRow]:
    rows = conn.execute(
        "SELECT * FROM groupme_outbound WHERE event_id = ? ORDER BY reserved_at, id",
        (event_id,),
    ).fetchall()
    return [_row(r) for r in rows]
