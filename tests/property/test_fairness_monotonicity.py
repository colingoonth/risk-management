"""Hypothesis property tests for ``services.fairness`` and ``services.policy``.

Invariants:
  P1. seniority_phantom_shifts is monotone non-increasing in ``class_year``
      (older members carry >= phantom shifts as younger ones).
  P2. seniority_phantom_shifts is non-negative.
  P3. seniority_phantom_shifts is capped at ``SENIORITY_PHANTOM_SHIFTS_PER_YEAR
      * SENIORITY_CAP_YEARS``.
  P4. score_member is monotone non-decreasing in shifts_so_far (more shifts
      assigned → higher score → picked later).
"""

from __future__ import annotations

import sqlite3

import pytest
from hypothesis import given
from hypothesis import strategies as st

from risk.repos import event_types as etypes_repo
from risk.repos import events as events_repo
from risk.repos import houses as houses_repo
from risk.repos import member_statuses as statuses_repo
from risk.repos import members as members_repo
from risk.repos import semesters as semesters_repo
from risk.repos import shift_types as stypes_repo
from risk.repos import shifts as shifts_repo
from risk.services import fairness
from risk.services.eligibility import EligibleMember
from risk.services.policy import (
    SENIORITY_CAP_YEARS,
    SENIORITY_PHANTOM_SHIFTS_PER_YEAR,
    seniority_phantom_shifts,
)

pytestmark = pytest.mark.property


@given(
    class_year=st.integers(min_value=1900, max_value=2200),
    event_year=st.integers(min_value=2020, max_value=2050),
)
def test_seniority_is_non_negative(class_year: int, event_year: int) -> None:
    assert seniority_phantom_shifts(class_year, event_year) >= 0


@given(
    class_year=st.integers(min_value=1900, max_value=2200),
    event_year=st.integers(min_value=2020, max_value=2050),
)
def test_seniority_is_capped(class_year: int, event_year: int) -> None:
    assert (
        seniority_phantom_shifts(class_year, event_year)
        <= SENIORITY_PHANTOM_SHIFTS_PER_YEAR * SENIORITY_CAP_YEARS
    )


@given(
    older=st.integers(min_value=1900, max_value=2200),
    delta=st.integers(min_value=1, max_value=50),
    event_year=st.integers(min_value=2020, max_value=2050),
)
def test_seniority_monotone_in_class_year(older: int, delta: int, event_year: int) -> None:
    """Older member (lower class_year) carries >= phantom shifts."""
    younger = older + delta
    assert seniority_phantom_shifts(older, event_year) >= seniority_phantom_shifts(
        younger, event_year
    )


# --- Integration property: score is monotone in shifts_so_far ---


def _seed_world(db: sqlite3.Connection, n_members: int) -> tuple[int, list[int], int]:
    sem_id = semesters_repo.insert(db, name="SP26", starts_on="2026-01-15", ends_on="2026-05-15")
    house_id = houses_repo.insert(db, slug="zta", display_name="ZTA")
    et = etypes_repo.get_by_slug(db, "mixer")
    assert et is not None
    event_id = events_repo.insert(
        db,
        semester_id=sem_id,
        event_type_id=et.id,
        display_name="ZTA mixer",
        date="2026-02-14",
        host_house_id=house_id,
    )
    active = statuses_repo.get_by_slug(db, "active")
    assert active is not None
    ids = [
        members_repo.insert(db, slug=f"m{i}", display_name=f"M{i}", status_id=active.id)
        for i in range(n_members)
    ]
    return sem_id, ids, event_id


def _assign_n_shifts_to(
    db: sqlite3.Connection, *, member_id: int, n: int, event_id: int, sem_id: int
) -> None:
    """Create n past shifts for the member by inserting+assigning slots on synthetic events."""
    from risk.repos import event_types as etypes_repo
    from risk.repos import pledge_modes as pmodes_repo

    et = etypes_repo.get_by_slug(db, "mixer")
    pm = pmodes_repo.get(db, "normal")
    assert et is not None and pm is not None
    st_door = stypes_repo.get_by_slug(db, "door")
    assert st_door is not None
    for i in range(n):
        ev_id = events_repo.insert(
            db,
            semester_id=sem_id,
            event_type_id=et.id,
            display_name=f"past-event-{member_id}-{i}",
            date="2026-02-01",
            host_house_id=None,
        )
        shift_id = shifts_repo.insert_open(
            db, event_id=ev_id, shift_type_id=st_door.id, slot_index=i
        )
        shifts_repo.assign(
            db,
            shift_id=shift_id,
            member_id=member_id,
            effective_pledge_mode_id=pm.id,
            assigned_at="2026-02-01",
        )
    _ = event_id  # unused; the future-event the score is pinned to


def test_score_monotone_in_shifts_so_far(db: sqlite3.Connection) -> None:
    sem_id, ids, event_id = _seed_world(db, n_members=3)
    # m0=0 shifts, m1=2 shifts, m2=5 shifts; same class_year so seniority equal.
    _assign_n_shifts_to(db, member_id=ids[1], n=2, event_id=event_id, sem_id=sem_id)
    _assign_n_shifts_to(db, member_id=ids[2], n=5, event_id=event_id, sem_id=sem_id)
    members = [
        EligibleMember(
            member_id=mid,
            member_slug=f"m{i}",
            display_name=f"M{i}",
            class_year=None,
            is_pledge=False,
        )
        for i, mid in enumerate(ids)
    ]
    scored = fairness.sort_by_fairness(
        db, pool=members, semester_id=sem_id, event_date="2026-03-01"
    )
    # Sorted ASC by score → m0, m1, m2
    assert [s.member.member_slug for s in scored] == ["m0", "m1", "m2"]
    scores = [s.score for s in scored]
    for a, b in zip(scores, scores[1:], strict=False):
        assert a <= b
