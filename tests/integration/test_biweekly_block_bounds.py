"""The fortnightly-publishing bounds: a rebuild must not reach a sent week.

From 2026-08-26 the chapter publishes a fortnight at a time instead of a whole
term. That makes "clear from this date forward and refill" the routine
operation, and the routine operation is a DELETE against shifts — so the date
bound is the only thing standing between a rebuild and a weekend that 60 people
have already been told they are working. A missed shift is a strike, so an
off-by-one here is not a cosmetic bug.

Both ends are pinned INCLUSIVE, because "Sunday the 30th until the Sunday two
weeks from then" is how the chair says it and both Sundays are meant to be in.
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
from risk.repos import shifts as shifts_repo
from risk.services import assignment, manual_assign, shift_requirements

pytestmark = pytest.mark.integration

DATES = ["2026-08-28", "2026-08-29", "2026-08-30", "2026-09-06", "2026-09-13", "2026-09-14"]


def _world(db: sqlite3.Connection) -> tuple[int, dict[str, int]]:
    sem_id = semesters_repo.insert(
        db, name="FA26", starts_on="2026-08-25", ends_on="2026-12-05"
    )
    et = etypes_repo.get_by_slug(db, "mixer")
    assert et is not None
    active = statuses_repo.get_by_slug(db, "active")
    assert active is not None
    for i in range(24):
        members_repo.insert(
            db,
            slug=f"m{i:02d}",
            display_name=f"Member {i:02d}",
            status_id=active.id,
            class_year=2027 + (i % 3),
        )
    by_date: dict[str, int] = {}
    for d in DATES:
        eid = events_repo.insert(
            db, semester_id=sem_id, event_type_id=et.id, display_name=f"Party {d}", date=d
        )
        shift_requirements.snapshot_for_event(db, eid)
        by_date[d] = eid
    with transaction(db):
        assignment.auto_assign_semester(db, semester_id=sem_id, seed=1, commit=True)
    return sem_id, by_date


def _dates_with_shifts(db: sqlite3.Connection, sem_id: int) -> set[str]:
    return {
        r["date"]
        for r in db.execute(
            """
            SELECT DISTINCT e.date FROM shifts s JOIN events e ON e.id = s.event_id
            WHERE e.semester_id = ? AND s.assigned_member_id IS NOT NULL
            """,
            (sem_id,),
        )
    }


def test_a_bounded_clear_leaves_the_published_week_alone(db: sqlite3.Connection) -> None:
    """The whole point. Clearing the block must not touch 28-29 Aug."""
    sem_id, _ = _world(db)
    assert _dates_with_shifts(db, sem_id) == set(DATES), "every date filled to begin with"

    with transaction(db):
        manual_assign.clear_semester(
            db, semester_id=sem_id, on_or_after="2026-08-30", on_or_before="2026-09-13"
        )

    survived = _dates_with_shifts(db, sem_id)
    assert "2026-08-28" in survived, "a party already worked was deleted"
    assert "2026-08-29" in survived, "a party already worked was deleted"
    assert "2026-09-14" in survived, "the clear reached past the end of the block"
    assert survived == {"2026-08-28", "2026-08-29", "2026-09-14"}


def test_both_bounds_are_inclusive(db: sqlite3.Connection) -> None:
    """Sunday the 30th and the Sunday a fortnight later are both IN the block."""
    sem_id, _ = _world(db)
    with transaction(db):
        manual_assign.clear_semester(
            db, semester_id=sem_id, on_or_after="2026-08-30", on_or_before="2026-09-13"
        )
    survived = _dates_with_shifts(db, sem_id)
    assert "2026-08-30" not in survived, "the opening Sunday must be inside the block"
    assert "2026-09-13" not in survived, "the closing Sunday must be inside the block"


def test_a_bounded_clear_still_keeps_chair_overrides(db: sqlite3.Connection) -> None:
    """The date bound must not quietly cost the chair his manual edits."""
    sem_id, by_date = _world(db)
    inside = shifts_repo.list_for_event(db, by_date["2026-09-06"])[0]
    with transaction(db):
        db.execute("UPDATE shifts SET chair_set = 1 WHERE id = ?", (inside.id,))

    with transaction(db):
        _, kept = manual_assign.clear_semester(
            db, semester_id=sem_id, on_or_after="2026-08-30", on_or_before="2026-09-13"
        )
    assert kept == 1
    assert db.execute("SELECT 1 FROM shifts WHERE id = ?", (inside.id,)).fetchone() is not None


def test_a_bounded_fill_stops_at_the_end_of_the_block(db: sqlite3.Connection) -> None:
    """auto_assign_semester must not seat anyone past the block being published."""
    sem_id, _ = _world(db)
    with transaction(db):
        manual_assign.clear_semester(db, semester_id=sem_id)
    assert _dates_with_shifts(db, sem_id) == set()

    with transaction(db):
        filled = assignment.auto_assign_semester(
            db,
            semester_id=sem_id,
            seed=1,
            commit=True,
            on_or_after="2026-08-30",
            on_or_before="2026-09-13",
        )
    assert {e.date for e, _ in filled} == {"2026-08-30", "2026-09-06", "2026-09-13"}
    assert _dates_with_shifts(db, sem_id) == {"2026-08-30", "2026-09-06", "2026-09-13"}
