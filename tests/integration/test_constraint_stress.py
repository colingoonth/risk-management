"""Raw-SQL constraint stress tests.

These tests bypass the service/repo layers and try malformed inserts directly
against the schema. Each documented CHECK / UNIQUE / FK invariant should
bounce with IntegrityError. Anything that slips through is a schema regression.

Coverage maps to migrations 0005-0011:

* shifts.slot_index range (0007)
* shifts.status enum (0007)
* shifts assigned_member_id <-> status coupling (0007)
* shifts UNIQUE(event_id, shift_type_id, slot_index) (0007)
* shifts_one_assignment partial unique index (0007)
* events.status enum (0006)
* event_shift_requirements target_count >= min_count (0006)
* member_statuses.slug GLOB pattern (0005)
* unavailability ends_on >= starts_on (0009)
* unavailability UNIQUE(member, semester, range) (0011)
* archive guard on strikes (0008)
"""

from __future__ import annotations

import sqlite3

import pytest

from risk.db.connection import transaction
from risk.repos import event_types as etypes_repo
from risk.repos import events as events_repo
from risk.repos import houses as houses_repo
from risk.repos import member_statuses as statuses_repo
from risk.repos import members as members_repo
from risk.repos import semesters as semesters_repo
from risk.services import shift_requirements as svc_reqs

pytestmark = pytest.mark.integration


def _populated(db: sqlite3.Connection) -> dict[str, int]:
    """Build a minimal world: 1 semester, 1 house, 3 members, 1 event w/ reqs."""
    sem_id = semesters_repo.insert(db, name="FA25", starts_on="2025-08-25", ends_on="2025-12-15")
    semesters_repo.set_current(db, "FA25")
    house_id = houses_repo.insert(db, slug="zta", display_name="ZTA")
    active = statuses_repo.get_by_slug(db, "active")
    assert active is not None
    member_ids = [
        members_repo.insert(
            db,
            slug=f"m-{i}",
            display_name=f"M{i}",
            status_id=active.id,
            class_year=2027,
        )
        for i in range(3)
    ]
    et = etypes_repo.get_by_slug(db, "mixer")
    assert et is not None
    event_id = events_repo.insert(
        db,
        semester_id=sem_id,
        event_type_id=et.id,
        host_house_id=house_id,
        display_name="Test Mixer",
        date="2025-10-10",
    )
    svc_reqs.snapshot_for_event(db, event_id)
    return {
        "sem_id": sem_id,
        "house_id": house_id,
        "event_id": event_id,
        "member_a": member_ids[0],
        "member_b": member_ids[1],
        "shift_type_driver": 1,
    }


# ---------------------------------------------------------------------------
# shifts table — slot_index, status enum, assignment coupling, uniques
# ---------------------------------------------------------------------------


def test_shift_negative_slot_index_rejected(db: sqlite3.Connection) -> None:
    ctx = _populated(db)
    with pytest.raises(sqlite3.IntegrityError), transaction(db):
        db.execute(
            "INSERT INTO shifts (event_id, shift_type_id, slot_index, status) "
            "VALUES (?, ?, -1, 'open')",
            (ctx["event_id"], ctx["shift_type_driver"]),
        )


def test_shift_slot_index_50_rejected(db: sqlite3.Connection) -> None:
    ctx = _populated(db)
    with pytest.raises(sqlite3.IntegrityError), transaction(db):
        db.execute(
            "INSERT INTO shifts (event_id, shift_type_id, slot_index, status) "
            "VALUES (?, ?, 50, 'open')",
            (ctx["event_id"], ctx["shift_type_driver"]),
        )


def test_shift_unknown_status_rejected(db: sqlite3.Connection) -> None:
    ctx = _populated(db)
    with pytest.raises(sqlite3.IntegrityError), transaction(db):
        db.execute(
            "INSERT INTO shifts (event_id, shift_type_id, slot_index, status) "
            "VALUES (?, ?, 0, 'bogus')",
            (ctx["event_id"], ctx["shift_type_driver"]),
        )


def test_shift_assigned_status_requires_member(db: sqlite3.Connection) -> None:
    """status='assigned' with NULL assigned_member_id is forbidden by CHECK."""
    ctx = _populated(db)
    with pytest.raises(sqlite3.IntegrityError), transaction(db):
        db.execute(
            "INSERT INTO shifts (event_id, shift_type_id, slot_index, "
            "assigned_member_id, status, assigned_at) "
            "VALUES (?, ?, 0, NULL, 'assigned', '2025-10-09T00:00:00')",
            (ctx["event_id"], ctx["shift_type_driver"]),
        )


def test_shift_open_status_with_member_rejected(db: sqlite3.Connection) -> None:
    """status='open' with a non-NULL member is forbidden."""
    ctx = _populated(db)
    with pytest.raises(sqlite3.IntegrityError), transaction(db):
        db.execute(
            "INSERT INTO shifts (event_id, shift_type_id, slot_index, "
            "assigned_member_id, status) "
            "VALUES (?, ?, 0, ?, 'open')",
            (ctx["event_id"], ctx["shift_type_driver"], ctx["member_a"]),
        )


def test_shift_duplicate_slot_index_rejected(db: sqlite3.Connection) -> None:
    """UNIQUE(event_id, shift_type_id, slot_index) — second insert bounces."""
    ctx = _populated(db)
    with transaction(db):
        db.execute(
            "INSERT INTO shifts (event_id, shift_type_id, slot_index, status) "
            "VALUES (?, ?, 7, 'open')",
            (ctx["event_id"], ctx["shift_type_driver"]),
        )
    with pytest.raises(sqlite3.IntegrityError), transaction(db):
        db.execute(
            "INSERT INTO shifts (event_id, shift_type_id, slot_index, status) "
            "VALUES (?, ?, 7, 'open')",
            (ctx["event_id"], ctx["shift_type_driver"]),
        )


def test_shift_member_assigned_twice_same_event_type_rejected(
    db: sqlite3.Connection,
) -> None:
    """Partial unique index `shifts_one_assignment` forbids double-assignment."""
    ctx = _populated(db)
    with transaction(db):
        db.execute(
            "INSERT INTO shifts (event_id, shift_type_id, slot_index, "
            "assigned_member_id, status, assigned_at) "
            "VALUES (?, ?, 0, ?, 'assigned', '2025-10-09T00:00:00')",
            (ctx["event_id"], ctx["shift_type_driver"], ctx["member_a"]),
        )
    with pytest.raises(sqlite3.IntegrityError), transaction(db):
        db.execute(
            "INSERT INTO shifts (event_id, shift_type_id, slot_index, "
            "assigned_member_id, status, assigned_at) "
            "VALUES (?, ?, 1, ?, 'assigned', '2025-10-09T00:00:00')",
            (ctx["event_id"], ctx["shift_type_driver"], ctx["member_a"]),
        )


# ---------------------------------------------------------------------------
# events / requirements
# ---------------------------------------------------------------------------


def test_event_unknown_status_rejected(db: sqlite3.Connection) -> None:
    ctx = _populated(db)
    et = etypes_repo.get_by_slug(db, "mixer")
    assert et is not None
    with pytest.raises(sqlite3.IntegrityError), transaction(db):
        db.execute(
            "INSERT INTO events (semester_id, event_type_id, display_name, "
            "date, status) VALUES (?, ?, 'Bad Event', '2025-10-10', 'oops')",
            (ctx["sem_id"], et.id),
        )


def test_event_requirement_target_below_min_rejected(db: sqlite3.Connection) -> None:
    ctx = _populated(db)
    # event already has snapshotted reqs; insert a new (event, shift_type) pair
    # with target < min to trip the CHECK.
    with pytest.raises(sqlite3.IntegrityError), transaction(db):
        db.execute(
            "INSERT INTO event_shift_requirements "
            "(event_id, shift_type_id, min_count, target_count) "
            "VALUES (?, 5, 3, 1)",  # shift_type 5 = bar, not yet inserted
            (ctx["event_id"],),
        )


# ---------------------------------------------------------------------------
# member_statuses / members
# ---------------------------------------------------------------------------


def test_member_slug_uppercase_rejected(db: sqlite3.Connection) -> None:
    """slug GLOB '[a-z]*' — uppercase first char is forbidden."""
    active = statuses_repo.get_by_slug(db, "active")
    assert active is not None
    with pytest.raises(sqlite3.IntegrityError), transaction(db):
        db.execute(
            "INSERT INTO members (slug, display_name, status_id, class_year) "
            "VALUES ('BadSlug', 'Bad', ?, 2027)",
            (active.id,),
        )


def test_member_class_year_out_of_range_rejected(db: sqlite3.Connection) -> None:
    active = statuses_repo.get_by_slug(db, "active")
    assert active is not None
    with pytest.raises(sqlite3.IntegrityError), transaction(db):
        db.execute(
            "INSERT INTO members (slug, display_name, status_id, class_year) "
            "VALUES ('m-bad', 'Bad', ?, 1999)",
            (active.id,),
        )


# ---------------------------------------------------------------------------
# unavailability
# ---------------------------------------------------------------------------


def test_unavailability_end_before_start_rejected(db: sqlite3.Connection) -> None:
    ctx = _populated(db)
    with pytest.raises(sqlite3.IntegrityError), transaction(db):
        db.execute(
            "INSERT INTO unavailability "
            "(member_id, semester_id, starts_on, ends_on, reason) "
            "VALUES (?, ?, '2025-10-10', '2025-10-05', 'bad')",
            (ctx["member_a"], ctx["sem_id"]),
        )


def test_unavailability_duplicate_range_rejected(db: sqlite3.Connection) -> None:
    """Migration 0011 added UNIQUE(member, semester, starts, ends)."""
    ctx = _populated(db)
    with transaction(db):
        db.execute(
            "INSERT INTO unavailability "
            "(member_id, semester_id, starts_on, ends_on, reason) "
            "VALUES (?, ?, '2025-10-10', '2025-10-12', 'first')",
            (ctx["member_a"], ctx["sem_id"]),
        )
    with pytest.raises(sqlite3.IntegrityError), transaction(db):
        db.execute(
            "INSERT INTO unavailability "
            "(member_id, semester_id, starts_on, ends_on, reason) "
            "VALUES (?, ?, '2025-10-10', '2025-10-12', 'dup')",
            (ctx["member_a"], ctx["sem_id"]),
        )


# ---------------------------------------------------------------------------
# strikes — archive guard trigger
# ---------------------------------------------------------------------------


def test_strike_on_archived_semester_rejected(db: sqlite3.Connection) -> None:
    """trg_strikes_block_archived_insert fires on archived semesters."""
    ctx = _populated(db)
    # Archive the semester.
    with transaction(db):
        semesters_repo.mark_archived(
            db, semester_id=ctx["sem_id"], archived_at="2025-12-20T00:00:00"
        )
    with pytest.raises(sqlite3.IntegrityError), transaction(db):
        db.execute(
            "INSERT INTO strikes "
            "(member_id, semester_id, issued_on, reason) "
            "VALUES (?, ?, '2025-10-10', 'late')",
            (ctx["member_a"], ctx["sem_id"]),
        )
