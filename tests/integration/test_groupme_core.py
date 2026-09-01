"""GroupMe end-to-end against a real SQLite file, with a stubbed API client.

No test in this file reaches the network, the macOS keychain, or the chair's
real database. Every name is invented and every id is a placeholder string.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from risk.db.connection import connect, transaction
from risk.db.schema import ensure_schema
from risk.repos import events as events_repo
from risk.repos import groupme_groups as groups_repo
from risk.repos import groupme_identities as identities_repo
from risk.repos import groupme_permanent_members as permanent_repo
from risk.services import groupme_announce as announce_svc
from tests.integration._groupme_helpers import (
    STAMP,
    assign,
    link,
    seed_event,
    seed_groups,
    seed_member,
    seed_semester,
    seed_setup_group,
    seed_setup_topics,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_the_migration_replays_safely_with_data_in_the_tables(tmp_path: Path) -> None:
    """``ensure_schema`` re-runs every migration on every connect."""
    path = tmp_path / "replay.db"
    conn = connect(path)
    ensure_schema(conn)
    seed_semester(conn)
    member_id = seed_member(conn, "test-alpha", "Test Alpha")
    seed_groups(conn)
    seed_setup_group(conn)
    link(conn, member_id, "user-1", "Test Alpha")
    with transaction(conn):
        permanent_repo.add(
            conn, group_slug=groups_repo.SETUP_GROUP_SLUG, member_id=member_id
        )
    conn.close()

    reopened = connect(path)
    ensure_schema(reopened)
    assert identities_repo.get_for_member(reopened, member_id) is not None
    assert len(groups_repo.list_topics(reopened)) == 2
    assert permanent_repo.member_ids_for_group(
        reopened, groups_repo.SETUP_GROUP_SLUG
    ) == frozenset({member_id})
    reopened.close()


def test_one_groupme_account_cannot_be_linked_to_two_members(db) -> None:
    seed_semester(db)
    a = seed_member(db, "test-alpha", "Test Alpha")
    b = seed_member(db, "test-bravo", "Test Bravo")
    link(db, a, "user-1", "Test Alpha")
    with pytest.raises(sqlite3.IntegrityError), transaction(db):
        identities_repo.link(
            db,
            member_id=b,
            groupme_user_id="user-1",
            nickname="Test Bravo",
            confidence="exact",
            linked_at=STAMP,
        )


def test_a_member_can_be_repointed_at_a_different_account(db) -> None:
    """A correction is fine; moving an ACCOUNT between members is not."""
    seed_semester(db)
    a = seed_member(db, "test-alpha", "Test Alpha")
    link(db, a, "user-1", "Test Alpha")
    link(db, a, "user-2", "Test Alpha")
    identity = identities_repo.get_for_member(db, a)
    assert identity is not None
    assert identity.groupme_user_id == "user-2"


def test_two_topics_cannot_claim_the_same_weekday(db) -> None:
    """Without this, `fetchone()` silently picks one of two Friday topics."""
    seed_groups(db)
    with pytest.raises(sqlite3.IntegrityError), transaction(db):
        groups_repo.upsert(
            db,
            slug="risk-friday-old",
            groupme_id="another-placeholder",
            label="Old Friday",
            parent_slug="risk-parent",
            weekday=5,
        )


def test_two_slugs_cannot_point_at_the_same_chat(db) -> None:
    seed_groups(db)
    with pytest.raises(sqlite3.IntegrityError), transaction(db):
        groups_repo.upsert(
            db, slug="risk-duplicate", groupme_id="parent-placeholder", label="Dupe"
        )


def test_a_blank_groupme_id_is_refused(db) -> None:
    with pytest.raises(ValueError, match="GroupMe id"), transaction(db):
        groups_repo.upsert(db, slug="risk-parent", groupme_id="   ", label="Risk")


@pytest.mark.parametrize("weekday", [0, 8, -1])
def test_a_weekday_outside_one_to_seven_is_refused(db, weekday: int) -> None:
    """1=Mon..7=Sun, matching ``date.isoweekday()``. 0 is not Monday here."""
    seed_groups(db)
    with pytest.raises(ValueError, match="1 \\(Mon\\) through 7"), transaction(db):
        groups_repo.upsert(
            db,
            slug="risk-bad",
            groupme_id="bad-placeholder",
            label="Bad",
            parent_slug="risk-parent",
            weekday=weekday,
        )


def test_several_non_topic_rows_may_share_a_null_weekday(db) -> None:
    with transaction(db):
        groups_repo.upsert(db, slug="risk-parent", groupme_id="p1", label="Risk")
        groups_repo.upsert(db, slug="roster-source", groupme_id="p2", label="Announcements")
    assert len(groups_repo.list_all(db)) == 2


@pytest.mark.parametrize(
    ("weekday", "event_date"),
    [
        (1, "2026-08-31"),  # Monday
        (2, "2026-09-01"),
        (3, "2026-09-02"),
        (4, "2026-09-03"),
        (5, "2026-09-04"),
        (6, "2026-09-05"),
        (7, "2026-09-06"),  # Sunday
    ],
)
def test_every_weekday_routes_to_its_own_topic(db, weekday: int, event_date: str) -> None:
    """All seven, so an off-by-one between weekday() and isoweekday() shows up."""
    sem_id = seed_semester(db)
    with transaction(db):
        groups_repo.upsert(db, slug="risk-parent", groupme_id="parent-placeholder", label="Risk")
        groups_repo.upsert(
            db,
            slug=f"risk-day-{weekday}",
            groupme_id=f"topic-placeholder-{weekday}",
            label=f"Day {weekday}",
            parent_slug="risk-parent",
            weekday=weekday,
        )
    member_id = seed_member(db, "test-alpha", "Test Alpha")
    link(db, member_id, "user-1", "Test Alpha")
    event_id = seed_event(db, sem_id, "Sample Party", event_date)
    assign(db, event_id, member_id, "door")

    plan = announce_svc.build_plan(
        db, semester_id=sem_id, on_or_after=event_date, on_or_before=event_date
    )
    assert [p.group_slug for p in plan.posts] == [f"risk-day-{weekday}"]
    assert plan.unroutable == ()


def test_an_event_on_a_day_with_no_topic_is_unroutable_not_redirected(db) -> None:
    """A Monday dage must NOT fall back to the parent group."""
    sem_id = seed_semester(db)
    seed_groups(db)
    member_id = seed_member(db, "test-alpha", "Test Alpha")
    link(db, member_id, "user-1", "Test Alpha")
    event_id = seed_event(db, sem_id, "Sample Dage", "2026-08-31")  # a Monday
    assign(db, event_id, member_id, "door")

    plan = announce_svc.build_plan(
        db, semester_id=sem_id, on_or_after="2026-08-31", on_or_before="2026-08-31"
    )
    assert plan.posts == ()
    assert [u.event_name for u in plan.unroutable] == ["Sample Dage"]
    assert "Mon" in plan.unroutable[0].reason


# ---------------------------------------------------------------------------
# Announce plan
# ---------------------------------------------------------------------------


def test_party_night_and_setup_crews_split_into_two_posts_by_the_party_day(db) -> None:
    """Friday's cleanup stays labelled Friday even though it is worked Saturday."""
    sem_id = seed_semester(db)
    seed_groups(db)
    seed_setup_group(db)
    alpha = seed_member(db, "test-alpha", "Test Alpha")
    bravo = seed_member(db, "test-bravo", "Test Bravo")
    charlie = seed_member(db, "test-charlie", "Test Charlie")
    link(db, alpha, "user-1", "Test Alpha")
    link(db, bravo, "user-2", "Test Bravo")
    link(db, charlie, "user-3", "Test Charlie")
    event_id = seed_event(db, sem_id, "Sample Mixer", "2026-09-04")  # Friday
    assign(db, event_id, alpha, "door")
    assign(db, event_id, bravo, "setup")
    assign(db, event_id, charlie, "cleanup")

    plan = announce_svc.build_plan(
        db, semester_id=sem_id, on_or_after="2026-09-01", on_or_before="2026-09-14"
    )
    assert [post.group_slug for post in plan.posts] == ["risk-friday", "setup-cleanup"]
    night, crew = plan.posts
    assert night.text == (
        "friday, sep 4\n@Test Alpha: door"
    )
    assert crew.text == (
        "friday, sep 4\n"
        "@Test Bravo: setup\n"
        "@Test Charlie: cleanup"
    )
    assert plan.unroutable == ()


def test_the_crew_post_goes_to_the_setup_groups_weekday_topic_when_one_exists(db) -> None:
    """Same weekday-topic treatment the party half already had.

    Before this, crews for every party of the week landed in one shared chat,
    so a Tuesday setup man was notified about Saturday's crew and read four
    posts a week to find his own.
    """
    sem_id = seed_semester(db)
    seed_groups(db)
    seed_setup_group(db)
    seed_setup_topics(db)  # Friday only
    alpha = seed_member(db, "test-alpha", "Test Alpha")
    bravo = seed_member(db, "test-bravo", "Test Bravo")
    link(db, alpha, "user-1", "Test Alpha")
    link(db, bravo, "user-2", "Test Bravo")
    event_id = seed_event(db, sem_id, "Sample Mixer", "2026-09-04")  # Friday
    assign(db, event_id, alpha, "door")
    assign(db, event_id, bravo, "setup")

    plan = announce_svc.build_plan(
        db, semester_id=sem_id, on_or_after="2026-09-04", on_or_before="2026-09-04"
    )

    assert [post.group_slug for post in plan.posts] == ["risk-friday", "setup-friday"]
    assert plan.unroutable == ()


def test_a_weekday_with_no_setup_topic_still_posts_to_the_setup_group(db) -> None:
    """The topics are OPTIONAL. A missing one is a quieter destination, not a
    dropped crew — unlike the party half, where the parent chat is the wrong
    audience and silence is correct."""
    sem_id = seed_semester(db)
    seed_groups(db, days={2: "risk-tuesday", 5: "risk-friday"})
    seed_setup_group(db)
    seed_setup_topics(db, days={5: "setup-friday"})  # no Tuesday topic
    member_id = seed_member(db, "test-alpha", "Test Alpha")
    link(db, member_id, "user-1", "Test Alpha")
    event_id = seed_event(db, sem_id, "Sample Mixer", "2026-09-01")  # Tuesday
    assign(db, event_id, member_id, "setup")

    plan = announce_svc.build_plan(
        db, semester_id=sem_id, on_or_after="2026-09-01", on_or_before="2026-09-01"
    )

    assert [post.group_slug for post in plan.posts] == ["setup-cleanup"]
    assert plan.unroutable == ()


def test_no_non_party_night_crew_means_no_setup_group_post(db) -> None:
    sem_id = seed_semester(db)
    seed_groups(db)
    seed_setup_group(db)
    member_id = seed_member(db, "test-alpha", "Test Alpha")
    link(db, member_id, "user-1", "Test Alpha")
    event_id = seed_event(db, sem_id, "Sample Mixer", "2026-09-04")
    assign(db, event_id, member_id, "door")

    plan = announce_svc.build_plan(
        db, semester_id=sem_id, on_or_after="2026-09-04", on_or_before="2026-09-04"
    )

    assert [post.group_slug for post in plan.posts] == ["risk-friday"]


def test_an_unregistered_setup_group_reports_only_that_crew_unroutable(db) -> None:
    sem_id = seed_semester(db)
    seed_groups(db)
    alpha = seed_member(db, "test-alpha", "Test Alpha")
    bravo = seed_member(db, "test-bravo", "Test Bravo")
    link(db, alpha, "user-1", "Test Alpha")
    link(db, bravo, "user-2", "Test Bravo")
    event_id = seed_event(db, sem_id, "Sample Mixer", "2026-09-04")
    assign(db, event_id, alpha, "door")
    assign(db, event_id, bravo, "cleanup")

    plan = announce_svc.build_plan(
        db, semester_id=sem_id, on_or_after="2026-09-04", on_or_before="2026-09-04"
    )

    assert [post.group_slug for post in plan.posts] == ["risk-friday"]
    assert len(plan.unroutable) == 1
    assert "setup-cleanup" in plan.unroutable[0].reason
    assert "seed" in plan.unroutable[0].reason


def test_routing_uses_the_window_flag_not_shift_type_names(db) -> None:
    sem_id = seed_semester(db)
    seed_groups(db)
    seed_setup_group(db)
    alpha = seed_member(db, "test-alpha", "Test Alpha")
    bravo = seed_member(db, "test-bravo", "Test Bravo")
    link(db, alpha, "user-1", "Test Alpha")
    link(db, bravo, "user-2", "Test Bravo")
    event_id = seed_event(db, sem_id, "Sample Mixer", "2026-09-04")
    assign(db, event_id, alpha, "door")
    assign(db, event_id, bravo, "setup")
    with transaction(db):
        db.execute(
            """
            UPDATE shift_type_windows
            SET occupies_event_night = CASE
              WHEN shift_type_id = (SELECT id FROM shift_types WHERE slug = 'door') THEN 0
              WHEN shift_type_id = (SELECT id FROM shift_types WHERE slug = 'setup') THEN 1
              ELSE occupies_event_night
            END
            """
        )

    plan = announce_svc.build_plan(
        db, semester_id=sem_id, on_or_after="2026-09-04", on_or_before="2026-09-04"
    )

    by_slug = {post.group_slug: post.text for post in plan.posts}
    assert "@Test Bravo: setup" in by_slug["risk-friday"]
    assert "@Test Alpha: door" in by_slug["setup-cleanup"]


def test_an_event_with_nobody_assigned_produces_no_post(db) -> None:
    sem_id = seed_semester(db)
    seed_groups(db)
    seed_event(db, sem_id, "Sample Mixer", "2026-09-01")
    plan = announce_svc.build_plan(
        db, semester_id=sem_id, on_or_after="2026-09-01", on_or_before="2026-09-14"
    )
    assert plan.posts == ()


def test_a_cancelled_event_is_never_announced(db) -> None:
    sem_id = seed_semester(db)
    seed_groups(db)
    member_id = seed_member(db, "test-alpha", "Test Alpha")
    link(db, member_id, "user-1", "Test Alpha")
    event_id = seed_event(db, sem_id, "Sample Mixer", "2026-09-01")
    assign(db, event_id, member_id, "door")
    with transaction(db):
        events_repo.update_status(db, event_id=event_id, status="cancelled")
    plan = announce_svc.build_plan(
        db, semester_id=sem_id, on_or_after="2026-09-01", on_or_before="2026-09-14"
    )
    assert plan.posts == ()


def test_an_oversize_post_is_separated_at_preview_time(db) -> None:
    """Caught in the rendered body before send, even when a nickname is huge."""
    sem_id = seed_semester(db)
    seed_groups(db)
    event_id = seed_event(db, sem_id, "Sample Mixer", "2026-09-01")
    member_id = seed_member(db, "test-alpha", "Test Alpha")
    link(db, member_id, "user-1", "T" * 990)
    assign(db, event_id, member_id, "door")

    plan = announce_svc.build_plan(
        db, semester_id=sem_id, on_or_after="2026-09-01", on_or_before="2026-09-14"
    )
    assert plan.posts == ()
    assert len(plan.oversize) == 1
    assert plan.oversize[0].too_long
    assert plan.oversize[0].char_count > announce_svc.MAX_MESSAGE_CHARS


def test_an_unlinked_member_is_named_without_an_at_and_reported(db) -> None:
    sem_id = seed_semester(db)
    seed_groups(db)
    alpha = seed_member(db, "test-alpha", "Test Alpha")
    bravo = seed_member(db, "test-bravo", "Test Bravo")
    link(db, alpha, "user-1", "Test Alpha")
    event_id = seed_event(db, sem_id, "Sample Mixer", "2026-09-01")
    assign(db, event_id, alpha, "door")
    assign(db, event_id, bravo, "bar")

    plan = announce_svc.build_plan(
        db, semester_id=sem_id, on_or_after="2026-09-01", on_or_before="2026-09-14"
    )
    post = plan.posts[0]
    assert "@Test Alpha: door" in post.text
    assert "Test Bravo: bar" in post.text
    assert "@Test Bravo" not in post.text
    assert [u.display_name for u in post.unlinked] == ["Test Bravo"]


# ---------------------------------------------------------------------------
# Unreached — who the posts named but did not notify
# ---------------------------------------------------------------------------


def _rosters(**by_slug: set[str]) -> dict[str, set[str]]:
    return dict(by_slug)


def test_a_man_in_the_destination_group_is_not_reported_as_unreached(db) -> None:
    sem_id = seed_semester(db)
    seed_groups(db)
    seed_setup_group(db)
    alpha = seed_member(db, "test-alpha", "Test Alpha")
    link(db, alpha, "user-1", "Test Alpha")
    event_id = seed_event(db, sem_id, "Sample Mixer", "2026-09-04")
    assign(db, event_id, alpha, "door")

    plan = announce_svc.build_plan(
        db, semester_id=sem_id, on_or_after="2026-09-04", on_or_before="2026-09-04"
    )
    people = announce_svc.unreached_members(
        db, plan, rosters=_rosters(**{"risk-friday": {"user-1"}})
    )

    assert people == ()


def test_a_mention_of_someone_outside_the_destination_group_notifies_nobody(db) -> None:
    """The message still READS correctly, which is exactly the danger: the
    chapter sees him tagged and assumes he was told."""
    sem_id = seed_semester(db)
    seed_groups(db)
    seed_setup_group(db)
    alpha = seed_member(db, "test-alpha", "Test Alpha")
    link(db, alpha, "user-1", "Test Alpha")
    event_id = seed_event(db, sem_id, "Sample Mixer", "2026-09-04")
    assign(db, event_id, alpha, "door")

    plan = announce_svc.build_plan(
        db, semester_id=sem_id, on_or_after="2026-09-04", on_or_before="2026-09-04"
    )
    people = announce_svc.unreached_members(db, plan, rosters=_rosters(**{"risk-friday": set()}))

    assert [(p.display_name, p.groupme_user_id) for p in people] == [("Test Alpha", "user-1")]
    assert "not a member of 'risk-friday'" in people[0].reason
    assert people[0].shifts == (("2026-09-04", "door"),)


def test_a_man_with_no_groupme_link_at_all_is_reported_with_no_user_id(db) -> None:
    sem_id = seed_semester(db)
    seed_groups(db)
    seed_setup_group(db)
    alpha = seed_member(db, "test-alpha", "Test Alpha")
    bravo = seed_member(db, "test-bravo", "Test Bravo")
    link(db, alpha, "user-1", "Test Alpha")
    event_id = seed_event(db, sem_id, "Sample Mixer", "2026-09-04")
    assign(db, event_id, alpha, "door")
    assign(db, event_id, bravo, "bar")

    plan = announce_svc.build_plan(
        db, semester_id=sem_id, on_or_after="2026-09-04", on_or_before="2026-09-04"
    )
    people = announce_svc.unreached_members(
        db, plan, rosters=_rosters(**{"risk-friday": {"user-1"}})
    )

    assert [(p.display_name, p.groupme_user_id) for p in people] == [("Test Bravo", None)]


def test_a_topic_routed_crew_post_is_checked_against_the_crew_not_the_party(db) -> None:
    """The half a post belongs to is its destination's PARENT, not its slug.

    Comparing the slug to ``setup-cleanup`` was right while crews had one chat.
    The moment they route to a weekday topic that test is False for every crew
    post, and each one gets checked against the party-night list instead — so
    the door man is reported unreached from a chat he was never posted in, and
    the setup man's real absence goes unreported. Both halves wrong at once.
    """
    sem_id = seed_semester(db)
    seed_groups(db)
    seed_setup_group(db)
    seed_setup_topics(db)  # Friday
    alpha = seed_member(db, "test-alpha", "Test Alpha")
    bravo = seed_member(db, "test-bravo", "Test Bravo")
    link(db, alpha, "user-1", "Test Alpha")
    link(db, bravo, "user-2", "Test Bravo")
    event_id = seed_event(db, sem_id, "Sample Mixer", "2026-09-04")
    assign(db, event_id, alpha, "door")
    assign(db, event_id, bravo, "setup")

    plan = announce_svc.build_plan(
        db, semester_id=sem_id, on_or_after="2026-09-04", on_or_before="2026-09-04"
    )
    assert [post.group_slug for post in plan.posts] == ["risk-friday", "setup-friday"]

    # Alpha is in the Risk topic he was posted to. Bravo is missing from the
    # setup topic he was posted to. Only Bravo is unreached.
    people = announce_svc.unreached_members(
        db,
        plan,
        rosters=_rosters(**{"risk-friday": {"user-1"}, "setup-friday": set()}),
    )

    assert [p.display_name for p in people] == ["Test Bravo"]
    assert people[0].shifts == (("2026-09-04", "setup"),)


def test_one_mans_several_missed_shifts_become_one_message(db) -> None:
    """Four separate texts for four shifts is how a man stops reading them."""
    sem_id = seed_semester(db)
    seed_groups(db)
    seed_setup_group(db)
    alpha = seed_member(db, "test-alpha", "Test Alpha")
    link(db, alpha, "user-1", "Test Alpha")
    first = seed_event(db, sem_id, "Sample Mixer", "2026-09-01")  # Tuesday
    second = seed_event(db, sem_id, "Other Mixer", "2026-09-04")  # Friday
    assign(db, first, alpha, "door")
    assign(db, second, alpha, "bar")

    plan = announce_svc.build_plan(
        db, semester_id=sem_id, on_or_after="2026-09-01", on_or_before="2026-09-04"
    )
    people = announce_svc.unreached_members(
        db, plan, rosters=_rosters(**{"risk-tuesday": set(), "risk-friday": set()})
    )

    assert len(people) == 1
    assert people[0].shifts == (("2026-09-01", "door"), ("2026-09-04", "bar"))
    assert announce_svc.format_direct_message(people[0].shifts) == (
        "your shifts\n  - september 1st: door\n  - september 4th: bar"
    )


def test_a_destination_with_no_roster_supplied_reports_everyone_it_named(db) -> None:
    """A missing roster must not read as "everybody is in that group". The
    caller failed to look, and silence there is the failure this command
    exists to end."""
    sem_id = seed_semester(db)
    seed_groups(db)
    seed_setup_group(db)
    alpha = seed_member(db, "test-alpha", "Test Alpha")
    link(db, alpha, "user-1", "Test Alpha")
    event_id = seed_event(db, sem_id, "Sample Mixer", "2026-09-04")
    assign(db, event_id, alpha, "door")

    plan = announce_svc.build_plan(
        db, semester_id=sem_id, on_or_after="2026-09-04", on_or_before="2026-09-04"
    )
    people = announce_svc.unreached_members(db, plan, rosters={})

    assert [p.display_name for p in people] == ["Test Alpha"]
