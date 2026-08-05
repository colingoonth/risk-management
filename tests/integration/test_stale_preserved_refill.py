"""Re-running auto-assign after someone becomes ineligible must not wedge.

``auto_assign`` preserves chair-made assignments, except where the assignee is
no longer eligible — those are unassigned as ``stale_preserved`` with a warning
naming each person, and the freed slot is refilled in the same run.

``shifts_repo.unassign`` clears the assignment but LEAVES THE ROW, status
``'open'``. The stale loop nevertheless dropped the slot from ``by_type_slot``,
so the fill loop believed no row existed and called ``insert_open`` for a
(event, shift_type, slot_index) that was already taken. That raises
``UNIQUE constraint failed`` — and since the run is one transaction, it rolls
back, the stale assignment survives, and the very next run hits the same wall.
Auto-assign for that event is then wedged for good, which is the worst shape a
scheduling bug can take: the chair's only recovery is hand-editing the DB.

Reachable through two ordinary chair actions — setting an event's host house
(H2 excludes members of that house) and marking a member alumni or exempt.
"""

from __future__ import annotations

import sqlite3

import pytest

from risk.db.connection import transaction
from risk.repos import event_types as etypes_repo
from risk.repos import events as events_repo
from risk.repos import houses as houses_repo
from risk.repos import member_house_assignments as mha_repo
from risk.repos import member_statuses as statuses_repo
from risk.repos import members as members_repo
from risk.repos import semesters as semesters_repo
from risk.repos import shifts as shifts_repo
from risk.services import assignment
from risk.services import shift_requirements as reqs_svc

pytestmark = pytest.mark.integration

EVENT_DATE = "2026-09-11"


@pytest.fixture()
def world(db: sqlite3.Connection) -> tuple[int, int, int, dict[str, int]]:
    """Semester, an off-site mixer, a spare house, and 15 identical members."""
    sem_id = semesters_repo.insert(
        db, name="FA26", starts_on="2026-08-25", ends_on="2026-12-05"
    )
    house_id = houses_repo.insert(db, slug="stale-house", display_name="Stale House")
    et = etypes_repo.get_by_slug(db, "mixer")
    assert et is not None
    event_id = events_repo.insert(
        db,
        semester_id=sem_id,
        event_type_id=et.id,
        display_name="Stale mixer",
        date=EVENT_DATE,
        host_house_id=None,
    )
    active = statuses_repo.get_by_slug(db, "active")
    assert active is not None
    members = {
        f"sm-{i:02d}": members_repo.insert(
            db,
            slug=f"sm-{i:02d}",
            display_name=f"Stale Member {i:02d}",
            status_id=active.id,
            class_year=2027,
        )
        for i in range(1, 16)
    }
    reqs_svc.snapshot_for_event(db, event_id)
    db.commit()
    return sem_id, house_id, event_id, members


def _first_assignee(result: assignment.AutoAssignResult) -> assignment.ProposedAssignment:
    filled = [a for a in result.assignments if a.member_id is not None]
    assert filled, "first run should have filled something"
    return filled[0]


def test_auto_assign_refills_a_slot_freed_by_ineligibility(
    db: sqlite3.Connection, world: tuple[int, int, int, dict[str, int]]
) -> None:
    """The freed slot is refilled in the same run, reusing the existing row."""
    sem_id, house_id, event_id, members = world
    with transaction(db):
        first = assignment.auto_assign(db, event_id=event_id, seed=1)
    victim = _first_assignee(first)
    assert victim.member_slug is not None

    # The chair sets a host house the assignee belongs to — H2 now excludes them.
    with transaction(db):
        mha_repo.set_assignment(
            db, member_id=members[victim.member_slug], house_id=house_id, semester_id=sem_id
        )
        db.execute(
            "UPDATE events SET host_house_id = ? WHERE id = ?", (house_id, event_id)
        )

    with transaction(db):
        second = assignment.auto_assign(db, event_id=event_id, seed=1)

    assert any(
        "no longer eligible" in w and victim.member_slug in w for w in second.warnings
    ), f"expected a stale-preserved warning naming {victim.member_slug}: {second.warnings}"

    refilled = [
        a
        for a in second.assignments
        if a.shift_type_slug == victim.shift_type_slug and a.slot_index == victim.slot_index
    ]
    assert len(refilled) == 1
    assert refilled[0].member_id is not None, "the freed slot should be refilled"
    assert refilled[0].member_slug != victim.member_slug

    # And the ineligible member holds nothing at this event any more.
    live = shifts_repo.list_for_event(db, event_id)
    assert members[victim.member_slug] not in {
        s.assigned_member_id for s in live if s.assigned_member_id is not None
    }
    # No duplicate row was created for the reused slot.
    keys = [(s.shift_type_id, s.slot_index) for s in live]
    assert len(keys) == len(set(keys)), "one row per (shift_type, slot)"


def test_auto_assign_is_not_wedged_by_a_stale_assignment(
    db: sqlite3.Connection, world: tuple[int, int, int, dict[str, int]]
) -> None:
    """Running it a third time must keep working, not fail the same way forever."""
    sem_id, house_id, event_id, members = world
    with transaction(db):
        first = assignment.auto_assign(db, event_id=event_id, seed=1)
    victim = _first_assignee(first)
    assert victim.member_slug is not None

    with transaction(db):
        mha_repo.set_assignment(
            db, member_id=members[victim.member_slug], house_id=house_id, semester_id=sem_id
        )
        db.execute(
            "UPDATE events SET host_house_id = ? WHERE id = ?", (house_id, event_id)
        )

    for attempt in range(3):
        with transaction(db):
            assignment.auto_assign(db, event_id=event_id, seed=1)
        _ = attempt


def test_dry_run_reports_the_freed_slot_as_refilled(
    db: sqlite3.Connection, world: tuple[int, int, int, dict[str, int]]
) -> None:
    """A dry run must model the same outcome the real run would produce.

    The stale slot is cleared in memory rather than only in the database, so
    ``commit=False`` shows the refill instead of reporting the slot preserved
    for a member who is no longer eligible.
    """
    sem_id, house_id, event_id, members = world
    with transaction(db):
        first = assignment.auto_assign(db, event_id=event_id, seed=1)
    victim = _first_assignee(first)
    assert victim.member_slug is not None

    with transaction(db):
        mha_repo.set_assignment(
            db, member_id=members[victim.member_slug], house_id=house_id, semester_id=sem_id
        )
        db.execute(
            "UPDATE events SET host_house_id = ? WHERE id = ?", (house_id, event_id)
        )

    dry = assignment.auto_assign(db, event_id=event_id, seed=1, commit=False)
    proposed = [
        a
        for a in dry.assignments
        if a.shift_type_slug == victim.shift_type_slug and a.slot_index == victim.slot_index
    ]
    assert len(proposed) == 1
    assert proposed[0].member_slug != victim.member_slug
    assert proposed[0].reason == "assigned"

    # Dry run wrote nothing: the stale assignment is still on the row.
    live = shifts_repo.list_for_event(db, event_id)
    still = [
        s
        for s in live
        if s.shift_type_slug == victim.shift_type_slug and s.slot_index == victim.slot_index
    ]
    assert still[0].assigned_member_id == members[victim.member_slug]
