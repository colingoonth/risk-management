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
from risk.repos import groupme_permanent_members as permanent_repo
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
    seed_setup_group,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Removal timing — shift WINDOW end, never the event date
# ---------------------------------------------------------------------------


def test_wednesday_removes_tuesdays_finished_door_rides_and_bar_crews(db) -> None:
    sem_id = seed_semester(db)
    seed_groups(db)
    event_id = seed_event(db, sem_id, "Test Tuesday Party", "2026-09-01")
    present = []
    for index, shift_type in enumerate(("door", "driver", "bar"), start=1):
        member_id = seed_member(db, f"test-night-{index}", f"Test Night {index}")
        user_id = f"user-night-{index}"
        link(db, member_id, user_id, f"Test Night {index}")
        assign(db, event_id, member_id, shift_type)
        present.append(account(user_id, f"Test Night {index}"))

    plan = membership_svc.build_plan(
        db,
        semester_id=sem_id,
        on_or_after="2026-09-02",
        on_or_before="2026-09-09",
        present=present,
        now=datetime(2026, 9, 2, 9, 0),
    )

    assert {member.display_name for member in plan.remove} == {
        "Test Night 1",
        "Test Night 2",
        "Test Night 3",
    }


def test_wednesday_keeps_tuesdays_cleanup_until_its_window_closes(db) -> None:
    sem_id = seed_semester(db)
    seed_groups(db)
    member_id = seed_member(db, "test-cleaner", "Test Cleaner")
    link(db, member_id, "user-cleaner", "Test Cleaner")
    event_id = seed_event(db, sem_id, "Test Tuesday Party", "2026-09-01")
    assign(db, event_id, member_id, "cleanup")

    plan = membership_svc.build_plan(
        db,
        semester_id=sem_id,
        on_or_after="2026-09-02",
        on_or_before="2026-09-09",
        present=[account("user-cleaner", "Test Cleaner")],
        now=datetime(2026, 9, 2, 9, 0),
    )

    assert plan.remove == ()


def test_wednesday_adds_the_following_tuesday_crew_including_setup(db) -> None:
    sem_id = seed_semester(db)
    seed_groups(db)
    event_id = seed_event(db, sem_id, "Test Next Tuesday Party", "2026-09-08")
    expected = set()
    for index, shift_type in enumerate(("driver", "door", "bar", "setup"), start=1):
        display_name = f"Test Upcoming {index}"
        member_id = seed_member(db, f"test-upcoming-{index}", display_name)
        link(db, member_id, f"user-upcoming-{index}", display_name)
        assign(db, event_id, member_id, shift_type)
        expected.add(display_name)

    plan = membership_svc.build_plan(
        db,
        semester_id=sem_id,
        on_or_after="2026-09-02",
        on_or_before="2026-09-09",
        present=[],
        now=datetime(2026, 9, 2, 9, 0),
    )

    added = {member.display_name for member in plan.add}
    assert added == expected
    assert "Test Upcoming 4" in added, "the setup worker must be added before setup starts"


def test_a_shift_three_weeks_away_does_not_prevent_removal(db) -> None:
    sem_id = seed_semester(db)
    seed_groups(db)
    member_id = seed_member(db, "test-distant", "Test Distant")
    link(db, member_id, "user-distant", "Test Distant")
    event_id = seed_event(db, sem_id, "Test Distant Party", "2026-09-23")
    assign(db, event_id, member_id, "door")

    plan = membership_svc.build_plan(
        db,
        semester_id=sem_id,
        on_or_after="2026-09-02",
        on_or_before="2026-09-09",
        present=[account("user-distant", "Test Distant")],
        now=datetime(2026, 9, 2, 9, 0),
    )

    assert [member.display_name for member in plan.remove] == ["Test Distant"]
    assert plan.remove[0].reason == "next shift is after membership horizon 2026-09-09"


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


def test_the_setup_chat_wants_its_own_crews_not_the_whole_roster(db) -> None:
    """The setup/cleanup chat is for the men who do NOT work the party night.

    Both plans read the same schedule, so without a filter they are the same
    plan and the setup chat fills with door, rides and DJ men who will never
    work a shift in it. The split is the same `occupies_event_night` flag the
    announcement routing uses, so a man is in a chat for the work he actually
    does there.
    """
    sem_id = seed_semester(db)
    seed_groups(db)
    sweeper = seed_member(db, "test-sweeper", "Test Sweeper")
    bouncer = seed_member(db, "test-bouncer", "Test Bouncer")
    link(db, sweeper, "user-sweep", "Test Sweeper")
    link(db, bouncer, "user-bounce", "Test Bouncer")
    event_id = seed_event(db, sem_id, "Test Party", FRIDAY)
    assign(db, event_id, sweeper, "cleanup")
    assign(db, event_id, bouncer, "door")

    kwargs = {
        "semester_id": sem_id,
        "on_or_after": FRIDAY,
        # Reaches Saturday because the horizon is now the SHIFT's start, and
        # this cleanup is worked Saturday morning. A same-day window would be
        # testing the horizon, not the split this test is about.
        "on_or_before": "2026-09-05",
        "present": [],
        "now": datetime(2026, 9, 4, 12, 0),
    }
    parent = membership_svc.build_plan(db, group_slug="risk-parent", **kwargs)
    setup = membership_svc.build_plan(db, group_slug="setup-cleanup", **kwargs)

    # The parent chat wants everyone working; the setup chat wants the crew
    # whose shift is not on the party night.
    assert {a.display_name for a in parent.add} == {"Test Sweeper", "Test Bouncer"}
    assert {a.display_name for a in setup.add} == {"Test Sweeper"}


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


def test_permanent_member_is_kept_and_reported_only_for_the_named_group(db) -> None:
    sem_id = seed_semester(db)
    seed_groups(db)
    seed_setup_group(db)
    member_id = seed_member(db, "test-alpha", "Test Alpha")
    link(db, member_id, "user-1", "Test Alpha")
    with transaction(db):
        permanent_repo.add(
            db, group_slug="setup-cleanup", member_id=member_id
        )

    setup_plan = membership_svc.build_plan(
        db,
        semester_id=sem_id,
        on_or_after=FRIDAY,
        on_or_before=FRIDAY,
        present=[account("user-1", "Test Alpha")],
        group_slug="setup-cleanup",
        now=datetime(2026, 9, 4, 12, 0),
    )

    assert setup_plan.remove == ()
    assert [member.display_name for member in setup_plan.permanent] == ["Test Alpha"]
    assert setup_plan.permanent[0].reason == "permanent member"
    assert "setup-cleanup" in setup_plan.permanent[0].detail
    assert setup_plan.hard_excluded == ()

    risk_plan = membership_svc.build_plan(
        db,
        semester_id=sem_id,
        on_or_after=FRIDAY,
        on_or_before=FRIDAY,
        present=[account("user-1", "Test Alpha")],
        now=datetime(2026, 9, 4, 12, 0),
    )
    assert [member.display_name for member in risk_plan.remove] == ["Test Alpha"]
    assert risk_plan.permanent == ()


def test_absent_permanent_member_is_added_without_needing_a_shift(db) -> None:
    sem_id = seed_semester(db)
    seed_groups(db)
    seed_setup_group(db)
    member_id = seed_member(db, "test-alpha", "Test Alpha")
    link(db, member_id, "user-1", "Test Alpha")
    with transaction(db):
        permanent_repo.add(db, group_slug="setup-cleanup", member_id=member_id)

    plan = membership_svc.build_plan(
        db,
        semester_id=sem_id,
        on_or_after=FRIDAY,
        on_or_before=FRIDAY,
        present=[],
        group_slug="setup-cleanup",
        now=datetime(2026, 9, 4, 12, 0),
    )

    assert [member.display_name for member in plan.add] == ["Test Alpha"]
    assert plan.remove == ()


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


def test_an_event_staying_in_the_rolling_window_is_not_announced_again(db) -> None:
    sem_id = seed_semester(db)
    seed_groups(db)
    member_id = seed_member(db, "test-alpha", "Test Alpha")
    link(db, member_id, "user-1", "Test Alpha")
    event_id = seed_event(db, sem_id, "Sample Mixer", "2026-09-08")
    assign(db, event_id, member_id, "driver")
    client = StubClient()

    wednesday = announce_svc.build_plan(
        db,
        semester_id=sem_id,
        on_or_after="2026-09-02",
        on_or_before="2026-09-09",
    )
    first = outbound_svc.post_announcements(db, client, wednesday.posts, confirm=True)

    thursday = announce_svc.build_plan(
        db,
        semester_id=sem_id,
        on_or_after="2026-09-03",
        on_or_before="2026-09-10",
    )
    second = outbound_svc.post_announcements(db, client, thursday.posts, confirm=True)

    assert [result.outcome for result in first] == ["sent"]
    assert [result.outcome for result in second] == ["already_sent"]
    assert len(client.posted) == 1
    rows = ledger_repo.list_for_event(db, event_id)
    assert [
        (row.event_id, row.destination_slug, row.content_version) for row in rows
    ] == [
        (
            event_id,
            wednesday.posts[0].group_slug,
            wednesday.posts[0].content_version,
        )
    ]


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
    link(db, member_id, "user-1", "T" * 990)
    event_id = seed_event(db, sem_id, "Sample Mixer", FRIDAY)
    assign(db, event_id, member_id, "door")
    plan = announce_svc.build_plan(
        db, semester_id=sem_id, on_or_after=FRIDAY, on_or_before=FRIDAY
    )
    with pytest.raises(ValueError, match="1000"):
        outbound_svc.post_announcements(db, StubClient(), plan.oversize, confirm=True)


# ---------------------------------------------------------------------------
# Applying — the add that GroupMe takes and does not perform
# ---------------------------------------------------------------------------


def _two_man_add_plan(db, sem_id: int):
    """Two men working Friday, neither in the group yet."""
    alpha = seed_member(db, "test-alpha", "Test Alpha")
    bravo = seed_member(db, "test-bravo", "Test Bravo")
    link(db, alpha, "user-1", "Test Alpha")
    link(db, bravo, "user-2", "Test Bravo")
    event_id = seed_event(db, sem_id, "Sample Mixer", FRIDAY)
    assign(db, event_id, alpha, "door")
    assign(db, event_id, bravo, "bar")
    return membership_svc.build_plan(
        db,
        semester_id=sem_id,
        on_or_after=FRIDAY,
        on_or_before=FRIDAY,
        present=[],
        now=datetime(2026, 9, 1, 12, 0),
    )


def test_an_add_the_api_accepts_and_does_not_perform_is_reported(db) -> None:
    """The request 202s either way, so nothing in the response says he is out.

    He then reads as a member of the group all week: the announcement mentions
    him, the text renders identically to everybody else's, and the only
    difference is a push notification that arrives on nobody's phone.
    """
    sem_id = seed_semester(db)
    seed_groups(db)
    plan = _two_man_add_plan(db, sem_id)
    client = StubClient()
    client.refuses_add = {"user-2"}

    result = outbound_svc.apply_membership(db, client, plan, confirm=True, sleep=lambda _: None)

    assert sorted(result.added) == ["Test Alpha", "Test Bravo"]
    assert [a.display_name for a in result.not_added] == ["Test Bravo"]


def test_adds_that_all_land_report_nobody_missing(db) -> None:
    sem_id = seed_semester(db)
    seed_groups(db)
    plan = _two_man_add_plan(db, sem_id)
    client = StubClient()

    result = outbound_svc.apply_membership(db, client, plan, confirm=True, sleep=lambda _: None)

    assert result.not_added == ()


def test_a_run_with_nothing_to_add_does_not_read_the_group_back(db) -> None:
    """The read-back exists to check the adds. With none queued it is a call
    that tells nobody anything, on a command the chair runs every week."""
    sem_id = seed_semester(db)
    seed_groups(db)
    member_id = seed_member(db, "test-alpha", "Test Alpha")
    link(db, member_id, "user-1", "Test Alpha")
    seed_event(db, sem_id, "Sample Mixer", FRIDAY)  # no shifts: nobody is needed
    plan = membership_svc.build_plan(
        db,
        semester_id=sem_id,
        on_or_after=FRIDAY,
        on_or_before=FRIDAY,
        present=[account("user-1", "Test Alpha")],
        now=datetime(2026, 9, 1, 12, 0),
    )
    assert plan.add == ()
    client = StubClient()

    result = outbound_svc.apply_membership(db, client, plan, confirm=True, sleep=lambda _: None)

    assert result.not_added == ()
    assert client.list_members_calls == 0


# ---------------------------------------------------------------------------
# The horizon is the shift's own start, not the party date
# ---------------------------------------------------------------------------


def test_a_crew_arrives_the_week_before_he_works_not_the_week_before_the_party(
    db,
) -> None:
    """Cleanup is worked the MORNING AFTER, so its crew arrives a day later.

    The chair's rule is that the chat holds a man for the week before his
    shift. Keying that off the party date puts the cleanup crew in a day early
    every single week — and puts setup, worked up to two days AHEAD of the
    party, in two days late.
    """
    sem_id = seed_semester(db)
    seed_groups(db)
    member_id = seed_member(db, "test-alpha", "Test Alpha")
    link(db, member_id, "user-1", "Test Alpha")
    event_id = seed_event(db, sem_id, "Sample Mixer", FRIDAY)  # party Friday
    assign(db, event_id, member_id, "cleanup")  # worked Saturday morning

    def plan_with_horizon(horizon: str):
        return membership_svc.build_plan(
            db,
            semester_id=sem_id,
            on_or_after=FRIDAY,
            on_or_before=horizon,
            present=[],
            now=datetime(2026, 8, 29, 12, 0),
        )

    # A horizon reaching the PARTY does not reach the shift.
    assert plan_with_horizon(FRIDAY).add == ()
    # One day further does, and that day is when he is due to work.
    assert [a.display_name for a in plan_with_horizon("2026-09-05").add] == ["Test Alpha"]


def test_setup_arrives_earlier_than_its_party_because_it_is_worked_earlier(db) -> None:
    """The same rule in the other direction — setup runs before the party."""
    sem_id = seed_semester(db)
    seed_groups(db)
    member_id = seed_member(db, "test-alpha", "Test Alpha")
    link(db, member_id, "user-1", "Test Alpha")
    event_id = seed_event(db, sem_id, "Sample Mixer", FRIDAY)
    assign(db, event_id, member_id, "setup")

    plan = membership_svc.build_plan(
        db,
        semester_id=sem_id,
        on_or_after=FRIDAY,
        # Stops two days short of the party, but the setup window has already
        # opened by then, so he belongs in the chat.
        on_or_before="2026-09-02",
        present=[],
        now=datetime(2026, 8, 29, 12, 0),
    )

    assert [a.display_name for a in plan.add] == ["Test Alpha"]


def test_a_slow_queue_is_waited_out_rather_than_called_a_refusal(db) -> None:
    """The add is asynchronous; reading the group back at once proves nothing.

    Measured against the real API: an instant read reported seven of seven adds
    as failed, and twenty seconds later ten of those fourteen were in. Reporting
    a refusal there is worse than reporting nothing, because it sends the chair
    off to hand-add men who are already in the group.
    """
    sem_id = seed_semester(db)
    seed_groups(db)
    plan = _two_man_add_plan(db, sem_id)
    client = StubClient()
    client.add_settles_after = 2  # not visible until the third read
    waits: list[float] = []

    result = outbound_svc.apply_membership(
        db, client, plan, confirm=True, sleep=waits.append
    )

    assert result.not_added == ()
    assert client.list_members_calls == 3
    assert waits, "it must actually wait between reads, not spin"


def test_a_man_who_never_appears_is_reported_after_a_bounded_wait(db) -> None:
    """The wait cannot be unbounded — this runs in front of the chair."""
    sem_id = seed_semester(db)
    seed_groups(db)
    plan = _two_man_add_plan(db, sem_id)
    client = StubClient()
    client.refuses_add = {"user-2"}
    waits: list[float] = []

    result = outbound_svc.apply_membership(
        db, client, plan, confirm=True, settle_attempts=3, sleep=waits.append
    )

    assert [a.display_name for a in result.not_added] == ["Test Bravo"]
    assert client.list_members_calls == 3
    assert len(waits) == 2
