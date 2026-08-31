"""Read every configured GroupMe topic, forward from wherever we last stopped.

This is the half of the forwarder that talks to GroupMe, and it is READ-ONLY.
It fetches messages and writes them into the local database. It never posts,
never adds anybody to a group and never removes anybody — structurally, not by
convention: what it holds is a single ``list_messages`` callable, not a client
object and not the client module, so there is no outbound method in scope to
call by accident. Announcing and membership are a different feature behind an
explicit ``confirm``.

THE PROBLEM THIS EXISTS TO SOLVE IS A CLOSED LAPTOP.
------------------------------------------------------------------
A LaunchAgent with a 120-second interval does not run while the machine is
asleep, and launchd does not replay the intervals it missed — it runs the job
once on wake. So the poll at 09:00 is the first since 01:00, and eight hours of
a party are sitting on GroupMe's side. Everything below follows from that:

* **``after_id``, never ``since_id``.** They are not two spellings of the same
  cursor. ``since_id`` answers with the MOST RECENT page after the given
  message, so a night that produced 250 messages returns the last 100 and the
  150 in between are gone — permanently, silently, and only on the occasion
  that mattered. ``after_id`` answers with the messages IMMEDIATELY following
  the cursor, ascending, which is the only one of the two that can be walked.
* **One poll is a LOOP, not a request.** A page holds at most a hundred
  messages; a night holds more. :func:`poll_group` keeps asking, advancing the
  cursor each time, until a page comes back empty.
* **The cursor advances inside the page's own transaction**, after that page's
  rows are written and while the lease is still verified as ours. Kill the
  process mid-catch-up and it resumes at the last page it durably wrote — not
  at the start, and not past the gap.
* **The cursor is never cleared.** Not when the server rejects it, not when a
  page is unusable, not on any error path. A poller that loses its place either
  replays the night into the feed or skips it, and "we could not read past
  message X" is a state worth keeping.
* **Every write is conditional on the message id.** The catch-up re-reads
  boundaries, a hand-run overlaps the LaunchAgent, and the API can force a
  cycle. ``groupme_message_id`` is UNIQUE and
  ``groupme_inbound.insert_if_new`` reports whether the row was new, so all of
  that converges on exactly one row per message.
* **One poller at a time**, via the lease in ``groupme_poll_lease``. Overlapping
  cycles are guaranteed — the LaunchAgent does not check whether the last one
  finished — and the thing they would overlap on is a single cursor per topic.

ERRORS ARE STORED AS CODES. ``last_error`` gets ``network_timeout``, never the
exception's own text: an HTTP client's error string carries the URL it was
calling, that URL contains a real group id, and this column is rendered by a
health endpoint and printed into a log file inside a public repo's working tree.
The same rule governs every log line here — timestamps, slugs, codes and counts,
nothing else. Message text never appears in a log.

WHAT THIS MODULE DOES NOT DO. It does not know how to speak HTTP to GroupMe.
The client lives in ``risk.services.groupme`` and is reached through a
``list_messages(group_id, after_id)`` callable — injected by every test, which
is why the whole catch-up path runs without a network and why the eight-hour
case above is a test rather than a hope, and resolved in production from the
single name in :data:`CLIENT_READ_FUNCTION`. That resolution is itself a seam
worth testing: it was broken for months precisely because injection meant
nothing ever ran it.
"""

from __future__ import annotations

import contextlib
import importlib
import logging
import os
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from risk.db.connection import transaction
from risk.repos import groupme_inbound as inbound_repo
from risk.repos import groupme_poll_lease as lease_repo
from risk.repos import groupme_poll_state as poll_state_repo
from risk.services import groupme_forward

log = logging.getLogger(__name__)

ListMessages = Callable[[str, str | None], Sequence[Mapping[str, Any]]]
"""``(groupme_group_id, after_id) -> the messages immediately AFTER after_id``.

Ascending by id, oldest first, at most one page. ``None`` for ``after_id``
means "start me somewhere sensible" — the bootstrap case, when a topic has
never been polled.

THE SECOND ARGUMENT IS AN ``after_id`` CURSOR, NOT A ``since_id``. A client
that implemented ``since_id`` semantics behind this signature would look
correct on a quiet group and lose everything but the last page of a busy night.
See the module docstring.
"""

MAX_PAGES_PER_POLL = 25
"""Ceiling on the catch-up loop: 25 pages x 100 messages = 2500 per topic per
cycle.

Not a correctness limit — a bound on the damage a misbehaving client can do.
A ``list_messages`` that ignored the cursor and returned the same page forever
would otherwise spin until launchd killed it. Hitting the cap is not an error
and not a data loss: the cursor is where the last page left it and the next
cycle, 120 seconds later, carries on. Two and a half thousand messages is
already several times the busiest night this chapter has had.
"""

STALE_AFTER_SECONDS = 600
"""When a topic stops counting as fresh: five missed 120-second cycles.

Deliberately loose. A laptop under load, a slow network, or a single failed
poll are all normal and none of them mean the forwarder is broken; a threshold
of one or two intervals would cry wolf several times a day and the flag would
stop meaning anything. Five is long enough that only a genuinely stuck or
misconfigured poller trips it, and short enough that a party which started an
hour ago is not still reported as healthy.

Measured against ``last_ok_at``, never ``last_polled_at``: a topic being polled
briskly and failing every time is exactly the case the flag has to catch.
"""

LEASE_TTL_SECONDS = 180
"""How long a poller's claim survives without being renewed.

Longer than a normal cycle (which is a few seconds) so ordinary scheduling
jitter never revokes a live holder, and short enough that a crashed one is
replaced within two LaunchAgent intervals rather than blocking the forwarder
until somebody notices. A long catch-up does not need a longer TTL — it renews
as it goes.
"""

DEFAULT_RATE_LIMIT_BACKOFF_SECONDS = 300
"""Used when the server says 429 but sends no ``Retry-After``."""

MAX_RETRY_AFTER_SECONDS = 3600
"""Cap on an honoured ``Retry-After``.

A malformed or hostile header must not be able to switch the forwarder off for
a week. An hour is longer than any real rate-limit window and short enough that
a mistake costs one evening at worst.
"""

HEARTBEAT_ENV = "RISK_FORWARDER_HEARTBEAT"
HEARTBEAT_SUFFIX = ".forwarder-heartbeat"

ERROR_CODES: frozenset[str] = frozenset(
    {
        "network_timeout",
        "network_unreachable",
        "auth_rejected",
        "rate_limited",
        "not_found",
        "server_error",
        "client_error",
        "invalid_response",
        "client_unavailable",
        "unknown_error",
    }
)
"""The entire vocabulary ``last_error`` may contain.

Closed on purpose. Anything not on this list is reported as ``unknown_error``
rather than passed through, because "pass through what we do not recognise" is
how a response body ends up in a public log.
"""


class LeaseLostError(RuntimeError):
    """Another poller took over mid-cycle. This one stops writing immediately."""


@dataclass(frozen=True, slots=True)
class PollTarget:
    """One topic to read. ``groupme_id`` is real chapter data and lives in the DB."""

    group_slug: str
    groupme_id: str
    label: str


@dataclass(frozen=True, slots=True)
class NormalizedMessage:
    groupme_message_id: str
    sender_user_id: str | None
    sender_name: str
    text: str
    created_at: str


@dataclass(frozen=True, slots=True)
class GroupPollResult:
    group_slug: str
    fetched: int
    stored: int
    duplicates: int
    pages: int
    ok: bool
    error_code: str | None
    last_message_id: str | None
    skipped: bool = False


@dataclass(frozen=True, slots=True)
class PollCycleResult:
    polled_at: str
    groups: tuple[GroupPollResult, ...]
    forwarded: int
    forward_error: str | None
    skipped_locked: bool = False
    lease_lost: bool = False

    @property
    def stored(self) -> int:
        return sum(g.stored for g in self.groups)


@dataclass(frozen=True, slots=True)
class GroupHealth:
    slug: str
    label: str
    last_polled_at: str | None
    last_ok_at: str | None
    consecutive_failures: int
    last_error: str | None
    stale: bool


@dataclass(frozen=True, slots=True)
class Health:
    groups: tuple[GroupHealth, ...]
    heartbeat_age_seconds: int | None
    healthy: bool


# ---------------------------------------------------------------------------
# Time. Every stamp this module writes is UTC, seconds resolution, offset
# included — which makes the strings sort lexicographically, and the lease's
# expiry comparison is done in SQL on exactly that property.
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(UTC)


def stamp(moment: datetime) -> str:
    aware = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat(timespec="seconds")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# Error classification. Nothing from an exception's text ever leaves this
# section — only which of the fixed codes it maps to, and how long the server
# asked us to wait.
# ---------------------------------------------------------------------------


def _status_code(exc: BaseException) -> int | None:
    for holder in (exc, getattr(exc, "response", None)):
        if holder is None:
            continue
        for attr in ("status_code", "status"):
            value = getattr(holder, attr, None)
            if isinstance(value, int) and 100 <= value <= 599:
                return value
    return None


def classify_error(exc: BaseException) -> str:
    """Map any exception onto one of :data:`ERROR_CODES`.

    Deliberately duck-typed rather than keyed off the client's exception
    classes: this module does not import the client, and it must keep working
    when that client's error hierarchy changes. HTTP status wins when there is
    one, because 401 and 429 need different human responses and both can arrive
    as the same generic exception type.
    """
    status = _status_code(exc)
    if status is not None:
        if status in (401, 403):
            return "auth_rejected"
        if status == 404:
            return "not_found"
        if status == 408:
            return "network_timeout"
        if status == 429:
            return "rate_limited"
        if status >= 500:
            return "server_error"
        if status >= 400:
            return "client_error"
    if isinstance(exc, ModuleNotFoundError | ImportError):
        return "client_unavailable"
    if isinstance(exc, TimeoutError):
        return "network_timeout"
    if isinstance(exc, ConnectionError):
        return "network_unreachable"
    if isinstance(exc, ValueError | TypeError | KeyError):
        # json.JSONDecodeError is a ValueError; a payload that is not the shape
        # we expect lands here too.
        return "invalid_response"
    if isinstance(exc, OSError):
        return "network_unreachable"
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return "network_timeout"
    if "connect" in name or "ssl" in name or "dns" in name or "socket" in name:
        return "network_unreachable"
    if "ratelimit" in name.replace("_", "") or "toomany" in name.replace("_", ""):
        return "rate_limited"
    if "auth" in name or "forbidden" in name or "unauthorized" in name:
        return "auth_rejected"
    return "unknown_error"


def retry_after_seconds(exc: BaseException) -> float | None:
    """How long the server asked us to wait, in seconds, or ``None``.

    Reads a ``retry_after`` attribute first (what a well-behaved client
    exposes), then the raw header. Only the numeric form of ``Retry-After`` is
    honoured; the HTTP-date form is rare, and guessing wrong about clock skew is
    worse than falling back to the default back-off. Clamped both ways: never
    negative, never longer than :data:`MAX_RETRY_AFTER_SECONDS`.
    """
    candidates: list[Any] = [getattr(exc, "retry_after", None)]
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is not None:
        with contextlib.suppress(AttributeError, TypeError):  # pragma: no branch
            candidates.append(headers.get("Retry-After"))
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            seconds = float(candidate)
        except (TypeError, ValueError):
            continue
        return max(0.0, min(seconds, MAX_RETRY_AFTER_SECONDS))
    return None


def _retry_after_stamp(exc: BaseException, code: str, moment: datetime) -> str | None:
    """When this topic may be asked again, as an instant, or ``None`` for now."""
    seconds = retry_after_seconds(exc)
    if seconds is None and code == "rate_limited":
        seconds = DEFAULT_RATE_LIMIT_BACKOFF_SECONDS
    if seconds is None or seconds <= 0:
        return None
    return stamp(moment + timedelta(seconds=seconds))


# ---------------------------------------------------------------------------
# Message normalisation. GroupMe's payload is JSON off the wire and every field
# here is defended, because a message that cannot be parsed must be skipped
# rather than allowed to abort a catch-up that has 2000 good ones behind it.
# ---------------------------------------------------------------------------


def _epoch_to_iso(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    try:
        return stamp(datetime.fromtimestamp(seconds, UTC))
    except (OverflowError, OSError, ValueError):  # pragma: no cover — defensive
        return None


def normalize(raw: Mapping[str, Any], *, received_at: str) -> NormalizedMessage | None:
    """Turn one GroupMe payload into something the table will accept.

    Returns ``None`` for a payload with no usable id — without one there is no
    dedupe key, so storing it would mean re-forwarding it on every future poll.

    ``text`` becomes ``""`` when GroupMe sends null, which it does for a message
    that is only an image. The row is still worth having: it is the record that
    somebody posted at that minute, and the column is NOT NULL.

    The text is stored VERBATIM. Colin wants the real words, and this is the
    archive; the sanitising happens at the one boundary where the bytes stop
    being an archived record and become a line somebody reads
    (:func:`risk.services.groupme_forward.format_line`).
    """
    raw_id = raw.get("id")
    if raw_id is None or str(raw_id) == "":
        return None
    created = _epoch_to_iso(raw.get("created_at"))
    if created is None:
        # A string ``created_at`` is already ISO; anything else falls back to
        # the moment we read it, which is wrong by at most one poll interval
        # and is better than dropping the message.
        candidate = raw.get("created_at")
        created = candidate if isinstance(candidate, str) and candidate else received_at
    user_id = raw.get("user_id") or raw.get("sender_id")
    return NormalizedMessage(
        groupme_message_id=str(raw_id),
        sender_user_id=str(user_id) if user_id not in (None, "") else None,
        sender_name=str(raw.get("name") or raw.get("sender_name") or "unknown"),
        text=str(raw.get("text") or ""),
        created_at=created,
    )


def message_sort_key(message_id: str) -> tuple[int, int, str]:
    """Order two GroupMe message ids.

    They are decimal strings that grow monotonically, so the comparison that
    matters is numeric — ``"9"`` is not newer than ``"10"``, which is what a
    plain string sort would decide, and getting it wrong would park the cursor
    behind messages it had already stored and replay them forever. Non-numeric
    ids (a stub, a future format change) sort after every numeric one and among
    themselves lexicographically, so the loop still terminates.
    """
    if message_id.isdigit():
        return (0, int(message_id), "")
    return (1, 0, message_id)


# ---------------------------------------------------------------------------
# Targets. The topic list lives in ``groupme_groups`` (migration 0024), which is
# owned by the identity/announce side of this feature.
# ---------------------------------------------------------------------------


def load_targets(conn: sqlite3.Connection) -> list[PollTarget]:
    """The topics to read: every configured subgroup, in weekday order.

    Only rows with a ``parent_slug`` are polled. The parent group carries the
    membership and the roster-source group is where identities are read from —
    neither is a place risk traffic is posted, and reading them would forward
    the whole chapter's chat into Colin's feed.

    Guarded on the table existing. ``ensure_schema`` replays every migration on
    every connect so it will be there in the merged tree, but a health check
    that raises ``no such table`` on a database built before the identity
    migration landed is a worse answer than one that truthfully reports zero
    configured topics.
    """
    present = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'groupme_groups'"
    ).fetchone()
    if present is None:
        return []
    rows = conn.execute(
        """
        SELECT slug, groupme_id, label, weekday
        FROM groupme_groups
        WHERE parent_slug IS NOT NULL
        ORDER BY COALESCE(weekday, 8), slug
        """
    ).fetchall()
    return [
        PollTarget(group_slug=r["slug"], groupme_id=r["groupme_id"], label=r["label"])
        for r in rows
    ]


CLIENT_MODULE = "risk.services.groupme"
CLIENT_READ_FUNCTION = "read_messages_after"
"""The one name this module resolves out of the client, written down on both sides.

Spelled out as a constant rather than searched for, because the search is what
broke: this used to try a list of plausible names and take the first callable
one. Nothing in ``risk.services.groupme`` matched — the reads live on a METHOD
of ``GroupMeClient`` — so the production path raised on its very first request
and ``POST /api/groupme/poll`` answered 503. It went unnoticed because every
test injects a fake, which means this seam was never once exercised. A lookup
that accepts whatever happens to be callable cannot tell anybody the two halves
have stopped agreeing; one documented name can.
"""


def _default_list_messages() -> ListMessages:
    """Resolve the real client's read entry point at call time.

    Imported by name rather than with a module-level ``from ... import`` so this
    module keeps working — and its tests keep running — regardless of whether
    the HTTP client is present, and so nothing here has a static dependency on
    a module it does not own.

    What comes back is a bare module-level FUNCTION: not the module, and not a
    method bound to a client object. That is what makes this module's read-only
    guarantee structural. A function has no ``post_message`` and no
    ``add_members`` hanging off it, so there is nothing in scope here to reach
    them with, however this file is edited later.

    ``risk.services.groupme.read_messages_after`` also absorbs the two shape
    differences between the halves — the cursor is positional in
    :data:`ListMessages` and keyword-only on the client, and the client returns
    dataclasses where :func:`normalize` reads mappings. Both are documented
    there. Neither belongs here: this module does not know what a
    ``GroupMeClient`` is, and that is the property worth keeping.
    """
    module = importlib.import_module(CLIENT_MODULE)
    read = getattr(module, CLIENT_READ_FUNCTION, None)
    if not callable(read):
        raise ImportError(
            f"{CLIENT_MODULE} exposes no {CLIENT_READ_FUNCTION}() —"
            " the forwarder has no read entry point"
        )
    return read  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# The heartbeat.
# ---------------------------------------------------------------------------


def heartbeat_path(conn: sqlite3.Connection) -> Path | None:
    """Where the "a cycle finished" marker lives: beside the database file.

    Derived from the connection rather than from ``resolve_db_path`` so that the
    LaunchAgent process and the API process land on the same file by
    construction — they are pointed at one database, and this follows it. An
    in-memory connection has no file and gets no heartbeat.
    """
    env = os.environ.get(HEARTBEAT_ENV, "").strip()
    if env:
        return Path(env)
    row = conn.execute("PRAGMA database_list").fetchone()
    if row is None:
        return None
    db_file = row["file"]
    if not db_file:
        return None
    path = Path(db_file)
    return path.with_name(path.name + HEARTBEAT_SUFFIX)


def write_heartbeat(conn: sqlite3.Connection, *, now: datetime | None = None) -> None:
    """Stamp "the forwarder is alive" at the end of a cycle.

    A file rather than a row because it answers a different question than
    ``last_polled_at`` does. That column says a TOPIC was read; this says the
    PROCESS ran — which is still the truth when no topic is configured yet, when
    every topic failed, and when every topic is inside a rate-limit back-off. A
    health endpoint that could only see per-topic state would report a perfectly
    alive forwarder with an empty config as dead.

    Written via a temp file and ``replace`` so a reader never catches it
    half-written, and failures are swallowed: a read-only disk must not take
    down the poll loop that this only observes.
    """
    path = heartbeat_path(conn)
    if path is None:
        return
    moment = now or datetime.now(UTC)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(stamp(moment) + "\n")
        tmp.replace(path)
    except OSError as exc:  # pragma: no cover — defensive
        log.warning("groupme forwarder: heartbeat not written (%s)", type(exc).__name__)


def read_heartbeat(conn: sqlite3.Connection) -> datetime | None:
    path = heartbeat_path(conn)
    if path is None:
        return None
    try:
        return _parse_iso(path.read_text().strip())
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Polling.
# ---------------------------------------------------------------------------


def poll_group(
    conn: sqlite3.Connection,
    target: PollTarget,
    *,
    list_messages: ListMessages,
    now: datetime | None = None,
    max_pages: int = MAX_PAGES_PER_POLL,
    holder: str | None = None,
) -> GroupPollResult:
    """Read one topic forward from its stored cursor until it runs out.

    Never raises except :class:`LeaseLostError`, which is not this topic's problem
    and belongs to the caller. A topic that is unreachable records a code
    against itself and the cycle carries on to the next one — one dead topic
    must not take the other two down with it, and the failure counter is how a
    persistent problem becomes visible instead of silent.
    """
    moment = now or datetime.now(UTC)
    at = stamp(moment)
    state = poll_state_repo.get(conn, target.group_slug)
    cursor = state.last_message_id if state else None

    # Honour a back-off the server asked for. Not a failure and not a poll: the
    # topic is untouched, `last_polled_at` stays where it was, and the next
    # cycle after the window expires picks it up with the same cursor.
    due = _parse_iso(state.retry_after) if state else None
    if due is not None and due > moment:
        log.info(
            "groupme poll: %s in back-off until %s", target.group_slug, state.retry_after
        )
        return GroupPollResult(
            group_slug=target.group_slug,
            fetched=0,
            stored=0,
            duplicates=0,
            pages=0,
            ok=True,
            error_code=state.last_error if state else None,
            last_message_id=cursor,
            skipped=True,
        )

    fetched = stored = duplicates = pages = 0
    try:
        for _ in range(max_pages):
            batch = list_messages(target.groupme_id, cursor)
            if not batch:
                break
            pages += 1
            fetched += len(batch)
            parsed = [m for m in (normalize(raw, received_at=at) for raw in batch) if m]
            if not parsed:
                # A whole page of unusable payloads. There is no id to advance
                # to, so continuing would re-request the same page forever. The
                # cursor stays exactly where it was.
                log.warning(
                    "groupme poll: %s returned %d message(s) with no usable id",
                    target.group_slug,
                    len(batch),
                )
                break
            # ``after_id`` pages ascend, so this is the last message on the
            # page. Taking the max rather than the last element costs nothing
            # and means an out-of-order page cannot park the cursor behind
            # messages we have already stored.
            newest = max(parsed, key=lambda m: message_sort_key(m.groupme_message_id))
            with transaction(conn):
                if holder is not None and not lease_repo.held_by(conn, holder=holder, now=at):
                    raise LeaseLostError(target.group_slug)
                for message in parsed:
                    row_id = inbound_repo.insert_if_new(
                        conn,
                        groupme_message_id=message.groupme_message_id,
                        group_slug=target.group_slug,
                        sender_user_id=message.sender_user_id,
                        sender_name=message.sender_name,
                        text=message.text,
                        created_at=message.created_at,
                        received_at=at,
                    )
                    if row_id is None:
                        duplicates += 1
                    else:
                        stored += 1
                poll_state_repo.advance_cursor(
                    conn,
                    group_slug=target.group_slug,
                    last_message_id=newest.groupme_message_id,
                )
            if newest.groupme_message_id == cursor:
                # The client handed back the page we asked to skip past. Nothing
                # was lost (the writes above are all no-ops), but asking again
                # would loop until the cap.
                break
            cursor = newest.groupme_message_id
        else:
            log.warning(
                "groupme poll: %s hit the %d-page catch-up cap; resuming next cycle",
                target.group_slug,
                max_pages,
            )
    except LeaseLostError:
        raise
    except Exception as exc:  # noqa: BLE001 — a poll loop that dies stops forever
        code = classify_error(exc)
        retry_at = _retry_after_stamp(exc, code, moment)
        log.warning("groupme poll: %s failed (%s)", target.group_slug, code)
        with transaction(conn):
            poll_state_repo.record_failure(
                conn,
                group_slug=target.group_slug,
                polled_at=at,
                error_code=code,
                retry_after=retry_at,
            )
        return GroupPollResult(
            group_slug=target.group_slug,
            fetched=fetched,
            stored=stored,
            duplicates=duplicates,
            pages=pages,
            ok=False,
            error_code=code,
            last_message_id=cursor,
        )

    with transaction(conn):
        poll_state_repo.record_success(conn, group_slug=target.group_slug, polled_at=at)
    return GroupPollResult(
        group_slug=target.group_slug,
        fetched=fetched,
        stored=stored,
        duplicates=duplicates,
        pages=pages,
        ok=True,
        error_code=None,
        last_message_id=cursor,
    )


def poll_once(
    conn: sqlite3.Connection,
    *,
    list_messages: ListMessages | None = None,
    targets: Sequence[PollTarget] | None = None,
    now: datetime | None = None,
    forward: bool = True,
    feed: str | Path | None = None,
    forward_limit: int = groupme_forward.DEFAULT_FORWARD_LIMIT,
    max_pages: int = MAX_PAGES_PER_POLL,
    lease_ttl_seconds: int = LEASE_TTL_SECONDS,
) -> PollCycleResult:
    """One full cycle, under the lease: read every topic, then append what is new.

    Returns immediately with ``skipped_locked`` when another poller holds the
    lease. That is the normal answer to a LaunchAgent firing while a long
    catch-up is still running, not an error — the cycle in flight is already
    doing this work, and starting a second one would have two processes moving
    one cursor.

    The forward step is inside the same cycle rather than a separate job so that
    a message read at 21:04:02 is in Colin's tailed feed at 21:04:03. It is also
    where the machine's two failure modes are kept apart: a topic that will not
    answer is recorded per-topic and does not stop delivery of what other topics
    returned, and an unwritable feed is recorded once and does not stop the
    reading.

    The heartbeat is stamped LAST and unconditionally — including on a cycle
    where every topic failed, because "the process ran and everything was
    broken" is a different diagnosis from "the process is not running", and the
    health endpoint has to be able to tell them apart.
    """
    tick: Callable[[], datetime] = (lambda: now) if now is not None else _utcnow
    moment = tick()
    at = stamp(moment)
    holder = lease_repo.new_holder_token()
    expiry = stamp(moment + timedelta(seconds=lease_ttl_seconds))

    with transaction(conn):
        acquired = lease_repo.acquire(conn, holder=holder, now=at, expires_at=expiry)
    if not acquired:
        log.info("groupme poll: another cycle holds the lease; skipping")
        return PollCycleResult(
            polled_at=at, groups=(), forwarded=0, forward_error=None, skipped_locked=True
        )

    results: list[GroupPollResult] = []
    forwarded = 0
    forward_error: str | None = None
    lease_lost = False

    def keepalive() -> bool:
        """Extend the lease as work is done; False once it is no longer ours.

        Reads the clock fresh each time when the caller did not pin one, so a
        renewal during a long catch-up genuinely moves the expiry forward in
        wall-clock terms. A caller that DID pin ``now`` gets that instant for
        the whole cycle, which is what makes a test with a historical timestamp
        deterministic instead of instantly expired.
        """
        moment_now = tick()
        with transaction(conn):
            return lease_repo.renew(
                conn,
                holder=holder,
                now=stamp(moment_now),
                expires_at=stamp(moment_now + timedelta(seconds=lease_ttl_seconds)),
            )

    try:
        # Inside the try, so that a missing client module or an unreadable
        # config releases the lease on the way out. Leaking it would make the
        # next three LaunchAgent cycles skip for no visible reason.
        resolved_targets = list(targets) if targets is not None else load_targets(conn)
        client = list_messages or _default_list_messages()

        for target in resolved_targets:
            results.append(
                poll_group(
                    conn,
                    target,
                    list_messages=client,
                    now=moment,
                    max_pages=max_pages,
                    holder=holder,
                )
            )
            if not keepalive():
                raise LeaseLostError(target.group_slug)

        if forward:
            outcome = groupme_forward.forward_pending(
                conn,
                feed=feed,
                limit=forward_limit,
                now=moment,
                keepalive=keepalive,
            )
            forwarded = len(outcome.sent)
            forward_error = outcome.error_code
    except LeaseLostError:
        # Someone else owns the cursor now. Everything written so far was
        # written while we still held it, so it stands; we simply stop.
        lease_lost = True
        log.warning("groupme poll: lease lost mid-cycle; stopping")
    finally:
        with transaction(conn):
            lease_repo.release(conn, holder=holder)

    write_heartbeat(conn, now=moment)
    return PollCycleResult(
        polled_at=at,
        groups=tuple(results),
        forwarded=forwarded,
        forward_error=forward_error,
        lease_lost=lease_lost,
    )


# ---------------------------------------------------------------------------
# Health.
# ---------------------------------------------------------------------------


def health(
    conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
    stale_after_seconds: int = STALE_AFTER_SECONDS,
    targets: Sequence[PollTarget] | None = None,
) -> Health:
    """What the health endpoint reads.

    A topic is **stale** when its last SUCCESSFUL poll is older than
    ``stale_after_seconds``, or when it has never had one. Configured-but-never-
    polled counts as stale on purpose: it is indistinguishable, from the
    chair's side, from a poller that is silently doing nothing.

    ``heartbeat_age_seconds`` is how long since a cycle finished, taken as the
    more recent of the heartbeat file and the newest ``last_polled_at``. Both,
    because either alone lies: the file alone goes missing when the DB is moved
    or the marker is cleaned up, and the column alone never moves when no topic
    is configured or when every poll is failing before it can stamp.

    ``healthy`` is the conjunction a human actually means by "is this working" —
    a cycle ran recently AND every configured topic answered recently. With no
    topics configured it is False: a forwarder with nothing to forward is not
    healthy, it is unfinished, and that should be visible on the dashboard
    rather than reported as fine.

    ``last_error`` comes back exactly as stored, which is a code from
    :data:`ERROR_CODES`. There is no path by which a URL, a group id or anybody's
    message text reaches this payload.
    """
    moment = now or datetime.now(UTC)
    cutoff = timedelta(seconds=stale_after_seconds)
    resolved_targets = list(targets) if targets is not None else load_targets(conn)
    labels = {t.group_slug: t.label for t in resolved_targets}

    states = {s.group_slug: s for s in poll_state_repo.list_all(conn)}
    # Configured topics first, in their configured order, then any state row for
    # a topic that has since been removed from the config — that row is the only
    # remaining evidence that it was ever polled, and hiding it would make a
    # renamed slug look like a poller that stopped for no reason.
    slugs = [t.group_slug for t in resolved_targets]
    slugs += sorted(s for s in states if s not in labels)

    groups: list[GroupHealth] = []
    for slug in slugs:
        state = states.get(slug)
        last_ok = _parse_iso(state.last_ok_at) if state else None
        is_stale = last_ok is None or (moment - last_ok) > cutoff
        groups.append(
            GroupHealth(
                slug=slug,
                label=labels.get(slug, slug),
                last_polled_at=state.last_polled_at if state else None,
                last_ok_at=state.last_ok_at if state else None,
                consecutive_failures=state.consecutive_failures if state else 0,
                last_error=state.last_error if state else None,
                stale=is_stale,
            )
        )

    beats = [
        b
        for b in (read_heartbeat(conn), _parse_iso(poll_state_repo.newest_poll_time(conn)))
        if b is not None
    ]
    heartbeat_age: int | None = None
    if beats:
        heartbeat_age = max(0, int((moment - max(beats)).total_seconds()))

    healthy = (
        bool(groups)
        and heartbeat_age is not None
        and heartbeat_age <= stale_after_seconds
        and not any(g.stale for g in groups)
    )
    return Health(groups=tuple(groups), heartbeat_age_seconds=heartbeat_age, healthy=healthy)
