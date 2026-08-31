"""The GroupMe forwarder: catching up after a closed laptop, and never twice.

Two properties carry this feature, and neither of them shows up in a test that
polls a quiet group once:

**CATCH-UP.** The LaunchAgent does not run while the machine is asleep and
launchd does not replay the intervals it missed. The poll at 09:00 is the first
since 01:00, and a night of a party is sitting on GroupMe's side — more than one
page of it. A single-request poll looks perfect until the one night it matters,
and then it silently keeps the last hundred messages and drops the rest.

**IDEMPOTENCY.** The same message is offered for storage many times over its
life: the catch-up re-reads page boundaries, Colin runs the binary by hand while
the agent is mid-cycle, and the dashboard has a button that forces a cycle. Each
one must land exactly once, in the database and in the terminal.

Everything here runs against a fake GroupMe with ``after_id`` semantics — the
page immediately FOLLOWING the cursor, ascending — because that is the only
cursor that can be walked. Nothing here touches the network, and every name and
id is invented.
"""

from __future__ import annotations

import sqlite3
import stat
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from risk.api import create_app
from risk.db.connection import connect, transaction
from risk.db.schema import ensure_schema
from risk.repos import groupme_inbound as inbound_repo
from risk.repos import groupme_poll_lease as lease_repo
from risk.repos import groupme_poll_state as state_repo
from risk.services import groupme_forward as fwd
from risk.services import groupme_poll as poll

pytestmark = pytest.mark.integration

# Invented topics. The chapter's real GroupMe ids live only in the database on
# Colin's machine and are never written down in this repo.
TUESDAY = poll.PollTarget(group_slug="risk-tuesday", groupme_id="10000001", label="Tuesday")
FRIDAY = poll.PollTarget(group_slug="risk-friday", groupme_id="10000002", label="Friday")

T0 = datetime(2026, 9, 4, 1, 0, tzinfo=UTC)
"""1am. The last poll before the laptop lid closed."""

WOKE = datetime(2026, 9, 4, 9, 0, tzinfo=UTC)
"""9am, eight hours later. The first poll after it opened again."""


# ---------------------------------------------------------------------------
# A fake GroupMe.
# ---------------------------------------------------------------------------


class FakeGroupMe:
    """A message stream per topic, read with ``after_id`` semantics.

    ``after_id`` returns the page IMMEDIATELY following the cursor, ascending —
    NOT the most recent page, which is what ``since_id`` would do and what would
    make an eight-hour backlog unrecoverable. The fake implements the contract
    the real client owes this service, so a client that got it wrong would be
    caught by the tests below rather than by a lost night.

    The outbound half of GroupMe is present only to prove nothing calls it.
    """

    def __init__(self, page_size: int = 100) -> None:
        self.streams: dict[str, list[dict[str, Any]]] = {}
        self.page_size = page_size
        self.calls: list[tuple[str, str | None]] = []
        self.raise_for: dict[str, Exception] = {}
        self.outbound_calls = 0

    # --- stream construction (test-side, not part of the client contract) ---

    def add(
        self,
        group_id: str,
        *,
        message_id: int,
        text: str = "message",
        name: str = "Test Alpha",
        user_id: str = "20000001",
        created_at: datetime | None = None,
    ) -> None:
        self.streams.setdefault(group_id, []).append(
            {
                "id": str(message_id),
                "user_id": user_id,
                "name": name,
                "text": text,
                "created_at": int((created_at or T0).timestamp()),
            }
        )

    def add_run(self, group_id: str, first: int, count: int, *, at: datetime) -> list[str]:
        for offset in range(count):
            self.add(
                group_id,
                message_id=first + offset,
                text=f"line {first + offset}",
                created_at=at + timedelta(seconds=offset),
            )
        return [str(first + offset) for offset in range(count)]

    # --- the client contract ---

    def list_messages(
        self, group_id: str, after_id: str | None
    ) -> Sequence[Mapping[str, Any]]:
        self.calls.append((group_id, after_id))
        failure = self.raise_for.get(group_id)
        if failure is not None:
            raise failure
        stream = sorted(self.streams.get(group_id, []), key=lambda m: int(m["id"]))
        if after_id is None:
            # Bootstrap: no cursor yet, so the newest page is all we can
            # sensibly claim. Every later read walks forward from there.
            return stream[-self.page_size :]
        start = next(
            (i for i, m in enumerate(stream) if int(m["id"]) > int(after_id)), len(stream)
        )
        return stream[start : start + self.page_size]

    # --- the half that must never be reached ---

    def post_message(self, *_: Any, **__: Any) -> None:
        self.outbound_calls += 1
        raise AssertionError("the poller posted to GroupMe")

    def add_member(self, *_: Any, **__: Any) -> None:
        self.outbound_calls += 1
        raise AssertionError("the poller changed group membership")

    def remove_member(self, *_: Any, **__: Any) -> None:
        self.outbound_calls += 1
        raise AssertionError("the poller changed group membership")


class _HttpError(Exception):
    def __init__(self, status: int, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.status_code = status
        if retry_after is not None:
            self.retry_after = retry_after


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_ambient_cmux(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never inherit the developer's own cmux config.

    Without this a test run on Colin's machine would type invented fixture
    messages into whatever surface he had configured, and would pass or fail
    depending on whether cmux happened to be open.
    """
    for name in (fwd.SURFACE_ENV, fwd.CMUX_SAY_ENV, poll.HEARTBEAT_ENV):
        monkeypatch.delenv(name, raising=False)


def _seed_topics(conn: sqlite3.Connection, *targets: poll.PollTarget) -> None:
    """Create and fill ``groupme_groups``, which migration 0024 owns.

    Declared here with IF NOT EXISTS so this fixture is correct both before that
    migration lands in this branch and after the worktrees merge, where the real
    CREATE TABLE will already have run.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS groupme_groups (
          id INTEGER PRIMARY KEY,
          slug TEXT NOT NULL UNIQUE,
          groupme_id TEXT NOT NULL,
          parent_slug TEXT,
          weekday INTEGER,
          label TEXT NOT NULL
        );
        """
    )
    with transaction(conn):
        conn.execute(
            "INSERT OR IGNORE INTO groupme_groups (slug, groupme_id, parent_slug, weekday, label)"
            " VALUES ('risk-parent', '19999999', NULL, NULL, 'Risk')"
        )
        for weekday, target in enumerate(targets, start=2):
            conn.execute(
                "INSERT OR IGNORE INTO groupme_groups"
                " (slug, groupme_id, parent_slug, weekday, label)"
                " VALUES (?, ?, 'risk-parent', ?, ?)",
                (target.group_slug, target.groupme_id, weekday, target.label),
            )


@pytest.fixture()
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    conn = connect(tmp_path / "forwarder.db")
    ensure_schema(conn)
    _seed_topics(conn, TUESDAY, FRIDAY)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture()
def fake() -> FakeGroupMe:
    return FakeGroupMe()


def _stored_ids(conn: sqlite3.Connection, group_slug: str) -> list[int]:
    return [
        int(r["groupme_message_id"])
        for r in conn.execute(
            "SELECT groupme_message_id FROM groupme_inbound WHERE group_slug = ?"
            " ORDER BY CAST(groupme_message_id AS INTEGER)",
            (group_slug,),
        )
    ]


def _fake_cmux_say(tmp_path: Path, *, exit_code: int = 0) -> tuple[Path, Path]:
    """A stand-in for ``cmux-say`` that records its argv instead of typing it."""
    transcript = tmp_path / "cmux-transcript.txt"
    script = tmp_path / "cmux-say"
    script.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\t%s\\n" "$1" "$2" >> "{transcript}"\n'
        f"exit {exit_code}\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script, transcript


# ---------------------------------------------------------------------------
# Catch-up after the machine has been asleep. The headline case.
# ---------------------------------------------------------------------------


def test_a_night_of_messages_is_walked_in_full_after_eight_hours_asleep(
    db: sqlite3.Connection, fake: FakeGroupMe
) -> None:
    """250 messages arrive while the laptop is shut. All 250 must land, in one
    cycle, with the cursor on the newest and no gap anywhere in between.

    250 against a 100-message page is the whole point: anything that reads one
    page and stops keeps 100 and loses 150, and it loses them permanently —
    the cursor moves past them and no later poll will ever ask again.
    """
    fake.add(TUESDAY.groupme_id, message_id=1000, text="last thing before bed")
    poll.poll_once(db, list_messages=fake.list_messages, targets=[TUESDAY], now=T0, forward=False)
    assert state_repo.get(db, TUESDAY.group_slug).last_message_id == "1000"

    expected = fake.add_run(TUESDAY.groupme_id, first=1001, count=250, at=T0)

    result = poll.poll_once(
        db, list_messages=fake.list_messages, targets=[TUESDAY], now=WOKE, forward=False
    )

    stored = _stored_ids(db, TUESDAY.group_slug)
    assert stored == [1000, *(int(i) for i in expected)], "a gap or a duplicate in the backlog"
    assert len(stored) == 251
    assert state_repo.get(db, TUESDAY.group_slug).last_message_id == "1250"
    assert result.groups[0].stored == 250
    assert result.groups[0].pages == 3, "100 + 100 + 50, then the empty page that ends the loop"


def test_the_catch_up_loop_keeps_asking_until_a_page_comes_back_empty(
    db: sqlite3.Connection, fake: FakeGroupMe
) -> None:
    """The termination condition is an empty answer, not a short page.

    A short page is not proof of the end — only the client knows its own page
    size, and this service deliberately does not.
    """
    fake.add(TUESDAY.groupme_id, message_id=1)
    poll.poll_group(db, TUESDAY, list_messages=fake.list_messages, now=T0)
    fake.add_run(TUESDAY.groupme_id, first=2, count=200, at=T0)
    fake.calls.clear()

    poll.poll_group(db, TUESDAY, list_messages=fake.list_messages, now=WOKE)

    cursors = [after_id for _, after_id in fake.calls]
    assert cursors == ["1", "101", "201"], "each page must resume after the previous one"


def test_an_interrupted_catch_up_resumes_where_it_stopped(
    db: sqlite3.Connection, fake: FakeGroupMe
) -> None:
    """Kill the process mid-backlog and the next cycle picks up the next page.

    The cursor advances in the same transaction as its page, so what is durable
    is exactly what was written — never further ahead, which would be a hole.
    """
    fake.add(TUESDAY.groupme_id, message_id=1)
    poll.poll_group(db, TUESDAY, list_messages=fake.list_messages, now=T0)
    fake.add_run(TUESDAY.groupme_id, first=2, count=250, at=T0)

    poll.poll_group(db, TUESDAY, list_messages=fake.list_messages, now=WOKE, max_pages=1)
    assert len(_stored_ids(db, TUESDAY.group_slug)) == 101
    assert state_repo.get(db, TUESDAY.group_slug).last_message_id == "101"

    poll.poll_group(db, TUESDAY, list_messages=fake.list_messages, now=WOKE)
    assert _stored_ids(db, TUESDAY.group_slug) == list(range(1, 252))


def test_a_bootstrap_poll_takes_the_newest_page_and_anchors_the_cursor(
    db: sqlite3.Connection, fake: FakeGroupMe
) -> None:
    """A topic that has never been polled has no place to walk forward from.

    It anchors on the newest page — which is the only honest answer — and every
    later poll is a true catch-up from there.
    """
    fake.add_run(TUESDAY.groupme_id, first=1, count=150, at=T0)
    poll.poll_group(db, TUESDAY, list_messages=fake.list_messages, now=T0)
    assert state_repo.get(db, TUESDAY.group_slug).last_message_id == "150"
    assert len(_stored_ids(db, TUESDAY.group_slug)) == 100


# ---------------------------------------------------------------------------
# Idempotency.
# ---------------------------------------------------------------------------


def test_running_the_same_cycle_again_stores_nothing_new(
    db: sqlite3.Connection, fake: FakeGroupMe
) -> None:
    fake.add_run(TUESDAY.groupme_id, first=1, count=40, at=T0)
    first = poll.poll_once(
        db, list_messages=fake.list_messages, targets=[TUESDAY], now=T0, forward=False
    )
    assert first.groups[0].stored == 40

    for _ in range(3):
        again = poll.poll_once(
            db, list_messages=fake.list_messages, targets=[TUESDAY], now=T0, forward=False
        )
        assert again.groups[0].stored == 0

    assert len(_stored_ids(db, TUESDAY.group_slug)) == 40


def test_a_client_that_replays_the_same_page_cannot_duplicate_a_row(
    db: sqlite3.Connection,
) -> None:
    """The dedupe lives in the unique index, not in the loop's arithmetic.

    This client ignores the cursor entirely — the failure mode of a client that
    quietly implements ``since_id``, or of a server that returns a page it was
    asked to skip. Nothing duplicates, and the loop still terminates.
    """
    page = [
        {"id": "7", "user_id": "20000001", "name": "Test Alpha", "text": "hi", "created_at": 0},
        {"id": "8", "user_id": "20000001", "name": "Test Alpha", "text": "ho", "created_at": 0},
    ]
    calls = 0

    def stuck(group_id: str, after_id: str | None) -> list[dict[str, Any]]:
        nonlocal calls
        calls += 1
        return page

    result = poll.poll_group(db, TUESDAY, list_messages=stuck, now=T0)

    assert _stored_ids(db, TUESDAY.group_slug) == [7, 8]
    assert result.stored == 2
    assert result.duplicates == 2, "the replay was recognised rather than written"
    assert calls == 2, "the loop stopped once the cursor stopped moving"
    assert state_repo.get(db, TUESDAY.group_slug).last_message_id == "8"


def test_two_overlapping_writers_of_the_same_message_produce_one_row(
    db: sqlite3.Connection,
) -> None:
    """The repo-level guarantee underneath the whole feature."""
    with transaction(db):
        first = inbound_repo.insert_if_new(
            db,
            groupme_message_id="4242",
            group_slug=TUESDAY.group_slug,
            sender_name="Test Alpha",
            text="only once",
            created_at="2026-09-04T01:00:00+00:00",
            received_at="2026-09-04T01:00:05+00:00",
        )
        second = inbound_repo.insert_if_new(
            db,
            groupme_message_id="4242",
            group_slug=TUESDAY.group_slug,
            sender_name="Test Alpha",
            text="only once",
            created_at="2026-09-04T01:00:00+00:00",
            received_at="2026-09-04T01:00:05+00:00",
        )
    assert isinstance(first, int)
    assert second is None, "a duplicate must report itself, not look like a fresh insert"
    assert len(_stored_ids(db, TUESDAY.group_slug)) == 1


# ---------------------------------------------------------------------------
# Failures: never lose the cursor, never store anything but a code.
# ---------------------------------------------------------------------------


def test_a_rejected_fetch_leaves_the_cursor_exactly_where_it_was(
    db: sqlite3.Connection, fake: FakeGroupMe
) -> None:
    """Losing our place is worse than any error.

    A cursor reset to NULL either replays the night into the terminal or, on the
    next bootstrap, skips it. Staying put costs nothing and is always recoverable.
    """
    fake.add_run(TUESDAY.groupme_id, first=1, count=5, at=T0)
    poll.poll_group(db, TUESDAY, list_messages=fake.list_messages, now=T0)
    fake.raise_for[TUESDAY.groupme_id] = _HttpError(404, "https://api.example/groups/10000001")

    result = poll.poll_group(db, TUESDAY, list_messages=fake.list_messages, now=WOKE)

    state = state_repo.get(db, TUESDAY.group_slug)
    assert result.ok is False
    assert state.last_message_id == "5", "the cursor survived a rejected fetch"
    assert state.last_error == "not_found"


def test_the_stored_error_is_a_code_with_no_url_or_group_id_in_it(
    db: sqlite3.Connection, fake: FakeGroupMe
) -> None:
    """``last_error`` is rendered by a health endpoint and printed into a log
    file beside a public repo. An exception string carries the URL it was
    calling, and that URL contains a real group id."""
    leaky = _HttpError(401, "401 for https://api.groupme.test/v3/groups/10000001/messages")
    fake.raise_for[TUESDAY.groupme_id] = leaky

    poll.poll_group(db, TUESDAY, list_messages=fake.list_messages, now=T0)

    state = state_repo.get(db, TUESDAY.group_slug)
    assert state.last_error == "auth_rejected"
    assert state.last_error in poll.ERROR_CODES
    row_text = " ".join(str(v) for v in (state.last_error, state.retry_after, state.last_polled_at))
    assert TUESDAY.groupme_id not in row_text
    assert "http" not in row_text


def test_the_repo_refuses_anything_that_is_not_a_code(db: sqlite3.Connection) -> None:
    """Defence in depth: the column is guarded at its only door, because every
    caller upstream is one refactor away from passing ``str(exc)``."""
    with pytest.raises(ValueError, match="normalised code"), transaction(db):
        state_repo.record_failure(
            db,
            group_slug=TUESDAY.group_slug,
            polled_at="2026-09-04T01:00:00+00:00",
            error_code="HTTPError: 401 for https://api.groupme.test/v3/groups/10000001",
        )


def test_failures_count_up_and_a_success_clears_them(
    db: sqlite3.Connection, fake: FakeGroupMe
) -> None:
    fake.raise_for[TUESDAY.groupme_id] = TimeoutError("slow")
    for _ in range(3):
        poll.poll_group(db, TUESDAY, list_messages=fake.list_messages, now=T0)
    state = state_repo.get(db, TUESDAY.group_slug)
    assert state.consecutive_failures == 3
    assert state.last_error == "network_timeout"

    del fake.raise_for[TUESDAY.groupme_id]
    fake.add(TUESDAY.groupme_id, message_id=1)
    poll.poll_group(db, TUESDAY, list_messages=fake.list_messages, now=WOKE)

    state = state_repo.get(db, TUESDAY.group_slug)
    assert state.consecutive_failures == 0
    assert state.last_error is None
    assert state.last_ok_at is not None


def test_one_dead_topic_does_not_stop_the_others(
    db: sqlite3.Connection, fake: FakeGroupMe
) -> None:
    fake.raise_for[TUESDAY.groupme_id] = ConnectionResetError("dropped")
    fake.add_run(FRIDAY.groupme_id, first=1, count=3, at=T0)

    result = poll.poll_once(
        db, list_messages=fake.list_messages, targets=[TUESDAY, FRIDAY], now=T0, forward=False
    )

    by_slug = {g.group_slug: g for g in result.groups}
    assert by_slug[TUESDAY.group_slug].ok is False
    assert by_slug[FRIDAY.group_slug].ok is True
    assert len(_stored_ids(db, FRIDAY.group_slug)) == 3


def test_a_page_of_unusable_payloads_stops_that_topic_without_moving_the_cursor(
    db: sqlite3.Connection,
) -> None:
    """No id means no dedupe key, so the message cannot be stored — and there is
    nothing to advance to, so asking again would spin."""
    calls = 0

    def idless(group_id: str, after_id: str | None) -> list[dict[str, Any]]:
        nonlocal calls
        calls += 1
        return [{"text": "who sent this", "created_at": 0}]

    result = poll.poll_group(db, TUESDAY, list_messages=idless, now=T0)

    assert calls == 1
    assert result.stored == 0
    assert state_repo.get(db, TUESDAY.group_slug).last_message_id is None


# ---------------------------------------------------------------------------
# Rate limiting.
# ---------------------------------------------------------------------------


def test_a_retry_after_is_honoured_and_the_topic_sits_the_next_cycle_out(
    db: sqlite3.Connection, fake: FakeGroupMe
) -> None:
    fake.raise_for[TUESDAY.groupme_id] = _HttpError(429, "slow down", retry_after=600)
    poll.poll_group(db, TUESDAY, list_messages=fake.list_messages, now=T0)

    state = state_repo.get(db, TUESDAY.group_slug)
    assert state.last_error == "rate_limited"
    assert state.retry_after == poll.stamp(T0 + timedelta(seconds=600))

    # Two minutes later — inside the window. The client must not be called.
    del fake.raise_for[TUESDAY.groupme_id]
    fake.add(TUESDAY.groupme_id, message_id=1)
    fake.calls.clear()
    result = poll.poll_group(
        db, TUESDAY, list_messages=fake.list_messages, now=T0 + timedelta(seconds=120)
    )
    assert fake.calls == [], "we asked again inside the window the server gave us"
    assert result.skipped is True

    # Eleven minutes later — the window has passed.
    result = poll.poll_group(
        db, TUESDAY, list_messages=fake.list_messages, now=T0 + timedelta(seconds=660)
    )
    assert result.skipped is False
    assert result.stored == 1
    assert state_repo.get(db, TUESDAY.group_slug).retry_after is None


def test_a_429_with_no_header_still_backs_off(
    db: sqlite3.Connection, fake: FakeGroupMe
) -> None:
    fake.raise_for[TUESDAY.groupme_id] = _HttpError(429, "slow down")
    poll.poll_group(db, TUESDAY, list_messages=fake.list_messages, now=T0)
    state = state_repo.get(db, TUESDAY.group_slug)
    assert state.retry_after == poll.stamp(
        T0 + timedelta(seconds=poll.DEFAULT_RATE_LIMIT_BACKOFF_SECONDS)
    )


# ---------------------------------------------------------------------------
# One poller at a time.
# ---------------------------------------------------------------------------


def test_a_cycle_starting_while_another_holds_the_lease_does_nothing(
    db: sqlite3.Connection, fake: FakeGroupMe
) -> None:
    """The LaunchAgent fires every 120 seconds whether or not the last cycle
    finished. During a long catch-up that is guaranteed to overlap."""
    with transaction(db):
        assert lease_repo.acquire(
            db,
            holder="other-cycle",
            now=poll.stamp(T0),
            expires_at=poll.stamp(T0 + timedelta(seconds=180)),
        )
    fake.add_run(TUESDAY.groupme_id, first=1, count=5, at=T0)

    result = poll.poll_once(
        db, list_messages=fake.list_messages, targets=[TUESDAY], now=T0, forward=False
    )

    assert result.skipped_locked is True
    assert result.groups == ()
    assert fake.calls == [], "a second poller must not touch the network"
    assert _stored_ids(db, TUESDAY.group_slug) == []


def test_an_expired_lease_is_taken_over(db: sqlite3.Connection, fake: FakeGroupMe) -> None:
    """A crashed poller never gets to release its lease. Expiry is the recovery
    path — otherwise one crash would stop the forwarder permanently."""
    with transaction(db):
        lease_repo.acquire(
            db,
            holder="dead-cycle",
            now=poll.stamp(T0),
            expires_at=poll.stamp(T0 + timedelta(seconds=180)),
        )
    fake.add(TUESDAY.groupme_id, message_id=1)

    result = poll.poll_once(
        db,
        list_messages=fake.list_messages,
        targets=[TUESDAY],
        now=T0 + timedelta(seconds=200),
        forward=False,
    )

    assert result.skipped_locked is False
    assert result.groups[0].stored == 1


def test_a_holder_that_lost_the_lease_stops_writing_the_cursor(
    db: sqlite3.Connection, fake: FakeGroupMe
) -> None:
    """The rule that makes the lease worth having.

    A superseded cycle finishing late and writing back its older cursor would
    replay hours of messages into the terminal, so the check sits inside the
    same transaction as the write.
    """
    fake.add_run(TUESDAY.groupme_id, first=1, count=5, at=T0)
    with transaction(db):
        lease_repo.acquire(
            db,
            holder="successor",
            now=poll.stamp(T0),
            expires_at=poll.stamp(T0 + timedelta(seconds=180)),
        )

    with pytest.raises(poll.LeaseLostError):
        poll.poll_group(
            db, TUESDAY, list_messages=fake.list_messages, now=T0, holder="superseded"
        )

    assert state_repo.get(db, TUESDAY.group_slug) is None, "a stale holder wrote a cursor"
    assert _stored_ids(db, TUESDAY.group_slug) == []


def test_the_lease_is_released_so_the_very_next_cycle_runs(
    db: sqlite3.Connection, fake: FakeGroupMe
) -> None:
    fake.add(TUESDAY.groupme_id, message_id=1)
    poll.poll_once(db, list_messages=fake.list_messages, targets=[TUESDAY], now=T0, forward=False)
    assert lease_repo.get(db) is None

    fake.add(TUESDAY.groupme_id, message_id=2)
    second = poll.poll_once(
        db, list_messages=fake.list_messages, targets=[TUESDAY], now=T0, forward=False
    )
    assert second.skipped_locked is False
    assert second.groups[0].stored == 1


# ---------------------------------------------------------------------------
# The poller is read-only.
# ---------------------------------------------------------------------------


def test_the_poller_never_reaches_the_outbound_half_of_the_client(
    db: sqlite3.Connection, fake: FakeGroupMe
) -> None:
    """It holds one bound read method, not a client object, so there is nothing
    in scope to post with. Posting and membership are a different feature with
    its own confirmation gate."""
    fake.add_run(TUESDAY.groupme_id, first=1, count=10, at=T0)
    poll.poll_once(
        db, list_messages=fake.list_messages, targets=[TUESDAY, FRIDAY], now=T0, forward=False
    )
    assert fake.outbound_calls == 0


# ---------------------------------------------------------------------------
# Delivery into cmux.
# ---------------------------------------------------------------------------


def test_messages_reach_cmux_in_the_exact_format_oldest_first(
    db: sqlite3.Connection, fake: FakeGroupMe, tmp_path: Path
) -> None:
    script, transcript = _fake_cmux_say(tmp_path)
    fake.add(TUESDAY.groupme_id, message_id=1, text="first", name="Test Alpha")
    fake.add(TUESDAY.groupme_id, message_id=2, text="second", name="Test Bravo")

    result = poll.poll_once(
        db,
        list_messages=fake.list_messages,
        targets=[TUESDAY],
        now=T0,
        cmux_target="surface:test",
        cmux_say=script,
    )

    assert result.forwarded == 2
    assert result.forward_error is None
    lines = transcript.read_text().splitlines()
    assert lines == [
        'surface:test\t"first" - Test Alpha',
        'surface:test\t"second" - Test Bravo',
    ], "a backlog replayed newest-first reads as a conversation running backwards"


def test_a_message_already_delivered_is_never_sent_again(
    db: sqlite3.Connection, fake: FakeGroupMe, tmp_path: Path
) -> None:
    script, transcript = _fake_cmux_say(tmp_path)
    fake.add(TUESDAY.groupme_id, message_id=1, text="only once please")

    for _ in range(3):
        poll.poll_once(
            db,
            list_messages=fake.list_messages,
            targets=[TUESDAY],
            now=T0,
            cmux_target="surface:test",
            cmux_say=script,
        )

    assert transcript.read_text().splitlines() == ['surface:test\t"only once please" - Test Alpha']


def test_a_missing_cmux_binary_does_not_stop_the_poll_loop(
    db: sqlite3.Connection, fake: FakeGroupMe, tmp_path: Path
) -> None:
    """cmux is not running most of the day. If that took down the cycle, a
    closed terminal would silently become a missing message archive."""
    fake.add_run(TUESDAY.groupme_id, first=1, count=4, at=T0)

    result = poll.poll_once(
        db,
        list_messages=fake.list_messages,
        targets=[TUESDAY],
        now=T0,
        cmux_target="surface:test",
        cmux_say=tmp_path / "nothing-here",
    )

    assert result.groups[0].ok is True
    assert result.groups[0].stored == 4, "the messages were still read and stored"
    assert result.forwarded == 0
    assert result.forward_error == "cmux_binary_missing"
    assert inbound_repo.count_unforwarded(db) == 4, "and they are still queued"


def test_a_cmux_that_is_not_running_leaves_the_queue_for_next_time(
    db: sqlite3.Connection, fake: FakeGroupMe, tmp_path: Path
) -> None:
    dead, _ = _fake_cmux_say(tmp_path, exit_code=1)
    fake.add_run(TUESDAY.groupme_id, first=1, count=3, at=T0)
    poll.poll_once(
        db,
        list_messages=fake.list_messages,
        targets=[TUESDAY],
        now=T0,
        cmux_target="surface:test",
        cmux_say=dead,
    )
    assert inbound_repo.count_unforwarded(db) == 3

    later = tmp_path / "later"
    later.mkdir()
    alive, _ = _fake_cmux_say(later)
    result = poll.poll_once(
        db,
        list_messages=fake.list_messages,
        targets=[TUESDAY],
        now=T0,
        cmux_target="surface:test",
        cmux_say=alive,
    )
    assert result.forwarded == 3
    assert inbound_repo.count_unforwarded(db) == 0


def test_with_no_surface_configured_nothing_is_sent_and_nothing_is_lost(
    db: sqlite3.Connection, fake: FakeGroupMe
) -> None:
    """A wrong surface id is worse than none: it types the chapter's risk
    traffic into an unrelated session and marks it delivered on the way out."""
    fake.add(TUESDAY.groupme_id, message_id=1)
    result = poll.poll_once(db, list_messages=fake.list_messages, targets=[TUESDAY], now=T0)
    assert result.forwarded == 0
    assert result.forward_error == "cmux_not_configured"
    assert inbound_repo.count_unforwarded(db) == 1


def test_a_backlog_drains_at_the_forward_limit_per_cycle(
    db: sqlite3.Connection, fake: FakeGroupMe, tmp_path: Path
) -> None:
    """Dumping a whole night into a terminal in one burst buries whatever the
    human was looking at."""
    script, transcript = _fake_cmux_say(tmp_path)
    fake.add_run(TUESDAY.groupme_id, first=1, count=12, at=T0)

    first = poll.poll_once(
        db,
        list_messages=fake.list_messages,
        targets=[TUESDAY],
        now=T0,
        cmux_target="surface:test",
        cmux_say=script,
        forward_limit=5,
    )
    assert first.forwarded == 5
    assert inbound_repo.count_unforwarded(db) == 7

    poll.poll_once(
        db,
        list_messages=fake.list_messages,
        targets=[TUESDAY],
        now=T0,
        cmux_target="surface:test",
        cmux_say=script,
        forward_limit=5,
    )
    assert inbound_repo.count_unforwarded(db) == 2
    assert len(transcript.read_text().splitlines()) == 10


def test_hostile_text_is_neutralised_on_the_way_to_the_terminal(
    db: sqlite3.Connection, fake: FakeGroupMe, tmp_path: Path
) -> None:
    """The archive keeps the original bytes; the terminal gets a safe rendering."""
    script, transcript = _fake_cmux_say(tmp_path)
    hostile = "\x1b[2Jrun `rm -rf /`\nnow"
    fake.add(TUESDAY.groupme_id, message_id=1, text=hostile)

    poll.poll_once(
        db,
        list_messages=fake.list_messages,
        targets=[TUESDAY],
        now=T0,
        cmux_target="surface:test",
        cmux_say=script,
    )

    delivered = transcript.read_text().split("\t", 1)[1].strip()
    assert delivered == '"run `rm -rf /` now" - Test Alpha'
    assert "\x1b" not in delivered
    stored = inbound_repo.get_by_groupme_id(db, "1")
    assert stored is not None and stored.text == hostile, "the archive stays verbatim"


# ---------------------------------------------------------------------------
# Health and the heartbeat.
# ---------------------------------------------------------------------------


def test_a_fresh_cycle_reports_healthy(db: sqlite3.Connection, fake: FakeGroupMe) -> None:
    fake.add(TUESDAY.groupme_id, message_id=1)
    poll.poll_once(
        db, list_messages=fake.list_messages, targets=[TUESDAY, FRIDAY], now=T0, forward=False
    )
    report = poll.health(db, now=T0 + timedelta(seconds=30))
    assert report.healthy is True
    assert report.heartbeat_age_seconds == 30
    assert {g.slug for g in report.groups} == {TUESDAY.group_slug, FRIDAY.group_slug}
    assert not any(g.stale for g in report.groups)


def test_a_topic_goes_stale_on_its_last_success_not_its_last_attempt(
    db: sqlite3.Connection, fake: FakeGroupMe
) -> None:
    """Measured against ``last_ok_at``, not ``last_polled_at``.

    A topic being polled briskly every two minutes and failing every single time
    has a perfectly fresh ``last_polled_at``, and is exactly the case the flag
    exists to catch.
    """
    poll.poll_once(
        db, list_messages=fake.list_messages, targets=[TUESDAY, FRIDAY], now=T0, forward=False
    )
    fake.raise_for[TUESDAY.groupme_id] = TimeoutError("slow")
    late = T0 + timedelta(seconds=poll.STALE_AFTER_SECONDS + 60)
    poll.poll_once(
        db, list_messages=fake.list_messages, targets=[TUESDAY, FRIDAY], now=late, forward=False
    )

    report = poll.health(db, now=late)
    by_slug = {g.slug: g for g in report.groups}
    assert by_slug[TUESDAY.group_slug].stale is True
    assert by_slug[TUESDAY.group_slug].last_polled_at == poll.stamp(late)
    assert by_slug[TUESDAY.group_slug].consecutive_failures == 1
    assert by_slug[FRIDAY.group_slug].stale is False
    assert report.healthy is False


def test_a_configured_topic_that_has_never_been_polled_is_stale(
    db: sqlite3.Connection,
) -> None:
    """Indistinguishable, from the chair's side, from a poller doing nothing."""
    report = poll.health(db, now=T0)
    assert {g.slug for g in report.groups} == {TUESDAY.group_slug, FRIDAY.group_slug}
    assert all(g.stale for g in report.groups)
    assert report.heartbeat_age_seconds is None
    assert report.healthy is False


def test_the_heartbeat_marker_is_written_beside_the_database(
    db: sqlite3.Connection, fake: FakeGroupMe, tmp_path: Path
) -> None:
    """Derived from the connection so the LaunchAgent and the API agree by
    construction rather than by both computing the same path."""
    poll.poll_once(db, list_messages=fake.list_messages, targets=[], now=T0, forward=False)
    marker = tmp_path / ("forwarder.db" + poll.HEARTBEAT_SUFFIX)
    assert marker.exists()
    assert marker.read_text().strip() == poll.stamp(T0)


def test_the_heartbeat_still_moves_when_every_topic_failed(
    db: sqlite3.Connection, fake: FakeGroupMe
) -> None:
    """"The process ran and everything was broken" is a different diagnosis from
    "the process is not running", and health has to tell them apart."""
    fake.raise_for[TUESDAY.groupme_id] = TimeoutError("slow")
    poll.poll_once(db, list_messages=fake.list_messages, targets=[TUESDAY], now=T0, forward=False)
    report = poll.health(db, now=T0 + timedelta(seconds=10))
    assert report.heartbeat_age_seconds == 10
    assert report.healthy is False


def test_health_reports_nothing_but_codes(
    db: sqlite3.Connection, fake: FakeGroupMe
) -> None:
    fake.raise_for[TUESDAY.groupme_id] = _HttpError(
        403, "forbidden: https://api.groupme.test/v3/groups/10000001/messages"
    )
    poll.poll_once(db, list_messages=fake.list_messages, targets=[TUESDAY], now=T0, forward=False)
    report = poll.health(db, now=T0)
    errors = [g.last_error for g in report.groups if g.last_error]
    assert errors == ["auth_rejected"]
    assert all(e in poll.ERROR_CODES for e in errors)


def test_a_topic_dropped_from_the_config_still_shows_its_last_state(
    db: sqlite3.Connection, fake: FakeGroupMe
) -> None:
    """Its state row is the only remaining evidence it was ever polled; hiding
    it would make a renamed slug look like a poller that stopped for no reason."""
    fake.add(FRIDAY.groupme_id, message_id=1)
    poll.poll_once(db, list_messages=fake.list_messages, targets=[FRIDAY], now=T0, forward=False)
    report = poll.health(db, now=T0, targets=[TUESDAY])
    assert [g.slug for g in report.groups] == [TUESDAY.group_slug, FRIDAY.group_slug]


def test_targets_come_from_the_configured_subgroups_only(db: sqlite3.Connection) -> None:
    """The parent group carries membership and is not a place risk traffic is
    posted; polling it would forward the whole chapter's chat."""
    targets = poll.load_targets(db)
    assert [t.group_slug for t in targets] == [TUESDAY.group_slug, FRIDAY.group_slug]


def test_no_configured_topics_is_reported_as_unhealthy_not_as_fine(
    tmp_path: Path,
) -> None:
    """A forwarder with nothing to forward is unfinished, not healthy."""
    conn = connect(tmp_path / "bare.db")
    ensure_schema(conn)
    report = poll.health(conn, now=T0)
    assert report.groups == ()
    assert report.healthy is False
    conn.close()


# ---------------------------------------------------------------------------
# Replay safety: 0025 must survive a reconnect with real rows in it.
# ---------------------------------------------------------------------------


def test_the_migration_replays_with_messages_and_a_cursor_in_place(
    tmp_path: Path,
) -> None:
    """``ensure_schema`` re-runs every migration on EVERY connect, so a
    migration can be perfectly idempotent against an empty database and still
    abort against a populated one."""
    path = tmp_path / "replay.db"
    conn = connect(path)
    ensure_schema(conn)
    _seed_topics(conn, TUESDAY)
    fake = FakeGroupMe()
    fake.add_run(TUESDAY.groupme_id, first=1, count=3, at=T0)
    poll.poll_once(conn, list_messages=fake.list_messages, targets=[TUESDAY], now=T0, forward=False)
    conn.close()

    for _ in range(2):
        reopened = connect(path)
        ensure_schema(reopened)
        assert len(inbound_repo.list_recent(reopened, limit=50)) == 3
        assert state_repo.get(reopened, TUESDAY.group_slug).last_message_id == "3"
        reopened.close()


# ---------------------------------------------------------------------------
# The API surface.
# ---------------------------------------------------------------------------


@pytest.fixture()
def api(tmp_path: Path) -> Iterator[tuple[TestClient, FakeGroupMe]]:
    db_path = tmp_path / "api.db"
    conn = connect(db_path)
    ensure_schema(conn)
    _seed_topics(conn, TUESDAY, FRIDAY)
    conn.close()

    fake = FakeGroupMe()
    app = create_app(db_path=db_path)
    app.state.groupme_list_messages = fake.list_messages
    with TestClient(app) as client:
        yield client, fake


def test_health_route_answers_before_anything_has_ever_polled(
    api: tuple[TestClient, FakeGroupMe],
) -> None:
    client, _ = api
    body = client.get("/api/groupme/health").json()
    assert body["healthy"] is False
    assert body["heartbeat_age_seconds"] is None
    assert [g["slug"] for g in body["groups"]] == [TUESDAY.group_slug, FRIDAY.group_slug]
    assert all(g["stale"] for g in body["groups"])


def test_poll_route_forces_a_cycle_and_answers_with_health(
    api: tuple[TestClient, FakeGroupMe],
) -> None:
    client, fake = api
    fake.add_run(TUESDAY.groupme_id, first=1, count=4, at=T0)

    body = client.post("/api/groupme/poll").json()

    assert body["healthy"] is True
    assert body["heartbeat_age_seconds"] is not None
    assert len(client.get("/api/groupme/inbound").json()) == 4


def test_pressing_poll_twice_stores_nothing_the_second_time(
    api: tuple[TestClient, FakeGroupMe],
) -> None:
    client, fake = api
    fake.add_run(TUESDAY.groupme_id, first=1, count=4, at=T0)
    client.post("/api/groupme/poll")
    client.post("/api/groupme/poll")
    assert len(client.get("/api/groupme/inbound").json()) == 4


def test_inbound_route_is_newest_first_and_honours_the_limit(
    api: tuple[TestClient, FakeGroupMe],
) -> None:
    client, fake = api
    fake.add_run(TUESDAY.groupme_id, first=1, count=6, at=T0)
    client.post("/api/groupme/poll")

    rows = client.get("/api/groupme/inbound", params={"limit": 3}).json()
    assert [r["text"] for r in rows] == ["line 6", "line 5", "line 4"]
    assert set(rows[0]) == {
        "id",
        "group_slug",
        "sender_name",
        "text",
        "created_at",
        "triage",
        "triage_note",
    }, "the DTO must not leak sender_user_id or the dedupe key"


def test_inbound_route_filters_by_triage(
    api: tuple[TestClient, FakeGroupMe], tmp_path: Path
) -> None:
    client, fake = api
    fake.add_run(TUESDAY.groupme_id, first=1, count=3, at=T0)
    client.post("/api/groupme/poll")

    conn = connect(tmp_path / "api.db")
    ensure_schema(conn)
    target = inbound_repo.list_recent(conn, limit=1)[0]
    with transaction(conn):
        inbound_repo.set_triage(conn, message_id=target.id, triage="urgent", triage_note="water")
    conn.close()

    urgent = client.get("/api/groupme/inbound", params={"triage": "urgent"}).json()
    assert len(urgent) == 1
    assert urgent[0]["triage_note"] == "water"


def test_inbound_route_rejects_a_triage_state_that_does_not_exist(
    api: tuple[TestClient, FakeGroupMe],
) -> None:
    client, _ = api
    response = client.get("/api/groupme/inbound", params={"triage": "panicking"})
    assert response.status_code == 400
    assert "handled" in response.json()["detail"]


def test_health_route_never_returns_anything_but_a_code(
    api: tuple[TestClient, FakeGroupMe],
) -> None:
    client, fake = api
    fake.raise_for[TUESDAY.groupme_id] = _HttpError(
        401, "unauthorized for https://api.groupme.test/v3/groups/10000001/messages"
    )
    client.post("/api/groupme/poll")

    body = client.get("/api/groupme/health").json()
    by_slug = {g["slug"]: g for g in body["groups"]}
    assert by_slug[TUESDAY.group_slug]["last_error"] == "auth_rejected"
    assert "10000001" not in repr(body), "a group id reached an HTTP response"
    assert "http" not in repr(body).lower()
