"""A chair may hold a member to another class year's QUOTA.

The roster is not rewritten: class_year keeps saying what it said and the Tally
keeps printing his real class. Only the target he is measured against moves.

The property that makes this safe is CONSERVATION. quota_targets solves for
targets that sum to exactly the work that exists, so an override has to move the
member in the DENOMINATOR too. Giving one man a junior target while still
counting him among the sophomores leaves the quotas summing to more than the
season, and every published "vs target" percentage silently stops meaning
anything. That is asserted here directly.
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
from risk.services import export as export_svc
from risk.services import fairness, shift_requirements

pytestmark = pytest.mark.integration


def _world(db: sqlite3.Connection) -> tuple[int, int]:
    """FA26-shaped: 2027 senior, 2028 junior, 2029 sophomore."""
    sem_id = semesters_repo.insert(
        db, name="FA26", starts_on="2026-08-25", ends_on="2026-12-05"
    )
    et = etypes_repo.get_by_slug(db, "mixer")
    active = statuses_repo.get_by_slug(db, "active")
    assert et is not None and active is not None
    subject = members_repo.insert(
        db, slug="the-sophomore", display_name="The Sophomore",
        status_id=active.id, class_year=2029,
    )
    for i in range(6):
        members_repo.insert(db, slug=f"sr{i}", display_name=f"Sr {i}",
                            status_id=active.id, class_year=2027)
        members_repo.insert(db, slug=f"jr{i}", display_name=f"Jr {i}",
                            status_id=active.id, class_year=2028)
        members_repo.insert(db, slug=f"so{i}", display_name=f"So {i}",
                            status_id=active.id, class_year=2029)
    eid = events_repo.insert(db, semester_id=sem_id, event_type_id=et.id,
                             display_name="A party", date="2026-09-12")
    shift_requirements.snapshot_for_event(db, eid)
    return sem_id, subject


def _override(db: sqlite3.Connection, member_id: int, year: int | None) -> None:
    with transaction(db):
        db.execute("UPDATE members SET risk_class_year = ? WHERE id = ?", (year, member_id))


def test_without_an_override_he_carries_the_sophomore_target(db: sqlite3.Connection) -> None:
    sem_id, subject = _world(db)
    q = fairness.build_quota_context(db, semester_id=sem_id)
    assert q.target_for(2029) == pytest.approx(q.sophomore_target)
    assert q.sophomore_count == 7  # the subject plus six


def test_the_override_moves_him_to_the_junior_target(db: sqlite3.Connection) -> None:
    sem_id, subject = _world(db)
    _override(db, subject, 2028)
    q = fairness.build_quota_context(db, semester_id=sem_id)
    assert q.sophomore_count == 6, "he must leave the sophomore count"
    assert q.junior_count == 7, "and join the junior count"
    assert q.junior_target < q.sophomore_target, "which is the point — a lighter quota"


def test_the_targets_still_sum_to_the_work(db: sqlite3.Connection) -> None:
    """The property an override could quietly break."""
    sem_id, subject = _world(db)
    _override(db, subject, 2028)
    q = fairness.build_quota_context(db, semester_id=sem_id)
    total = (
        q.senior_count * q.senior_target
        + q.junior_count * q.junior_target
        + q.sophomore_count * q.sophomore_target
    )
    assert total == pytest.approx(q.rotation_slots, rel=1e-9)


def test_the_sheet_prints_the_new_target_and_the_real_class(db: sqlite3.Connection) -> None:
    """Target follows the override; the class label does not.

    Printing "Junior" beside a sophomore would be the sheet lying about the
    roster in order to explain a number.
    """
    sem_id, subject = _world(db)
    _override(db, subject, 2028)
    data = export_svc.build(db, semester_id=sem_id)
    row = next(r for r in data.tally if r.display_name == "The Sophomore")
    peer = next(r for r in data.tally if r.display_name == "So 0")
    assert row.class_label == "Sophomore", "the roster is not rewritten"
    assert row.target == pytest.approx(data.junior_target)
    assert row.target < peer.target, "he is held to less than his classmates"


def test_the_sheet_and_the_fill_agree_on_his_number(db: sqlite3.Connection) -> None:
    """The failure this codebase has already shipped once: two definitions."""
    sem_id, subject = _world(db)
    _override(db, subject, 2028)
    q = fairness.build_quota_context(db, semester_id=sem_id)
    data = export_svc.build(db, semester_id=sem_id)
    row = next(r for r in data.tally if r.display_name == "The Sophomore")
    assert row.target == pytest.approx(q.target_for(2028))


def test_clearing_the_override_puts_him_back(db: sqlite3.Connection) -> None:
    sem_id, subject = _world(db)
    _override(db, subject, 2028)
    _override(db, subject, None)
    q = fairness.build_quota_context(db, semester_id=sem_id)
    assert q.sophomore_count == 7
    assert q.junior_count == 6
