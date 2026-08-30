"""Seniors and rides: a preference, a ceiling, and a crew limit.

Colin, 2026-08-30: "Try to keep seniors off rides or at least not 2 seniors on
one ride shift. Id like to have seniors at max 2 rides a sem but pref 1."

Three numbers, so three mechanisms, and they are not interchangeable:

  pref 1   SENIOR_NIGHT_TYPE_CAPS       soft — sorts him last, still takeable
  max 2    SENIOR_NIGHT_TYPE_HARD_CAPS  hard — removed from the pool at 2
  not 2    MAX_SENIORS_PER_EVENT_SHIFT  per-crew — constrains who works TOGETHER

The season caps limit what one man works and say nothing about who he works it
with, so three seniors each under their own cap could still crew one night's
rides between them. That is the picture the crew limit exists to prevent.
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
from risk.services import assignment, policy, shift_requirements

pytestmark = pytest.mark.integration


def _world(db: sqlite3.Connection, *, seniors: int, others: int) -> tuple[int, list[int]]:
    sem_id = semesters_repo.insert(
        db, name="FA26", starts_on="2026-08-25", ends_on="2026-12-05"
    )
    et = etypes_repo.get_by_slug(db, "mixer")
    active = statuses_repo.get_by_slug(db, "active")
    assert et is not None and active is not None
    for i in range(seniors):
        members_repo.insert(db, slug=f"sr{i:02d}", display_name=f"Sr {i:02d}",
                            status_id=active.id, class_year=2027)
    for i in range(others):
        members_repo.insert(db, slug=f"un{i:02d}", display_name=f"Un {i:02d}",
                            status_id=active.id, class_year=2029)
    ids = []
    for d in ("2026-09-01", "2026-09-04", "2026-09-08", "2026-09-11", "2026-09-15"):
        eid = events_repo.insert(db, semester_id=sem_id, event_type_id=et.id,
                                 display_name=f"Party {d}", date=d)
        shift_requirements.snapshot_for_event(db, eid)
        ids.append(eid)
    return sem_id, ids


def _rides(db: sqlite3.Connection, event_id: int) -> list[sqlite3.Row]:
    return db.execute(
        """SELECT m.display_name, m.class_year FROM shifts s
           JOIN shift_types st ON st.id = s.shift_type_id
           JOIN members m ON m.id = s.assigned_member_id
           WHERE s.event_id = ? AND st.slug = 'driver'""",
        (event_id,),
    ).fetchall()


def test_no_more_than_one_senior_on_a_rides_crew(db: sqlite3.Connection) -> None:
    sem_id, events = _world(db, seniors=20, others=20)
    for eid in events:
        with transaction(db):
            assignment.auto_assign(db, event_id=eid, seed=1, commit=True)
    for eid in events:
        srs = [r for r in _rides(db, eid) if r["class_year"] <= 2027]
        assert len(srs) <= policy.MAX_SENIORS_PER_EVENT_SHIFT["driver"], (
            f"{[r['display_name'] for r in srs]} all on one rides crew"
        )


def test_no_senior_passes_the_season_ceiling(db: sqlite3.Connection) -> None:
    sem_id, events = _world(db, seniors=4, others=6)
    for eid in events:
        with transaction(db):
            assignment.auto_assign(db, event_id=eid, seed=1, commit=True)
    counts = db.execute(
        """SELECT m.display_name, COUNT(*) n FROM shifts s
           JOIN shift_types st ON st.id = s.shift_type_id
           JOIN members m ON m.id = s.assigned_member_id
           WHERE st.slug='driver' AND m.class_year <= 2027 GROUP BY m.id"""
    ).fetchall()
    over = [(r["display_name"], r["n"]) for r in counts
            if r["n"] > policy.SENIOR_NIGHT_TYPE_HARD_CAPS["driver"]]
    assert over == [], f"past the ceiling: {over}"


def test_the_crew_limit_yields_before_it_strands_a_post(db: sqlite3.Connection) -> None:
    """A rides post with nobody but seniors left must still be staffed.

    Every avoidance rule in this codebase has to lose to an empty post, and this
    one is checked with a pool of seniors only.
    """
    sem_id, events = _world(db, seniors=12, others=0)
    eid = events[0]
    with transaction(db):
        assignment.auto_assign(db, event_id=eid, seed=1, commit=True)
    required = db.execute(
        """SELECT r.target_count n FROM event_shift_requirements r
           JOIN shift_types st ON st.id = r.shift_type_id
           WHERE r.event_id = ? AND st.slug='driver'""",
        (eid,),
    ).fetchone()["n"]
    assert len(_rides(db, eid)) == required, "the crew cap must never cost a staffed post"


def test_the_preference_is_still_only_a_preference(db: sqlite3.Connection) -> None:
    """pref 1 sorts him last; it does not forbid a second rides shift the way
    the ceiling forbids a third."""
    assert policy.SENIOR_NIGHT_TYPE_CAPS["driver"] == 1
    assert policy.SENIOR_NIGHT_TYPE_HARD_CAPS["driver"] == 2
    assert policy.SENIOR_NIGHT_TYPE_HARD_CAPS["driver"] > policy.SENIOR_NIGHT_TYPE_CAPS["driver"]


def test_no_single_night_is_flooded_with_seniors(db: sqlite3.Connection) -> None:
    """"Seniors can now work but dont like flood it with just seniors."

    Measured before this cap existed, on the first build after the block-1
    steer was lifted: four of ten nights came out at ten or eleven seniors from
    twelve slots and four had none at all. Every individual assignment was
    correct — holding a class out of a block leaves all of them on a score of
    zero, so they arrive in one burst — and the distribution was still
    indefensible. This pins the shape, not the arithmetic.
    """
    sem_id, events = _world(db, seniors=20, others=20)
    for eid in events:
        with transaction(db):
            assignment.auto_assign(db, event_id=eid, seed=1, commit=True)
    for eid in events:
        rows = db.execute(
            """SELECT m.class_year FROM shifts s
               JOIN shift_types st ON st.id = s.shift_type_id
               JOIN members m ON m.id = s.assigned_member_id
               WHERE s.event_id = ? AND st.counts_toward_tally = 1""",
            (eid,),
        ).fetchall()
        total = len(rows)
        seniors = sum(1 for r in rows if r["class_year"] <= 2027)
        allowed = int(total * policy.MAX_SENIOR_FRACTION_PER_EVENT) + 1
        assert seniors <= allowed, (
            f"{seniors}/{total} of one night's slots went to seniors"
        )


def test_the_event_cap_yields_before_it_strands_a_post(db: sqlite3.Connection) -> None:
    """With nobody but seniors, every post the cap governs is still staffed.

    COUNTED slots only. dj is qualification-gated and no one in this fixture
    holds it, so that slot is legitimately short for a reason that has nothing
    to do with seniors — counting it would make this test fail for the wrong
    reason, which it did on first writing.
    """
    sem_id, events = _world(db, seniors=20, others=0)
    eid = events[0]
    with transaction(db):
        assignment.auto_assign(db, event_id=eid, seed=1, commit=True)
    required = db.execute(
        """SELECT COALESCE(SUM(r.target_count), 0) n FROM event_shift_requirements r
           JOIN shift_types st ON st.id = r.shift_type_id
           WHERE r.event_id = ? AND st.counts_toward_tally = 1""",
        (eid,),
    ).fetchone()["n"]
    seated = db.execute(
        """SELECT COUNT(*) n FROM shifts s JOIN shift_types st ON st.id = s.shift_type_id
           WHERE s.event_id = ? AND s.assigned_member_id IS NOT NULL
             AND st.counts_toward_tally = 1""",
        (eid,),
    ).fetchone()["n"]
    assert seated == required, "the senior cap must never cost a staffed post"
