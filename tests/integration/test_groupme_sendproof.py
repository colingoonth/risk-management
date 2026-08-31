"""Failure-boundary proofs for the GroupMe send-once protocol.

Every HTTP-facing object in this module is a ``StubClient``. The concurrency
test uses two real SQLite connections, but no test constructs a real GroupMe
client, reads a token, or opens a socket.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from threading import Event, Thread

import pytest

from risk.db.connection import connect
from risk.repos import groupme_outbound as ledger_repo
from risk.services import groupme_announce as announce_svc
from risk.services import groupme_outbound as outbound_svc
from risk.services.groupme_announce import MentionMismatchError
from tests.integration._groupme_helpers import (
    FRIDAY,
    StubClient,
    assign,
    link,
    seed_event,
    seed_groups,
    seed_member,
    seed_semester,
)

pytestmark = pytest.mark.integration


def _one_post_plan(db, sem_id: int):
    member_id = seed_member(db, "test-alpha", "Test Alpha")
    link(db, member_id, "user-1", "Test Alpha")
    event_id = seed_event(db, sem_id, "Sample Mixer", FRIDAY)
    assign(db, event_id, member_id, "door")
    return announce_svc.build_plan(db, semester_id=sem_id, on_or_after=FRIDAY, on_or_before=FRIDAY)


def _database_path(db) -> Path:
    row = db.execute("PRAGMA database_list").fetchone()
    assert row is not None
    return Path(row["file"])


def test_every_post_is_verified_before_any_row_is_reserved_or_message_is_posted(db) -> None:
    sem_id = seed_semester(db)
    seed_groups(db, days={5: "risk-friday", 6: "risk-saturday"})
    member_id = seed_member(db, "test-alpha", "Test Alpha")
    link(db, member_id, "user-1", "Test Alpha")
    friday_event = seed_event(db, sem_id, "Sample Mixer", FRIDAY)
    saturday_event = seed_event(db, sem_id, "Sample Social", "2026-09-05")
    assign(db, friday_event, member_id, "door")
    assign(db, saturday_event, member_id, "bar")
    plan = announce_svc.build_plan(
        db,
        semester_id=sem_id,
        on_or_after=FRIDAY,
        on_or_before="2026-09-05",
    )
    bad_mention = replace(plan.posts[1].mentions[0], offset=plan.posts[1].mentions[0].offset + 1)
    posts = (*plan.posts[:1], replace(plan.posts[1], mentions=(bad_mention,)))
    client = StubClient()

    with pytest.raises(MentionMismatchError):
        outbound_svc.post_announcements(db, client, posts, confirm=True)

    assert client.posted == []
    assert ledger_repo.list_for_event(db, friday_event) == []
    assert ledger_repo.list_for_event(db, saturday_event) == []


def test_a_crash_after_reserve_is_reconciled_instead_of_blindly_retried(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    sem_id = seed_semester(db)
    seed_groups(db)
    plan = _one_post_plan(db, sem_id)
    original_reserve = outbound_svc._reserve

    def reserve_then_crash(conn, post):
        original_reserve(conn, post)
        raise RuntimeError("simulated process crash after reservation")

    monkeypatch.setattr(outbound_svc, "_reserve", reserve_then_crash)
    with pytest.raises(RuntimeError, match="simulated process crash"):
        outbound_svc.post_announcements(db, StubClient(), plan.posts, confirm=True)
    monkeypatch.setattr(outbound_svc, "_reserve", original_reserve)

    reopened = connect(_database_path(db))
    try:
        rows = ledger_repo.list_unsettled(reopened)
        assert [row.state for row in rows] == ["pending"]

        client = StubClient()
        blocked = outbound_svc.post_announcements(reopened, client, plan.posts, confirm=True)
        assert [result.outcome for result in blocked] == ["blocked"]
        assert "reconcile" in (blocked[0].detail or "")
        assert client.posted == []

        resolved = outbound_svc.reconcile(reopened, client)
        assert [result.resolved_to for result in resolved] == ["failed"]

        retried = outbound_svc.post_announcements(reopened, client, plan.posts, confirm=True)
        assert [result.outcome for result in retried] == ["sent"]
        assert len(client.posted) == 1
    finally:
        reopened.close()


class _BlockingStubClient(StubClient):
    """Pause the first sender after its reservation and before its stubbed post."""

    def __init__(self, *, entered: Event, release: Event) -> None:
        super().__init__()
        self.entered = entered
        self.release = release

    def post_message(self, group_id, text, *, mentions=(), source_guid=None):
        self.entered.set()
        if not self.release.wait(timeout=5):
            raise AssertionError("the concurrent sender never released the first sender")
        return super().post_message(group_id, text, mentions=mentions, source_guid=source_guid)


def test_two_connections_cannot_both_post_the_same_announcement(db) -> None:
    sem_id = seed_semester(db)
    seed_groups(db)
    plan = _one_post_plan(db, sem_id)
    db_path = _database_path(db)
    first_entered = Event()
    release_first = Event()
    first_client = _BlockingStubClient(entered=first_entered, release=release_first)
    second_client = StubClient()
    first_results = []
    first_errors: list[BaseException] = []

    def send_first() -> None:
        first_conn = connect(db_path)
        try:
            first_results.extend(
                outbound_svc.post_announcements(first_conn, first_client, plan.posts, confirm=True)
            )
        except BaseException as exc:
            first_errors.append(exc)
        finally:
            first_conn.close()

    worker = Thread(target=send_first, daemon=True)
    worker.start()
    try:
        assert first_entered.wait(timeout=5), "the first sender never reached its stubbed call"
        second_results = outbound_svc.post_announcements(
            db, second_client, plan.posts, confirm=True
        )
    finally:
        release_first.set()
        worker.join(timeout=5)

    assert not worker.is_alive()
    assert first_errors == []
    assert [result.outcome for result in first_results] == ["sent"]
    assert [result.outcome for result in second_results] == ["blocked"]
    assert len(first_client.posted) == 1
    assert second_client.posted == []
