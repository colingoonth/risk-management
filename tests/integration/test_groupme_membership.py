"""Removal timing, identity blocking, and the send-once ledger.

The removal tests are the ones that matter most: getting them wrong removes the
cleanup crew from the group hours before they are due to work, and the first
they hear of it is not being able to read the chat that tells them when.

Every name is invented; every id is an obvious placeholder.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from risk.db.connection import transaction
from risk.repos import groupme_identities as identities_repo
from risk.repos import groupme_outbound as ledger_repo
from risk.repos import member_roles as member_roles_repo
from risk.repos import roles as roles_repo
from risk.services import groupme_announce as announce_svc
from risk.services import groupme_identity as identity_svc
from risk.services import groupme_membership as membership_svc
from risk.services import groupme_outbound as outbound_svc
from risk.services.groupme import (
    GroupMeAmbiguousError,
    GroupMeError,
    InboundMessage,
)
from tests.integration._groupme_helpers import (
    FRIDAY,
    StubClient,
    account,
    assign,
    link,
    seed_event,
    seed_groups,
    seed_member,
    seed_semester,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Removal timing — shift WINDOW end, never the event date
# ---------------------------------------------------------------------------


def test_shift_window_end_uses_the_real_window_not_the_event_date(db) -> None:
    seed_semester(db)
    assert membership_svc.shift_window_end(
        db, shift_type_slug="cleanup", event_date=FRIDAY
    ) == datetime(2026, 9, 5, 12, 0)
    assert membership_svc.shift_window_end(
        db, shift_type_slug="door", event_date=FRIDAY
    ) == datetime(2026, 9, 4, 23, 59)
    assert membership_svc.shift_window_end(
        db, shift_type_slug="setup", event_date=FRIDAY
    ) == datetime(2026, 9, 4, 23, 59)


def test_the_cleanup_crew_is_not_removed_at_midnight(db) -> None:
    """THE bug this module exists to prevent.

    Cleanup is worked the next MORNING. Using the event date removes the crew
    at midnight, twelve hours before they show up.
    """
    sem_id = seed_semester(db)
    seed_groups(db)
    member_id = seed_member(db, "test-alpha", "Test Alpha")
    link(db, member_id, "user-1", "Test Alpha")
    event_id = seed_event(db, sem_id, "Sample Mixer", FRIDAY)
    assign(db, event_id, member_id, "cleanup")

    present = [account("user-1", "Test Alpha")]
    just_after_midnight = datetime(2026, 9, 5, 0, 30)
    plan = membership_svc.build_plan(
        db,
        semester_id=sem_id,
        on_or_after=FRIDAY,
        on_or_before=FRIDAY,
        present=present,
        now=just_after_midnight,
    )
    assert plan.remove == (), "the cleanup crew was kicked before they worked"


def test_the_cleanup_crew_is_removable_once_the_window_has_closed(db) -> None:
    sem_id = seed_semester(db)
    seed_groups(db)
    member_id = seed_member(db, "test-alpha", "Test Alpha")
    link(db, member_id, "user-1", "Test Alpha")
    event_id = seed_event(db, sem_id, "Sample Mixer", FRIDAY)
    assign(db, event_id, member_id, "cleanup")

    plan = membership_svc.build_plan(
        db,
        semester_id=sem_id,
        on_or_after=FRIDAY,
        on_or_before=FRIDAY,
        present=[account("user-1", "Test Alpha")],
        now=datetime(2026, 9, 5, 12, 1),
    )
    assert [r.display_name for r in plan.remove] == ["Test Alpha"]
    assert "finished" in plan.remove[0].reason


def test_the_boundary_minute_itself_is_removable(db) -> None:
    sem_id = seed_semester(db)
    seed_groups(db)
    member_id = seed_member(db, "test-alpha", "Test Alpha")
    link(db, member_id, "user-1", "Test Alpha")
    event_id = seed_event(db, sem_id, "Sample Mixer", FRIDAY)
    assign(db, event_id, member_id, "cleanup")
    plan = membership_svc.build_plan(
        db,
        semester_id=sem_id,
        on_or_after=FRIDAY,
        on_or_before=FRIDAY,
        present=[account("user-1", "Test Alpha")],
        now=datetime(2026, 9, 5, 12, 0),
    )
    assert len(plan.remove) == 1


def test_the_latest_of_several_shifts_governs(db) -> None:
    """A man on door Friday and cleanup Saturday is involved until Sunday noon."""
    sem_id = seed_semester(db)
    seed_groups(db)
    member_id = seed_member(db, "test-alpha", "Test Alpha")
    link(db, member_id, "user-1", "Test Alpha")
    friday = seed_event(db, sem_id, "Sample Mixer", FRIDAY)
    saturday = seed_event(db, sem_id, "Sample Krush", "2026-09-05")
    assign(db, friday, member_id, "door")
    assign(db, saturday, member_id, "cleanup")

    still_working = membership_svc.build_plan(
        db,
        semester_id=sem_id,
        on_or_after=FRIDAY,
        on_or_before="2026-09-05",
        present=[account("user-1", "Test Alpha")],
        now=datetime(2026, 9, 5, 23, 0),
    )
    assert still_working.remove == ()

    done = membership_svc.build_plan(
        db,
        semester_id=sem_id,
        on_or_after=FRIDAY,
        on_or_before="2026-09-05",
        present=[account("user-1", "Test Alpha")],
        now=datetime(2026, 9, 6, 12, 30),
    )
    assert [r.display_name for r in done.remove] == ["Test Alpha"]


def test_the_cleanup_crew_survives_the_window_rolling_past_their_event(db) -> None:
    """The Sunday-morning trap: the week rolls forward while they are still owed.

    Cleanup is worked the MORNING AFTER. On the Sunday the chair opens the page,
    the announcement window has already advanced past Saturday's event, so the
    Saturday event is outside it. If removal is scoped to that window, the crew
    reads as having no shift and is ejected at dawn on the day they are due —
    from the chat that tells them when to show up, for the least popular job.
    """
    sem_id = seed_semester(db)
    seed_groups(db)
    member_id = seed_member(db, "test-sweeper", "Test Sweeper")
    link(db, member_id, "user-sweep", "Test Sweeper")
    event_id = seed_event(db, sem_id, "Test Party", FRIDAY)
    assign(db, event_id, member_id, "cleanup")

    # The window now starts the day AFTER his event; his cleanup window is still
    # open until noon. Evaluated at 06:00, six hours before he works.
    plan = membership_svc.build_plan(
        db,
        semester_id=sem_id,
        on_or_after="2026-09-05",
        on_or_before="2026-09-11",
        present=[account("user-sweep", "Test Sweeper")],
        now=datetime(2026, 9, 5, 6, 0),
    )
    assert [r.display_name for r in plan.remove] == []

    # And once he has genuinely finished, at 12:30, he goes.
    after = membership_svc.build_plan(
        db,
        semester_id=sem_id,
        on_or_after="2026-09-05",
        on_or_before="2026-09-11",
        present=[account("user-sweep", "Test Sweeper")],
        now=datetime(2026, 9, 5, 12, 30),
    )
    assert [r.display_name for r in after.remove] == ["Test Sweeper"]


def test_somebody_with_no_shift_in_the_window_is_proposed_for_removal(db) -> None:
    sem_id = seed_semester(db)
    seed_groups(db)
    member_id = seed_member(db, "test-alpha", "Test Alpha")
    link(db, member_id, "user-1", "Test Alpha")
    plan = membership_svc.build_plan(
        db,
        semester_id=sem_id,
        on_or_after=FRIDAY,
        on_or_before=FRIDAY,
        present=[account("user-1", "Test Alpha")],
        now=datetime(2026, 9, 4, 12, 0),
    )
    assert plan.remove[0].reason == "no unfinished shift"
    assert plan.remove[0].membership_id == "mem-user-1"


def test_hard_excluded_roles_are_skipped_but_soft_and_finished_workers_are_removed(
    db,
) -> None:
    sem_id = seed_semester(db)
    seed_groups(db)
    chair_id = seed_member(db, "test-chair", "Test Chair")
    officer_id = seed_member(db, "test-officer", "Test Officer")
    soft_id = seed_member(db, "test-soft", "Test Soft")
    worker_id = seed_member(db, "test-worker", "Test Worker")
    for member_id, user_id, nickname in (
        (chair_id, "user-chair", "Test Chair"),
        (officer_id, "user-officer", "Test Officer"),
        (soft_id, "user-soft", "Test Soft"),
        (worker_id, "user-worker", "Test Worker"),
    ):
        link(db, member_id, user_id, nickname)

    risk_chair = roles_repo.get_by_slug(db, "risk_chair")
    exec_role = roles_repo.get_by_slug(db, "exec")
    pledge_chair = roles_repo.get_by_slug(db, "pledge_chair")
    assert risk_chair is not None
    assert exec_role is not None
    assert pledge_chair is not None
    with transaction(db):
        member_roles_repo.set_role(
            db, member_id=chair_id, role_id=risk_chair.id, semester_id=sem_id
        )
        member_roles_repo.set_role(
            db, member_id=officer_id, role_id=exec_role.id, semester_id=sem_id
        )
        member_roles_repo.set_role(
            db, member_id=soft_id, role_id=pledge_chair.id, semester_id=sem_id
        )

    event_id = seed_event(db, sem_id, "Sample Mixer", FRIDAY)
    assign(db, event_id, worker_id, "door")
    present = [
        account("user-chair", "Test Chair"),
        account("user-officer", "Test Officer"),
        account("user-soft", "Test Soft"),
        account("user-worker", "Test Worker"),
    ]
    plan = membership_svc.build_plan(
        db,
        semester_id=sem_id,
        on_or_after=FRIDAY,
        on_or_before=FRIDAY,
        present=present,
        now=datetime(2026, 9, 5, 12, 0),
    )

    assert [r.display_name for r in plan.remove] == ["Test Soft", "Test Worker"]
    assert [r.reason for r in plan.remove] == [
        "no unfinished shift",
        "all shifts finished 2026-09-04 23:59",
    ]
    assert [e.display_name for e in plan.hard_excluded] == [
        "Test Chair",
        "Test Officer",
    ]
    assert [e.detail for e in plan.hard_excluded] == [
        "Risk Chair is hard-excluded from assignment",
        "Exec is hard-excluded from assignment",
    ]


def test_an_unrecognised_account_is_reported_and_never_removed(db) -> None:
    """The chair, an exec, an alumnus, or somebody still awaiting mapping."""
    sem_id = seed_semester(db)
    seed_groups(db)
    plan = membership_svc.build_plan(
        db,
        semester_id=sem_id,
        on_or_after=FRIDAY,
        on_or_before=FRIDAY,
        present=[account("user-99", "Someone Unmapped")],
        now=datetime(2026, 9, 4, 12, 0),
    )
    assert plan.remove == ()
    assert [u.nickname for u in plan.unrecognised] == ["Someone Unmapped"]


def test_somebody_working_who_is_not_in_the_group_is_added(db) -> None:
    sem_id = seed_semester(db)
    seed_groups(db)
    member_id = seed_member(db, "test-alpha", "Test Alpha")
    link(db, member_id, "user-1", "Test Alpha")
    event_id = seed_event(db, sem_id, "Sample Mixer", FRIDAY)
    assign(db, event_id, member_id, "door")
    plan = membership_svc.build_plan(
        db,
        semester_id=sem_id,
        on_or_after=FRIDAY,
        on_or_before=FRIDAY,
        present=[],
        now=datetime(2026, 9, 1, 12, 0),
    )
    assert [a.display_name for a in plan.add] == ["Test Alpha"]


def test_a_worker_with_no_link_is_reported_rather_than_guessed_at(db) -> None:
    sem_id = seed_semester(db)
    seed_groups(db)
    member_id = seed_member(db, "test-alpha", "Test Alpha")
    event_id = seed_event(db, sem_id, "Sample Mixer", FRIDAY)
    assign(db, event_id, member_id, "door")
    plan = membership_svc.build_plan(
        db,
        semester_id=sem_id,
        on_or_after=FRIDAY,
        on_or_before=FRIDAY,
        present=[],
        now=datetime(2026, 9, 1, 12, 0),
    )
    assert plan.add == ()
    assert plan.unlinked_workers == ((member_id, "Test Alpha"),)


# ---------------------------------------------------------------------------
# Identity blocking
# ---------------------------------------------------------------------------


def test_a_renamed_account_is_blocked_from_being_mentioned(db) -> None:
    sem_id = seed_semester(db)
    seed_groups(db)
    member_id = seed_member(db, "test-alpha", "Test Alpha")
    link(db, member_id, "user-1", "Test Alpha")
    event_id = seed_event(db, sem_id, "Sample Mixer", FRIDAY)
    assign(db, event_id, member_id, "door")

    present = [account("user-1", "Someone Else Entirely")]
    plan = announce_svc.build_plan(
        db,
        semester_id=sem_id,
        on_or_after=FRIDAY,
        on_or_before=FRIDAY,
        present=present,
    )
    post = plan.posts[0]
    assert post.mentions == ()
    assert "@" not in post.text.split("\n", 1)[1]
    assert post.unlinked[0].reason.startswith(identity_svc.BLOCK_DRIFTED)
    assert plan.identity_check == "full"


def test_a_renamed_account_is_blocked_from_being_removed(db) -> None:
    """Not knowing who an id belongs to is a hard stop on removal, not a nudge."""
    sem_id = seed_semester(db)
    seed_groups(db)
    member_id = seed_member(db, "test-alpha", "Test Alpha")
    link(db, member_id, "user-1", "Test Alpha")
    plan = membership_svc.build_plan(
        db,
        semester_id=sem_id,
        on_or_after=FRIDAY,
        on_or_before=FRIDAY,
        present=[account("user-1", "Someone Else Entirely")],
        now=datetime(2026, 9, 4, 12, 0),
    )
    assert plan.remove == ()
    assert [b.reason for b in plan.blocked] == [identity_svc.BLOCK_DRIFTED]


def test_two_links_sharing_a_nickname_block_each_other(db) -> None:
    sem_id = seed_semester(db)
    seed_groups(db)
    alpha = seed_member(db, "test-alpha-one", "Test Alpha One")
    bravo = seed_member(db, "test-alpha-two", "Test Alpha Two")
    link(db, alpha, "user-1", "Test Alpha")
    link(db, bravo, "user-2", "Test Alpha")
    event_id = seed_event(db, sem_id, "Sample Mixer", FRIDAY)
    assign(db, event_id, alpha, "door")
    assign(db, event_id, bravo, "bar")

    plan = announce_svc.build_plan(
        db, semester_id=sem_id, on_or_after=FRIDAY, on_or_before=FRIDAY
    )
    post = plan.posts[0]
    assert post.mentions == ()
    assert len(post.unlinked) == 2
    assert all(
        u.reason.startswith(identity_svc.BLOCK_DUPLICATE_NICKNAME) for u in post.unlinked
    )


def test_a_departed_account_is_blocked_from_being_mentioned(db) -> None:
    sem_id = seed_semester(db)
    seed_groups(db)
    member_id = seed_member(db, "test-alpha", "Test Alpha")
    link(db, member_id, "user-1", "Test Alpha")
    event_id = seed_event(db, sem_id, "Sample Mixer", FRIDAY)
    assign(db, event_id, member_id, "door")
    plan = announce_svc.build_plan(
        db, semester_id=sem_id, on_or_after=FRIDAY, on_or_before=FRIDAY, present=[]
    )
    assert plan.posts[0].mentions == ()
    assert plan.posts[0].unlinked[0].reason.startswith(identity_svc.BLOCK_NOT_IN_GROUP)


def test_not_being_in_the_group_does_not_block_being_added_to_it(db) -> None:
    """Otherwise nobody could ever be added — the block would eat its own trigger."""
    sem_id = seed_semester(db)
    seed_groups(db)
    member_id = seed_member(db, "test-alpha", "Test Alpha")
    link(db, member_id, "user-1", "Test Alpha")
    event_id = seed_event(db, sem_id, "Sample Mixer", FRIDAY)
    assign(db, event_id, member_id, "door")
    plan = membership_svc.build_plan(
        db,
        semester_id=sem_id,
        on_or_after=FRIDAY,
        on_or_before=FRIDAY,
        present=[],
        now=datetime(2026, 9, 1, 12, 0),
    )
    assert [a.display_name for a in plan.add] == ["Test Alpha"]
    assert plan.blocked == ()


def test_a_rename_is_never_accepted_automatically(db) -> None:
    """There is no code path that refreshes a cached nickname on its own."""
    seed_semester(db)
    member_id = seed_member(db, "test-alpha", "Test Alpha")
    link(db, member_id, "user-1", "Test Alpha")
    present = [account("user-1", "Renamed Entirely")]

    identity_svc.blocked_identities(db, present=present)
    identity_svc.plan_for_roster(db, present)

    stored = identities_repo.get_for_member(db, member_id)
    assert stored is not None
    assert stored.nickname == "Test Alpha"


def test_a_human_confirming_a_rename_unblocks_it_and_records_confirmed(db) -> None:
    seed_semester(db)
    member_id = seed_member(db, "test-alpha", "Test Alpha")
    link(db, member_id, "user-1", "Test Alpha")
    present = [account("user-1", "Renamed Entirely")]
    assert identity_svc.blocked_identities(db, present=present)

    with transaction(db):
        identity_svc.confirm_rename(
            db, member_id=member_id, new_nickname="Renamed Entirely", linked_at="2026-09-01"
        )
    assert identity_svc.blocked_identities(db, present=present) == {}
    stored = identities_repo.get_for_member(db, member_id)
    assert stored is not None
    assert stored.confidence == "confirmed"


def test_an_offline_preview_says_which_check_it_made(db) -> None:
    sem_id = seed_semester(db)
    seed_groups(db)
    plan = announce_svc.build_plan(
        db, semester_id=sem_id, on_or_after=FRIDAY, on_or_before=FRIDAY
    )
    assert plan.identity_check == "database-only"


# ---------------------------------------------------------------------------
# The send-once ledger
# ---------------------------------------------------------------------------


def _one_post_plan(db, sem_id: int):
    member_id = seed_member(db, "test-alpha", "Test Alpha")
    link(db, member_id, "user-1", "Test Alpha")
    event_id = seed_event(db, sem_id, "Sample Mixer", FRIDAY)
    assign(db, event_id, member_id, "door")
    return announce_svc.build_plan(
        db, semester_id=sem_id, on_or_after=FRIDAY, on_or_before=FRIDAY
    )


def test_the_same_announcement_is_never_posted_twice(db) -> None:
    """The headline guarantee: a double click does not notify sixty people twice."""
    sem_id = seed_semester(db)
    seed_groups(db)
    plan = _one_post_plan(db, sem_id)
    client = StubClient()

    first = outbound_svc.post_announcements(db, client, plan.posts, confirm=True)
    second = outbound_svc.post_announcements(db, client, plan.posts, confirm=True)

    assert [p.outcome for p in first] == ["sent"]
    assert [p.outcome for p in second] == ["already_sent"]
    assert len(client.posted) == 1


def test_the_ledger_row_is_reserved_before_the_call(db) -> None:
    """The reservation must be durable even when the send never returns."""
    sem_id = seed_semester(db)
    seed_groups(db)
    plan = _one_post_plan(db, sem_id)
    client = StubClient()
    client.raise_on_post = GroupMeAmbiguousError("connection reset")

    results = outbound_svc.post_announcements(db, client, plan.posts, confirm=True)
    assert [r.outcome for r in results] == ["unknown"]
    rows = ledger_repo.list_for_event(db, plan.posts[0].event_id)
    assert [r.state for r in rows] == ["unknown"]
    assert rows[0].settled_at is None


def test_an_unknown_row_blocks_a_blind_retry(db) -> None:
    sem_id = seed_semester(db)
    seed_groups(db)
    plan = _one_post_plan(db, sem_id)
    client = StubClient()
    client.raise_on_post = GroupMeAmbiguousError("timed out")
    outbound_svc.post_announcements(db, client, plan.posts, confirm=True)

    client.raise_on_post = None
    retried = outbound_svc.post_announcements(db, client, plan.posts, confirm=True)
    assert [r.outcome for r in retried] == ["blocked"]
    assert "reconcile" in (retried[0].detail or "")
    assert client.posted == []


def test_a_definite_failure_is_retryable(db) -> None:
    """A 4xx means it did not post, so the same content may be tried again."""
    sem_id = seed_semester(db)
    seed_groups(db)
    plan = _one_post_plan(db, sem_id)
    client = StubClient()
    client.raise_on_post = GroupMeError("400 bad request")
    with pytest.raises(GroupMeError):
        outbound_svc.post_announcements(db, client, plan.posts, confirm=True)
    assert [r.state for r in ledger_repo.list_for_event(db, plan.posts[0].event_id)] == ["failed"]

    client.raise_on_post = None
    again = outbound_svc.post_announcements(db, client, plan.posts, confirm=True)
    assert [r.outcome for r in again] == ["sent"]
    assert len(client.posted) == 1


def test_changing_the_roster_makes_a_genuinely_new_announcement(db) -> None:
    sem_id = seed_semester(db)
    seed_groups(db)
    plan = _one_post_plan(db, sem_id)
    client = StubClient()
    outbound_svc.post_announcements(db, client, plan.posts, confirm=True)

    bravo = seed_member(db, "test-bravo", "Test Bravo")
    link(db, bravo, "user-2", "Test Bravo")
    assign(db, plan.posts[0].event_id, bravo, "bar")
    changed = announce_svc.build_plan(
        db, semester_id=sem_id, on_or_after=FRIDAY, on_or_before=FRIDAY
    )
    assert changed.posts[0].content_version != plan.posts[0].content_version

    results = outbound_svc.post_announcements(db, client, changed.posts, confirm=True)
    assert [r.outcome for r in results] == ["sent"]
    assert len(client.posted) == 2


def test_the_persisted_source_guid_is_reused_across_a_retry(db) -> None:
    sem_id = seed_semester(db)
    seed_groups(db)
    plan = _one_post_plan(db, sem_id)
    client = StubClient()
    client.raise_on_post = GroupMeError("400")
    with pytest.raises(GroupMeError):
        outbound_svc.post_announcements(db, client, plan.posts, confirm=True)
    first_guid = ledger_repo.list_for_event(db, plan.posts[0].event_id)[0].source_guid

    client.raise_on_post = None
    outbound_svc.post_announcements(db, client, plan.posts, confirm=True)
    assert client.posted[0]["source_guid"] == first_guid


def test_reconcile_settles_an_unknown_row_from_the_chat_itself(db) -> None:
    """Evidence, not a timeout heuristic: GroupMe echoes the source_guid."""
    sem_id = seed_semester(db)
    seed_groups(db)
    plan = _one_post_plan(db, sem_id)
    client = StubClient()
    client.raise_on_post = GroupMeAmbiguousError("reset")
    outbound_svc.post_announcements(db, client, plan.posts, confirm=True)
    row = ledger_repo.list_for_event(db, plan.posts[0].event_id)[0]

    client.messages = [
        InboundMessage(
            message_id="msg-real",
            group_id="topic-placeholder-5",
            sender_user_id="u",
            sender_name="Test Alpha",
            text=plan.posts[0].text,
            created_at=1_756_000_000,
            source_guid=row.source_guid,
        )
    ]
    resolved = outbound_svc.reconcile(db, client)
    assert [r.resolved_to for r in resolved] == ["sent"]
    assert ledger_repo.list_for_event(db, plan.posts[0].event_id)[0].state == "sent"

    client.raise_on_post = None
    again = outbound_svc.post_announcements(db, client, plan.posts, confirm=True)
    assert [r.outcome for r in again] == ["already_sent"]


def test_reconcile_marks_a_missing_guid_as_failed_and_therefore_retryable(db) -> None:
    sem_id = seed_semester(db)
    seed_groups(db)
    plan = _one_post_plan(db, sem_id)
    client = StubClient()
    client.raise_on_post = GroupMeAmbiguousError("reset")
    outbound_svc.post_announcements(db, client, plan.posts, confirm=True)

    client.messages = []
    resolved = outbound_svc.reconcile(db, client)
    assert [r.resolved_to for r in resolved] == ["failed"]

    client.raise_on_post = None
    again = outbound_svc.post_announcements(db, client, plan.posts, confirm=True)
    assert [r.outcome for r in again] == ["sent"]


def test_posting_without_confirm_is_refused(db) -> None:
    sem_id = seed_semester(db)
    seed_groups(db)
    plan = _one_post_plan(db, sem_id)
    with pytest.raises(ValueError, match="confirm"):
        outbound_svc.post_announcements(db, StubClient(), plan.posts, confirm=False)


def test_membership_apply_without_confirm_is_refused(db) -> None:
    sem_id = seed_semester(db)
    seed_groups(db)
    plan = membership_svc.build_plan(
        db,
        semester_id=sem_id,
        on_or_after=FRIDAY,
        on_or_before=FRIDAY,
        present=[],
        now=datetime(2026, 9, 1, 12, 0),
    )
    with pytest.raises(ValueError, match="confirm"):
        outbound_svc.apply_membership(db, StubClient(), plan, confirm=False)


def test_an_oversize_post_cannot_be_sent_even_if_it_reaches_the_sender(db) -> None:
    sem_id = seed_semester(db)
    seed_groups(db)
    member_id = seed_member(db, "test-alpha", "Test Alpha")
    link(db, member_id, "user-1", "Test Alpha")
    event_id = seed_event(db, sem_id, "S" * 990, FRIDAY)
    assign(db, event_id, member_id, "door")
    plan = announce_svc.build_plan(
        db, semester_id=sem_id, on_or_after=FRIDAY, on_or_before=FRIDAY
    )
    with pytest.raises(ValueError, match="1000"):
        outbound_svc.post_announcements(db, StubClient(), plan.oversize, confirm=True)
