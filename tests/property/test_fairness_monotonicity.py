"""Hypothesis property tests for ``services.fairness`` and ``services.policy``.

These used to assert properties of ``seniority_phantom_shifts`` — non-negative,
capped, monotone in ``class_year``. That function is gone: HANDOFF lists the
phantom under "Considered and REJECTED" and ratifies a quota ratio in its place.

The properties are rewritten rather than deleted, because the intent behind them
survives the model change even though the arithmetic does not. "Seniors work
less" is still an invariant; it is now expressed as a smaller target rather than
a larger phantom. Two genuinely new properties come with the quota model and had
no phantom analogue at all — conservation of work, and the ratio itself — and
they are the two that make a published target defensible.

  Q1. The targets conserve work: every slot that must be staffed is accounted
      for by exactly one member's quota.
  Q2. The senior target sits at SENIOR_QUOTA_RATIO of the underclassman target.
  Q3. is_senior_in_term is monotone in class_year — an older member (lower
      graduation year) is a senior whenever a younger one is.
  Q4. score_member is monotone non-decreasing in shifts_so_far (more shifts
      assigned → higher score → picked later).
  Q5. Seniors work less: at equal shifts worked, a senior scores higher than an
      underclassman and is therefore picked later.
"""

from __future__ import annotations

import sqlite3

import pytest
from hypothesis import assume, given
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
from risk.services import shift_requirements as reqs_svc
from risk.services.eligibility import EligibleMember
from risk.services.policy import (
    SENIOR_QUOTA_RATIO,
    SOPHOMORE_QUOTA_RATIO,
    is_senior_in_term,
    quota_targets,
)

pytestmark = pytest.mark.property


@given(
    rotation_slots=st.integers(min_value=1, max_value=5000),
    senior_count=st.integers(min_value=0, max_value=300),
    junior_count=st.integers(min_value=0, max_value=300),
    sophomore_count=st.integers(min_value=0, max_value=300),
)
def test_targets_conserve_the_work(
    rotation_slots: int, senior_count: int, junior_count: int, sophomore_count: int
) -> None:
    """Q1. Sum of everyone's quota == the work there actually is.

    This is the property that makes a published "Target" column defensible. A
    hardcoded set of targets drifts the moment the roster or the calendar
    changes, and then the sheet asserts a total nobody is being asked to work.

    It is also the property that a naive third tier breaks. Multiplying the
    underclass target by 1.25 for sophomores AFTER a two-way solve leaves the
    quotas summing to more than the work, so every member finishes the term
    under 100% and the column stops meaning anything. The tier has to go into
    the solve, which is what this asserts.
    """
    assume(senior_count + junior_count + sophomore_count > 0)
    senior_target, junior_target, sophomore_target = quota_targets(
        rotation_slots=rotation_slots,
        senior_count=senior_count,
        junior_count=junior_count,
        sophomore_count=sophomore_count,
    )
    total = (
        senior_count * senior_target
        + junior_count * junior_target
        + sophomore_count * sophomore_target
    )
    assert total == pytest.approx(rotation_slots, rel=1e-9)


@given(
    rotation_slots=st.integers(min_value=1, max_value=5000),
    senior_count=st.integers(min_value=1, max_value=300),
    junior_count=st.integers(min_value=1, max_value=300),
    sophomore_count=st.integers(min_value=1, max_value=300),
)
def test_targets_hold_the_ratios(
    rotation_slots: int, senior_count: int, junior_count: int, sophomore_count: int
) -> None:
    """Q2. The whole point of the model, asserted directly."""
    senior_target, junior_target, sophomore_target = quota_targets(
        rotation_slots=rotation_slots,
        senior_count=senior_count,
        junior_count=junior_count,
        sophomore_count=sophomore_count,
    )
    assert senior_target / junior_target == pytest.approx(SENIOR_QUOTA_RATIO)
    assert sophomore_target / junior_target == pytest.approx(SOPHOMORE_QUOTA_RATIO)
    assert senior_target < junior_target < sophomore_target, (
        "seniors carry the smallest quota, sophomores the largest"
    )


@given(
    older=st.integers(min_value=1900, max_value=2200),
    delta=st.integers(min_value=1, max_value=50),
    term_start_year=st.integers(min_value=2020, max_value=2050),
    term_is_fall=st.booleans(),
)
def test_seniority_monotone_in_class_year(
    older: int, delta: int, term_start_year: int, term_is_fall: bool
) -> None:
    """Q3. Nobody younger is a senior while someone older is not."""
    younger = older + delta
    kwargs = {"term_start_year": term_start_year, "term_is_fall": term_is_fall}
    if is_senior_in_term(younger, **kwargs):
        assert is_senior_in_term(older, **kwargs)


@given(term_start_year=st.integers(min_value=2020, max_value=2050))
def test_the_same_person_is_a_senior_in_both_halves_of_an_academic_year(
    term_start_year: int,
) -> None:
    """A 2027 graduate is a senior in fall 2026 AND in spring 2027.

    The academic year straddles the calendar year, and the replaced phantom got
    this wrong — it read a 2027 graduate as a junior in a fall 2026 term. That
    was survivable there because it shifted every class uniformly and only the
    ranking mattered. A quota is an absolute target, so the same slip would hand
    seniors the underclassman quota and silently double their load.
    """
    graduating = term_start_year + 1
    assert is_senior_in_term(graduating, term_start_year=term_start_year, term_is_fall=True)
    assert is_senior_in_term(graduating, term_start_year=graduating, term_is_fall=False)


# --- Integration properties: scoring against a real database ---


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
    # Requirements, not just an event row. build_quota_context derives the
    # season's work from event_shift_requirements — deliberately, because
    # `shifts` is populated lazily by the fill and would read as zero work on a
    # calendar nobody has assigned yet. Without this the targets come out at
    # zero and every score is inf, which is a real behaviour but not the one
    # under test here.
    reqs_svc.snapshot_for_event(db, event_id)
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


def _member(member_id: int, index: int, class_year: int | None = None) -> EligibleMember:
    return EligibleMember(
        member_id=member_id,
        member_slug=f"m{index}",
        display_name=f"M{index}",
        class_year=class_year,
        pledge_class=None,
        is_pledge=False,
    )


def test_score_monotone_in_shifts_so_far(db: sqlite3.Connection) -> None:
    """Q4."""
    sem_id, ids, event_id = _seed_world(db, n_members=3)
    # m0=0 shifts, m1=2 shifts, m2=5 shifts; same class_year so targets are equal.
    _assign_n_shifts_to(db, member_id=ids[1], n=2, event_id=event_id, sem_id=sem_id)
    _assign_n_shifts_to(db, member_id=ids[2], n=5, event_id=event_id, sem_id=sem_id)
    members = [_member(mid, i) for i, mid in enumerate(ids)]
    quota = fairness.build_quota_context(db, semester_id=sem_id)
    scored = fairness.sort_by_fairness(
        db, pool=members, semester_id=sem_id, event_date="2026-03-01", quota=quota
    )
    # Sorted ASC by score → m0, m1, m2
    assert [s.member.member_slug for s in scored] == ["m0", "m1", "m2"]
    scores = [s.score for s in scored]
    for a, b in zip(scores, scores[1:], strict=False):
        assert a <= b


def test_a_senior_is_picked_after_an_underclassman_who_has_worked_the_same(
    db: sqlite3.Connection,
) -> None:
    """Q5. "Seniors work less", stated as the thing a brother would check.

    Both have worked exactly two shifts. The senior's smaller target makes two
    shifts a larger fraction of his season, so he scores higher and goes later —
    which is the entire behaviour the ratio exists to produce, and the one a
    mutation to ``target_for`` would silently remove.
    """
    sem_id, ids, event_id = _seed_world(db, n_members=2)
    for mid in ids:
        _assign_n_shifts_to(db, member_id=mid, n=2, event_id=event_id, sem_id=sem_id)
    # SP26 is a spring term starting 2026-01-15, so a 2026 graduate is the senior.
    senior = _member(ids[0], 0, class_year=2026)
    underclassman = _member(ids[1], 1, class_year=2029)
    quota = fairness.build_quota_context(db, semester_id=sem_id)

    scored = {
        s.member.member_slug: s
        for s in fairness.sort_by_fairness(
            db,
            pool=[senior, underclassman],
            semester_id=sem_id,
            event_date="2026-03-01",
            quota=quota,
        )
    }
    assert scored["m0"].target < scored["m1"].target
    assert scored["m0"].shifts_so_far == scored["m1"].shifts_so_far == 2
    assert scored["m0"].score > scored["m1"].score
