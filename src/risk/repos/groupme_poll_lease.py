"""Per-entity repository for ``groupme_poll_lease`` — one poller at a time.

Three things can start a poll cycle: the LaunchAgent every 120 seconds, Colin
running ``risk-forwarder`` by hand, and the dashboard's force-a-cycle button.
They can overlap, and the thing they would overlap on is a single read cursor
per topic. Two pollers reading the same page is harmless — the unique index on
``groupme_message_id`` absorbs it — but a slow cycle finishing after a fast one
and writing back its OLDER cursor would replay hours of messages into the
feed, and a slow cycle that has been superseded has no business writing a
cursor at all.

So: a single-row lease with an expiry, taken before the cycle and renewed at
every page boundary.

**Why the writes are safe.** Every function here is called inside
``transaction(conn)``, which is ``BEGIN IMMEDIATE`` — SQLite serialises writers,
so the compare-and-set in :func:`acquire` cannot interleave with another
process's, and the check in :func:`held_by` is in the same transaction as the
cursor write it guards. There is no separate lock to keep in sync with the data.

**Why it expires rather than being released.** A crashed or SIGKILLed poller
never gets to run its cleanup, and a lease that outlived its holder forever
would stop the forwarder permanently — a silent, total failure, which is the
worst kind here. The TTL is the recovery path; :func:`release` is only an
optimisation for the normal case.

**``holder`` carries no identity.** It is a pid plus a nonce. This row is read
by a health surface and can end up in a log file inside a public repo's working
tree, so it says nothing about the user, the host, or where anything lives.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
from dataclasses import dataclass

LEASE_ROW_ID = 1
"""There is exactly one lease. The CHECK in the schema enforces it; this names it."""


@dataclass(frozen=True, slots=True)
class Lease:
    holder: str
    acquired_at: str
    expires_at: str


def new_holder_token() -> str:
    """An opaque per-process token: pid for a human reading a log, nonce for
    uniqueness after pid reuse."""
    return f"{os.getpid()}-{secrets.token_hex(4)}"


def _row(r: sqlite3.Row) -> Lease:
    return Lease(holder=r["holder"], acquired_at=r["acquired_at"], expires_at=r["expires_at"])


def get(conn: sqlite3.Connection) -> Lease | None:
    row = conn.execute(
        "SELECT * FROM groupme_poll_lease WHERE id = ?", (LEASE_ROW_ID,)
    ).fetchone()
    return _row(row) if row else None


def acquire(conn: sqlite3.Connection, *, holder: str, now: str, expires_at: str) -> bool:
    """Take the lease if it is free, expired, or already ours.

    Re-taking our own lease is allowed on purpose: a cycle that is retried
    in-process should not deadlock against itself, and the holder token is
    unique per process so "already ours" cannot be somebody else's.

    Returns False when another live holder has it — which is not an error. It
    means the previous cycle is still running, and the right response is to do
    nothing and let it finish.
    """
    cur = conn.execute(
        """
        INSERT INTO groupme_poll_lease (id, holder, acquired_at, expires_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            holder = excluded.holder,
            acquired_at = excluded.acquired_at,
            expires_at = excluded.expires_at
        WHERE groupme_poll_lease.holder = excluded.holder
           OR groupme_poll_lease.expires_at <= excluded.acquired_at
        """,
        (LEASE_ROW_ID, holder, now, expires_at),
    )
    return cur.rowcount == 1


def renew(conn: sqlite3.Connection, *, holder: str, now: str, expires_at: str) -> bool:
    """Push our own expiry out. False means we no longer hold it.

    A catch-up spanning a night of messages can outlast any TTL short enough to
    be a useful crash-recovery window, so the lease is extended as work is done
    rather than sized for the worst case up front.
    """
    cur = conn.execute(
        """
        UPDATE groupme_poll_lease
        SET expires_at = ?
        WHERE id = ? AND holder = ? AND expires_at > ?
        """,
        (expires_at, LEASE_ROW_ID, holder, now),
    )
    return cur.rowcount == 1


def held_by(conn: sqlite3.Connection, *, holder: str, now: str) -> bool:
    """Do we still hold an unexpired lease?

    Called inside the same transaction as every cursor write. A holder whose
    lease went stale mid-cycle must stop writing: another process has taken
    over, and this one's idea of where the cursor belongs is out of date.
    """
    row = conn.execute(
        "SELECT holder, expires_at FROM groupme_poll_lease WHERE id = ?", (LEASE_ROW_ID,)
    ).fetchone()
    return bool(row) and row["holder"] == holder and row["expires_at"] > now


def release(conn: sqlite3.Connection, *, holder: str) -> int:
    """Give the lease up early. Guarded on the holder so a process that already
    lost it cannot delete its successor's."""
    cur = conn.execute(
        "DELETE FROM groupme_poll_lease WHERE id = ? AND holder = ?", (LEASE_ROW_ID, holder)
    )
    return cur.rowcount
