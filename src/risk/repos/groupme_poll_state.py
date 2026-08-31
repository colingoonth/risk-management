"""Per-entity repository for ``groupme_poll_state`` — one read cursor per topic.

The row is small and does three jobs that are easy to conflate:

**``last_message_id`` is a cursor, not a status.** It is the id of the newest
message we have actually STORED for that topic, and it is what the next fetch
walks forward from with ``after_id``. It advances only on the page that was
written, in the same transaction as those rows — so a process killed halfway
through an eight-hour catch-up resumes at the last message it durably has,
never past it. Nothing here can set it back to NULL: a poller that has lost its
place and starts over from nothing either replays a night into the feed or
skips it, and both are worse than staying put.

**``last_polled_at`` / ``last_ok_at`` / ``consecutive_failures`` are health.**
Polled and succeeded are different questions: a topic polled every 120 seconds
for an hour with a dead network has a fresh ``last_polled_at`` and an hour-old
``last_ok_at``, and it is the second one that means the chair is not seeing his
messages.

**``last_error`` is a CODE and ``retry_after`` is an instant.** The code comes
from the fixed vocabulary in :mod:`risk.services.groupme_poll`; this repo will
not store anything longer than one, because the alternative — an exception
string — carries the URL it was calling, and that URL contains a real group id
in a public repo. ``retry_after`` is when the topic may be asked again, already
resolved to a wall-clock instant.

Every writer here upserts, so the poller never has to ask whether a topic has
been seen before — the first poll of a brand-new topic writes its row on the
way past. :func:`ensure` exists for the other direction: seeding a topic at
configuration time so it appears in health as "never polled" rather than not
appearing at all.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
"""What ``last_error`` is allowed to contain.

Lowercase, underscore-separated, at most 40 characters — long enough for
``groupme_client_unavailable`` and far too short to hide a URL, a response body
or somebody's message text in. Enforced here, at the only door into the column,
because every caller upstream is one refactor away from passing ``str(exc)``.
"""


@dataclass(frozen=True, slots=True)
class PollState:
    group_slug: str
    last_message_id: str | None
    last_polled_at: str | None
    last_ok_at: str | None
    consecutive_failures: int
    last_error: str | None
    retry_after: str | None


def _row(r: sqlite3.Row) -> PollState:
    return PollState(
        group_slug=r["group_slug"],
        last_message_id=r["last_message_id"],
        last_polled_at=r["last_polled_at"],
        last_ok_at=r["last_ok_at"],
        consecutive_failures=r["consecutive_failures"],
        last_error=r["last_error"],
        retry_after=r["retry_after"],
    )


def get(conn: sqlite3.Connection, group_slug: str) -> PollState | None:
    row = conn.execute(
        "SELECT * FROM groupme_poll_state WHERE group_slug = ?", (group_slug,)
    ).fetchone()
    return _row(row) if row else None


def list_all(conn: sqlite3.Connection) -> list[PollState]:
    rows = conn.execute("SELECT * FROM groupme_poll_state ORDER BY group_slug").fetchall()
    return [_row(r) for r in rows]


def ensure(conn: sqlite3.Connection, group_slug: str) -> None:
    """Give a topic a row with no cursor and no history. Idempotent.

    Called when a topic is configured so it shows up in health as "never
    polled" rather than not showing up at all — a topic missing from the health
    list reads as "not configured", which is the wrong diagnosis.
    """
    conn.execute(
        """
        INSERT INTO groupme_poll_state (group_slug) VALUES (?)
        ON CONFLICT(group_slug) DO NOTHING
        """,
        (group_slug,),
    )


def advance_cursor(conn: sqlite3.Connection, *, group_slug: str, last_message_id: str) -> None:
    """Move the read cursor forward.

    Called once per fetched page, inside the same transaction as that page's
    rows. The write is what makes an interrupted catch-up resumable rather than
    restartable.

    An empty id is a ValueError rather than a NULL write. Blanking the cursor is
    the one mutation this table must never accept: it does not mean "start
    again", it means "lose our place in the stream".
    """
    if not last_message_id:
        raise ValueError("a cursor needs a message id; it is never cleared")
    conn.execute(
        """
        INSERT INTO groupme_poll_state (group_slug, last_message_id)
        VALUES (?, ?)
        ON CONFLICT(group_slug) DO UPDATE SET last_message_id = excluded.last_message_id
        """,
        (group_slug, last_message_id),
    )


def record_success(conn: sqlite3.Connection, *, group_slug: str, polled_at: str) -> None:
    """The topic answered. Clears the failure history and any back-off.

    Deliberately does NOT touch ``last_message_id``: a successful poll that
    found nothing new must not move the cursor, and the pages that did find
    something already moved it themselves.
    """
    conn.execute(
        """
        INSERT INTO groupme_poll_state (
            group_slug, last_polled_at, last_ok_at, consecutive_failures,
            last_error, retry_after
        )
        VALUES (?, ?, ?, 0, NULL, NULL)
        ON CONFLICT(group_slug) DO UPDATE SET
            last_polled_at = excluded.last_polled_at,
            last_ok_at = excluded.last_ok_at,
            consecutive_failures = 0,
            last_error = NULL,
            retry_after = NULL
        """,
        (group_slug, polled_at, polled_at),
    )


def record_failure(
    conn: sqlite3.Connection,
    *,
    group_slug: str,
    polled_at: str,
    error_code: str,
    retry_after: str | None = None,
) -> None:
    """The topic did not answer.

    ``last_ok_at`` and ``last_message_id`` survive untouched — they are the two
    facts that let the next successful poll pick up exactly where this one
    failed, and overwriting them here would turn a network blip into a gap in
    the record.

    ``error_code`` is validated against the code shape, not against a list of
    known codes: the vocabulary belongs to the service that classifies errors,
    but the guarantee that nothing sensitive reaches this column belongs here.
    """
    if not _CODE_RE.match(error_code):
        raise ValueError(
            f"last_error takes a normalised code (lowercase, underscores, <=40 chars), "
            f"got {error_code[:60]!r}"
        )
    conn.execute(
        """
        INSERT INTO groupme_poll_state (
            group_slug, last_polled_at, consecutive_failures, last_error, retry_after
        )
        VALUES (?, ?, 1, ?, ?)
        ON CONFLICT(group_slug) DO UPDATE SET
            last_polled_at = excluded.last_polled_at,
            consecutive_failures = groupme_poll_state.consecutive_failures + 1,
            last_error = excluded.last_error,
            retry_after = excluded.retry_after
        """,
        (group_slug, polled_at, error_code, retry_after),
    )


def newest_poll_time(conn: sqlite3.Connection) -> str | None:
    """The most recent ``last_polled_at`` across every topic.

    One half of the heartbeat: evidence that a poll cycle ran at all, from
    inside the database, so it survives the heartbeat file being wiped and works
    when the cycle was forced through the API rather than by the LaunchAgent.
    """
    row = conn.execute("SELECT MAX(last_polled_at) AS newest FROM groupme_poll_state").fetchone()
    return row["newest"] if row and row["newest"] else None


def delete(conn: sqlite3.Connection, *, group_slug: str) -> int:
    cur = conn.execute("DELETE FROM groupme_poll_state WHERE group_slug = ?", (group_slug,))
    return cur.rowcount
