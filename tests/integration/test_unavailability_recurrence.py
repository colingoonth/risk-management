"""Weekly-recurring unavailability must only block the weekday it names.

``repeats_weekday`` arrived with migration 0012 so the availability Google Form
could capture "busy every Thursday". ``services.availability._applies_on`` has
always honoured it. ``repos.unavailability.member_ids_unavailable_on`` — the
query behind the H4 eligibility filter — did not, and treated the row as if the
member were gone for the entire ``starts_on..ends_on`` range.

That is over-exclusion, and over-exclusion is the specific failure this whole
app exists to stop: a member who is busy on Thursdays gets dropped from every
Friday party of the semester, the pool shrinks, slots go unfilled, and the chair
ends up back-filling by hand.

Dates are pinned to real FA26 weekdays:
    2026-09-10 and 2026-10-08 are Thursdays; 2026-09-11 and 2026-10-09 Fridays.
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
from risk.repos import shift_types as stypes_repo
from risk.repos import unavailability as unav_repo
from risk.services import availability, eligibility

pytestmark = pytest.mark.integration

THURSDAY = "2026-09-10"
FRIDAY = "2026-09-11"
LATER_THURSDAY = "2026-10-08"
LATER_FRIDAY = "2026-10-09"
THURSDAY_WEEKDAY = 3

SEM_START = "2026-08-25"
SEM_END = "2026-12-05"


@pytest.fixture()
def world(db: sqlite3.Connection) -> tuple[int, int]:
    """A semester and one member. Returns (member_id, semester_id)."""
    sem_id = semesters_repo.insert(db, name="FA26", starts_on=SEM_START, ends_on=SEM_END)
    active = statuses_repo.get_by_slug(db, "active")
    assert active is not None
    member_id = members_repo.insert(
        db,
        slug="recurring-tester",
        display_name="Recurring Tester",
        status_id=active.id,
        class_year=2027,
    )
    db.commit()
    return member_id, sem_id


def _busy_thursdays(db: sqlite3.Connection, world: tuple[int, int]) -> None:
    """'Busy every Thursday, all semester' — the form's headline recurring case."""
    member_id, sem_id = world
    with transaction(db):
        unav_repo.insert(
            db,
            member_id=member_id,
            semester_id=sem_id,
            starts_on=SEM_START,
            ends_on=SEM_END,
            reason="lab every Thursday",
            repeats_weekday=THURSDAY_WEEKDAY,
        )


def test_recurring_row_blocks_its_own_weekday(
    db: sqlite3.Connection, world: tuple[int, int]
) -> None:
    member_id, sem_id = world
    _busy_thursdays(db, world)
    for thursday in (THURSDAY, LATER_THURSDAY):
        assert unav_repo.member_ids_unavailable_on(db, semester_id=sem_id, date=thursday) == {
            member_id
        }, f"should be blocked on {thursday}"


def test_recurring_row_leaves_other_weekdays_alone(
    db: sqlite3.Connection, world: tuple[int, int]
) -> None:
    """The bug: 'busy Thursdays' emptied every Friday too."""
    _, sem_id = world
    _busy_thursdays(db, world)
    for friday in (FRIDAY, LATER_FRIDAY):
        assert unav_repo.member_ids_unavailable_on(db, semester_id=sem_id, date=friday) == set(), (
            f"should be free on {friday}"
        )


def test_one_off_row_still_covers_its_whole_range(
    db: sqlite3.Connection, world: tuple[int, int]
) -> None:
    """Guard the other direction: no recurrence means every day in the range."""
    member_id, sem_id = world
    with transaction(db):
        unav_repo.insert(
            db,
            member_id=member_id,
            semester_id=sem_id,
            starts_on=THURSDAY,
            ends_on=LATER_FRIDAY,
            reason="away",
        )
    for day in (THURSDAY, FRIDAY, LATER_THURSDAY, LATER_FRIDAY):
        assert unav_repo.member_ids_unavailable_on(db, semester_id=sem_id, date=day) == {
            member_id
        }, f"one-off range should cover {day}"


def test_recurring_row_does_not_drop_member_from_a_friday_event(
    db: sqlite3.Connection, world: tuple[int, int]
) -> None:
    """The user-visible consequence: busy Thursdays must not cost a Friday."""
    member_id, sem_id = world
    _busy_thursdays(db, world)
    et = etypes_repo.get_by_slug(db, "mixer")
    assert et is not None
    event_id = events_repo.insert(
        db,
        semester_id=sem_id,
        event_type_id=et.id,
        display_name="Friday mixer",
        date=FRIDAY,
        host_house_id=None,
    )
    db.commit()

    result = eligibility.eligible_for(
        db,
        event_id=event_id,
        semester_id=sem_id,
        host_house_id=None,
    )
    assert [m.member_id for m in result.eligible] == [member_id]
    # And the window check agrees: a Thursday-only blackout leaves the Friday
    # party night untouched. Asserted through the shift type rather than the
    # pool, because that is where unavailability is now decided.
    door = stypes_repo.get_by_slug(db, "door")
    assert door is not None
    assert availability.can_cover(
        db,
        member_id=member_id,
        semester_id=sem_id,
        shift_type_id=door.id,
        event_date=FRIDAY,
    )
