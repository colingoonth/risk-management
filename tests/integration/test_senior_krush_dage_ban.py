"""Seniors do not stand door, bar or rides at a Krush or a Dage.

Colin's rule, 2026-08-26. Dan had asked for the Krush half in the scratch pad
three days earlier and it was honoured BY HAND, which is why it is a test now:
the hand-application survived one rebuild and was silently lost on the next.

Pinned on BOTH write paths. A rule the fill honours and `risk shift assign` does
not is a rule broken by the chair, who is the person who will be asked why.
"""

from __future__ import annotations

import sqlite3

import pytest

from risk.db.connection import transaction
from risk.repos import event_types as etypes_repo
from risk.repos import events as events_repo
from risk.repos import member_statuses as statuses_repo
from risk.repos import members as members_repo
from risk.repos import semesters as semesters_repo
from risk.services import assignment, manual_assign, policy, shift_requirements

pytestmark = pytest.mark.integration

NIGHT = ("door", "bar", "driver")


def _world(db: sqlite3.Connection, event_type: str) -> tuple[int, int]:
    sem_id = semesters_repo.insert(
        db, name="FA26", starts_on="2026-08-25", ends_on="2026-12-05"
    )
    et = etypes_repo.get_by_slug(db, event_type)
    assert et is not None
    active = statuses_repo.get_by_slug(db, "active")
    assert active is not None
    # 14 seniors (2027 graduates in a fall-2026 term) and 14 sophomores, so the
    # ban has plenty of seniors to exclude AND plenty of pool left to fill with.
    for i in range(14):
        members_repo.insert(
            db, slug=f"sr{i:02d}", display_name=f"Senior {i:02d}",
            status_id=active.id, class_year=2027,
        )
        members_repo.insert(
            db, slug=f"so{i:02d}", display_name=f"Soph {i:02d}",
            status_id=active.id, class_year=2029,
        )
    eid = events_repo.insert(
        db, semester_id=sem_id, event_type_id=et.id,
        display_name=f"The {event_type}", date="2026-09-19",
    )
    shift_requirements.snapshot_for_event(db, eid)
    return sem_id, eid


def _door_slot(db: sqlite3.Connection, event_id: int):  # noqa: ANN202
    """The first door slot on this event, by id — the display name carries a
    date suffix the repo resolver does not match on."""
    from risk.repos import shifts as shifts_repo

    for shift in shifts_repo.list_for_event(db, event_id):
        if shift.shift_type_slug == "door" and shift.slot_index == 0:
            return shift
    raise AssertionError("no door slot 0")


def _seniors_on(db: sqlite3.Connection, event_id: int, slugs: tuple[str, ...]) -> list[str]:
    return [
        r["display_name"]
        for r in db.execute(
            """
            SELECT m.display_name FROM shifts s
            JOIN members m ON m.id = s.assigned_member_id
            JOIN shift_types st ON st.id = s.shift_type_id
            WHERE s.event_id = ? AND st.slug IN (?, ?, ?) AND m.class_year <= 2027
            """,
            (event_id, *slugs),
        )
    ]


@pytest.mark.parametrize("event_type", ["krush", "dage"])
def test_the_fill_seats_no_senior_on_a_night_post(
    db: sqlite3.Connection, event_type: str
) -> None:
    sem_id, eid = _world(db, event_type)
    with transaction(db):
        assignment.auto_assign(db, event_id=eid, seed=1, commit=True)
    assert _seniors_on(db, eid, NIGHT) == []


@pytest.mark.parametrize("event_type", ["krush", "dage"])
def test_the_night_posts_still_all_get_filled(
    db: sqlite3.Connection, event_type: str
) -> None:
    """The ban must not be paid for with an empty door. It never removes more
    than the seniors, and the underclassmen are still there."""
    sem_id, eid = _world(db, event_type)
    with transaction(db):
        assignment.auto_assign(db, event_id=eid, seed=1, commit=True)
    open_night = db.execute(
        """
        SELECT COUNT(*) AS n FROM shifts s JOIN shift_types st ON st.id = s.shift_type_id
        WHERE s.event_id = ? AND st.slug IN (?, ?, ?) AND s.assigned_member_id IS NULL
        """,
        (eid, *NIGHT),
    ).fetchone()["n"]
    assert open_night == 0


def test_setup_and_cleanup_stay_open_to_seniors(db: sqlite3.Connection) -> None:
    """The point is to move seniors onto the easy jobs, not off the event."""
    assert not policy.senior_is_barred("krush", "setup")
    assert not policy.senior_is_barred("dage", "cleanup")


def test_dj_stays_open_to_seniors(db: sqlite3.Connection) -> None:
    """One of the chapter's two DJs is a senior, and the other is at a tennis
    tournament on 19 Sep — the first Krush of the term. Banning him empties the
    decks. DJ is also uncounted, so it is not a senior dodging a shift."""
    assert not policy.senior_is_barred("krush", "dj")
    assert not policy.senior_is_barred("dage", "dj")


def test_a_mixer_is_unaffected(db: sqlite3.Connection) -> None:
    assert not policy.senior_is_barred("mixer", "door")
    assert not policy.senior_is_barred("open", "driver")


def test_the_chair_cannot_hand_place_a_senior_there_by_accident(
    db: sqlite3.Connection,
) -> None:
    """Both write paths, or the rule is only half a rule."""
    sem_id, eid = _world(db, "krush")
    with transaction(db):
        assignment.auto_assign(db, event_id=eid, seed=1, commit=True)
    slot = _door_slot(db, eid)
    with (
        pytest.raises(ValueError, match="seniors do not work door at a krush"),
        transaction(db),
    ):
        manual_assign.assign(db, shift_id=slot.id, member_key="sr00")


def test_force_still_lands_it_and_says_why(db: sqlite3.Connection) -> None:
    """The chair is sometimes right and the data is sometimes stale. What the
    override must not do is hide the objection."""
    sem_id, eid = _world(db, "krush")
    with transaction(db):
        assignment.auto_assign(db, event_id=eid, seed=1, commit=True)
    slot = _door_slot(db, eid)
    with transaction(db):
        res = manual_assign.assign(db, shift_id=slot.id, member_key="sr00", force=True)
    assert any("seniors do not work door at a krush" in w for w in res.warnings)
    assert _seniors_on(db, eid, NIGHT) == ["Senior 00"]
