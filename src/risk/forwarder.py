"""``risk-forwarder`` — one GroupMe poll-and-forward cycle.

Run by a LaunchAgent (``com.colin.riskforwarder``) every 120 seconds, and by
hand when Colin wants the backlog now. See
``packaging/launchd/com.colin.riskforwarder.plist.template``.

ONE CYCLE PER INVOCATION, no internal loop. launchd's ``StartInterval`` already
owns the cadence, and a resident daemon would have to reimplement everything it
gives for free — restart on crash, not running while the machine is asleep, and
a process that goes away cleanly on logout. It also means the failure mode of a
bad cycle is one bad cycle: the process exits, and the next one starts from
stored state 120 seconds later.

EXIT CODES. 0 whenever the cycle ran, INCLUDING when individual topics failed,
when the feed file was unwritable, and when another cycle held the lease — those are
recorded in the database and reported on stdout, and they are not conditions
launchd can do anything about. 1 only when the cycle could not run at all (the
database would not open, or the GroupMe client is not installed), which is the
one case where a human needs to look at the log.

WHAT IT PRINTS. Timestamps, topic slugs, error CODES and counts. Never message
text, never a sender, never a group id, never a URL — this line goes to a log
file that lives beside a public repo, and everything upstream has already been
reduced to a code for the same reason.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from risk.db.connection import close_conn, connect, resolve_db_path
from risk.db.schema import ensure_schema
from risk.services import groupme_forward, groupme_poll


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="risk-forwarder",
        description="Poll the chapter's GroupMe risk topics and append new messages to a feed file.",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="database to use (default: RISK_DB_PATH, else the app's standard location)",
    )
    parser.add_argument(
        "--feed",
        type=Path,
        default=None,
        help=f"file to append forwarded messages to (default: ${groupme_forward.FEED_ENV})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=groupme_forward.DEFAULT_FORWARD_LIMIT,
        help="most messages to append to the feed in one cycle (default: %(default)s)",
    )
    parser.add_argument(
        "--no-forward",
        action="store_true",
        help="read GroupMe and store, but do not append anything to the feed",
    )
    parser.add_argument(
        "--forward-only",
        action="store_true",
        help="do not read GroupMe; just drain the queue (use when the feed is writable again)",
    )
    parser.add_argument("--json", action="store_true", help="emit the summary as one JSON line")
    parser.add_argument(
        "--verbose", action="store_true", help="log at DEBUG instead of WARNING"
    )
    return parser


def _summary(result: groupme_poll.PollCycleResult) -> str:
    if result.skipped_locked:
        return f"{result.polled_at} skipped=lease_held"
    stored = sum(g.stored for g in result.groups)
    dupes = sum(g.duplicates for g in result.groups)
    failed = [f"{g.group_slug}:{g.error_code}" for g in result.groups if not g.ok]
    backoff = [g.group_slug for g in result.groups if g.skipped]
    parts = [
        result.polled_at,
        f"topics={len(result.groups)}",
        f"stored={stored}",
        f"dup={dupes}",
        f"forwarded={result.forwarded}",
    ]
    if failed:
        parts.append(f"failed={','.join(failed)}")
    if backoff:
        parts.append(f"backoff={','.join(backoff)}")
    if result.forward_error:
        parts.append(f"feed={result.forward_error}")
    if result.lease_lost:
        parts.append("lease=lost")
    return " ".join(parts)


def _payload(result: groupme_poll.PollCycleResult) -> dict[str, object]:
    return {
        "polled_at": result.polled_at,
        "forwarded": result.forwarded,
        "forward_error": result.forward_error,
        "skipped_locked": result.skipped_locked,
        "lease_lost": result.lease_lost,
        "groups": [
            {
                "group_slug": g.group_slug,
                "fetched": g.fetched,
                "stored": g.stored,
                "duplicates": g.duplicates,
                "pages": g.pages,
                "ok": g.ok,
                "skipped": g.skipped,
                "error_code": g.error_code,
            }
            for g in result.groups
        ],
    }


def _drain_only(
    conn: sqlite3.Connection, args: argparse.Namespace, now: datetime
) -> groupme_poll.PollCycleResult:
    """``--forward-only``: skip GroupMe entirely and append what is already stored.

    Still stamps the heartbeat. The process ran and did its job; a health
    endpoint reporting it as dead because this particular cycle chose not to
    call out to the network would be wrong.
    """
    outcome = groupme_forward.forward_pending(
        conn, feed=args.feed, limit=args.limit, now=now
    )
    groupme_poll.write_heartbeat(conn, now=now)
    return groupme_poll.PollCycleResult(
        polled_at=groupme_poll.stamp(now),
        groups=(),
        forwarded=len(outcome.sent),
        forward_error=outcome.error_code,
    )


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    db_path = resolve_db_path(args.db_path)
    now = datetime.now(UTC)
    try:
        conn = connect(db_path)
        ensure_schema(conn)
    except sqlite3.Error as exc:
        print(f"risk-forwarder: cannot open {db_path}: {exc}", file=sys.stderr)
        return 1

    try:
        if args.forward_only:
            result = _drain_only(conn, args, now)
        else:
            result = groupme_poll.poll_once(
                conn,
                now=now,
                forward=not args.no_forward,
                feed=args.feed,
                forward_limit=args.limit,
            )
    except ImportError:
        # The GroupMe HTTP client is not present in this build. Nothing to poll
        # this cycle; --forward-only still drains the queue. Named, not traced:
        # an ImportError's text carries module paths.
        print("risk-forwarder: client_unavailable", file=sys.stderr)
        return 1
    finally:
        close_conn(conn)

    print(json.dumps(_payload(result)) if args.json else _summary(result))
    return 0


if __name__ == "__main__":  # pragma: no cover — exercised via the console script
    raise SystemExit(main())
