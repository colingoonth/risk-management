"""A DJ may also take setup or cleanup at the party he is DJing.

Colin, 2026-08-26: "dj is only not available to work with door and rides. they
can still setup and cleanup."

The old rule was a flat one-shift-per-member-per-event, and it was wrong here
twice. There is no clash of TIME — dj is 20:00-23:59, setup is a 2h block across
the three days up to the party, cleanup is the next morning. And there is no
clash of LOAD — a dj night counts toward nobody's tally, so a DJ who also takes
setup still has exactly one counted turn, like everybody else. He was being
charged a whole event for a shift the ledger does not record, which is also what
made standing note 15 ("both DJs must work a real rotation shift early") so hard
to satisfy: a man who DJs most parties had almost no event left to take a turn at.
"""

from __future__ import annotations

import sqlite3

import pytest

from risk.db.connection import transaction
from risk.repos import event_types as etypes_repo
from risk.repos import events as events_repo
from risk.repos import member_qualifications as mq_repo
from risk.repos import member_statuses as statuses_repo
from risk.repos import members as members_repo
from risk.repos import qualifications as quals_repo
from risk.repos import semesters as semesters_repo
from risk.repos import shifts as shifts_repo
from risk.services import manual_assign, shift_requirements

pytestmark = pytest.mark.integration


def _world(db: sqlite3.Connection) -> tuple[int, int, int]:
    sem_id = semesters_repo.insert(
        db, name="FA26", starts_on="2026-08-25", ends_on="2026-12-05"
    )
    et = etypes_repo.get_by_slug(db, "mixer")
    active = statuses_repo.get_by_slug(db, "active")
    assert et is not None and active is not None
    dj_qual = quals_repo.get_by_slug(db, "dj")
    assert dj_qual is not None

    the_dj = members_repo.insert(
        db, slug="the-dj", display_name="The DJ", status_id=active.id, class_year=2029
    )
    mq_repo.grant(db, member_id=the_dj, qualification_id=dj_qual.id, semester_id=sem_id)
    for i in range(12):
        members_repo.insert(
            db, slug=f"b{i:02d}", display_name=f"B {i:02d}",
            status_id=active.id, class_year=2029,
        )
    eid = events_repo.insert(
        db, semester_id=sem_id, event_type_id=et.id,
        display_name="The party", date="2026-09-12",
    )
    shift_requirements.snapshot_for_event(db, eid)
    return sem_id, eid, the_dj


def _slot(db: sqlite3.Connection, event_id: int, slug: str, idx: int = 0):  # noqa: ANN202
    for s in shifts_repo.list_for_event(db, event_id):
        if s.shift_type_slug == slug and s.slot_index == idx:
            return s
    raise AssertionError(f"no {slug} slot {idx}")


def _seed_slots(db: sqlite3.Connection, event_id: int) -> None:
    """Create the open shift rows; the fill makes them lazily."""
    from risk.repos import event_shift_requirements as req_repo

    with transaction(db):
        for r in req_repo.list_for_event(db, event_id):
            for i in range(r.target_count):
                shifts_repo.insert_open(
                    db, event_id=event_id, shift_type_id=r.shift_type_id, slot_index=i
                )


@pytest.mark.parametrize("second", ["setup", "cleanup"])
def test_a_dj_may_also_take_setup_or_cleanup(db: sqlite3.Connection, second: str) -> None:
    sem_id, eid, the_dj = _world(db)
    _seed_slots(db, eid)
    with transaction(db):
        manual_assign.assign(db, shift_id=_slot(db, eid, "dj").id, member_key="the-dj")
    problems = manual_assign.check(
        db, shift=_slot(db, eid, second), member_id=the_dj
    )
    assert problems == [], f"a DJ should be allowed to work {second} at his own party"


@pytest.mark.parametrize("second", ["door", "driver"])
def test_a_dj_may_not_also_take_a_night_post(db: sqlite3.Connection, second: str) -> None:
    """The physical half: both are 20:00-23:59. One man, one place."""
    sem_id, eid, the_dj = _world(db)
    _seed_slots(db, eid)
    with transaction(db):
        manual_assign.assign(db, shift_id=_slot(db, eid, "dj").id, member_key="the-dj")
    problems = manual_assign.check(db, shift=_slot(db, eid, second), member_id=the_dj)
    assert problems, f"a DJ must not also stand {second} on the same night"
    assert any("night of this event" in p for p in problems)


def test_two_counted_shifts_at_one_event_are_still_blocked(db: sqlite3.Connection) -> None:
    """The relaxation is for UNCOUNTED shifts only. door + setup is two turns
    for one man and stays refused, exactly as before."""
    sem_id, eid, _ = _world(db)
    _seed_slots(db, eid)
    with transaction(db):
        manual_assign.assign(db, shift_id=_slot(db, eid, "door").id, member_key="b00")
    b00 = members_repo.resolve(db, "b00")
    assert b00 is not None
    problems = manual_assign.check(db, shift=_slot(db, eid, "setup"), member_id=b00.id)
    assert any("already working door" in p for p in problems)
