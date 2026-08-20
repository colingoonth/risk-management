"""Integration tests for the R3.2-A seniority-inverted tiebreaker chain.

The fairness ``score`` encodes "seniors work less" as a smaller season quota.
These tests pin the behaviour when scores *tie*: younger class_year first, then
newer pledge class first, then last_assigned_at, then slug.

How the ties are constructed changed with the model, and the change is worth
understanding rather than working around. Under the old phantom, a senior with
0 shifts and a sophomore with 2 both scored 3.0, so a cross-class tie was easy
to manufacture and these tests manufactured one. Under a ratio, score is
``shifts / target``, and a cross-class tie needs ``n / 0.43 == m`` — the
smallest integer solution is 43 shifts against 100. Cross-class ties are
effectively unreachable now, which is a property of the model rather than a
problem with it: the score does more of the deciding across classes.

So these seed the tie the way the season actually produces one — at the first
event, where everyone is at zero. That is not a contrivance; it is the single
most common state the chain runs in, and with 57 members against 13 slots a
night it stays the common state for weeks.
"""

from __future__ import annotations

import sqlite3

import pytest

from risk.repos import event_types as etypes_repo
from risk.repos import events as events_repo
from risk.repos import member_statuses as statuses_repo
from risk.repos import members as members_repo
from risk.repos import pledge_modes as pmodes_repo
from risk.repos import semesters as semesters_repo
from risk.repos import shift_types as stypes_repo
from risk.repos import shifts as shifts_repo
from risk.services import fairness
from risk.services import shift_requirements as reqs_svc
from risk.services.eligibility import EligibleMember

pytestmark = pytest.mark.integration

EVENT_DATE = "2026-02-14"  # event_year 2026


def _seed(db: sqlite3.Connection) -> tuple[int, int]:
    sem_id = semesters_repo.insert(db, name="SP26", starts_on="2026-01-15", ends_on="2026-05-15")
    # A real event with real requirements, so the season has work in it.
    # build_quota_context divides the work among the pool; with no requirement
    # rows anywhere the targets come out at zero, every score is inf, and these
    # tests would pass on a degenerate all-ties sort that proves nothing.
    et = etypes_repo.get_by_slug(db, "mixer")
    assert et is not None
    quota_event_id = events_repo.insert(
        db,
        semester_id=sem_id,
        event_type_id=et.id,
        display_name="the-event-being-filled",
        date=EVENT_DATE,
    )
    reqs_svc.snapshot_for_event(db, quota_event_id)
    active = statuses_repo.get_by_slug(db, "active")
    assert active is not None
    return sem_id, active.id


def _make_member(
    db: sqlite3.Connection,
    *,
    sem_id: int,
    status_id: int,
    slug: str,
    class_year: int | None,
    pledge_class: str | None,
    n_shifts: int,
) -> EligibleMember:
    """Insert a member, give them ``n_shifts`` past assignments, return their pool row."""
    mid = members_repo.insert(
        db,
        slug=slug,
        display_name=slug.upper(),
        status_id=status_id,
        class_year=class_year,
        pledge_class=pledge_class,
    )
    et = etypes_repo.get_by_slug(db, "mixer")
    door = stypes_repo.get_by_slug(db, "door")
    pm = pmodes_repo.get(db, "normal")
    assert et is not None and door is not None and pm is not None
    for i in range(n_shifts):
        ev_id = events_repo.insert(
            db,
            semester_id=sem_id,
            event_type_id=et.id,
            display_name=f"{slug}-past-{i}",
            date="2026-02-01",  # on-or-before EVENT_DATE → counts toward score
            host_house_id=None,
        )
        shift_id = shifts_repo.insert_open(db, event_id=ev_id, shift_type_id=door.id, slot_index=0)
        shifts_repo.assign(
            db,
            shift_id=shift_id,
            member_id=mid,
            effective_pledge_mode_id=pm.id,
            assigned_at="2026-02-01",
        )
    return EligibleMember(
        member_id=mid,
        member_slug=slug,
        display_name=slug.upper(),
        class_year=class_year,
        pledge_class=pledge_class,
        is_pledge=False,
    )


def _order(db: sqlite3.Connection, sem_id: int, pool: list[EligibleMember]) -> list[str]:
    quota = fairness.build_quota_context(db, semester_id=sem_id)
    scored = fairness.sort_by_fairness(
        db, pool=pool, semester_id=sem_id, event_date=EVENT_DATE, quota=quota
    )
    return [s.member.member_slug for s in scored]


def test_class_year_tiebreaker_picks_younger_first(db: sqlite3.Connection) -> None:
    """At the first event of the term, nobody has worked and class_year decides.

    Both are at zero, so both score exactly 0.0 however different their targets
    are — ``0 / 5.6`` and ``0 / 13.1`` are the same number. The assertion on the
    targets is the load-bearing one: it proves this is a real tie between two
    members the model treats differently, not two members who happen to share a
    quota.
    """
    sem_id, status_id = _seed(db)
    senior = _make_member(
        db,
        sem_id=sem_id,
        status_id=status_id,
        slug="senior",
        class_year=2026,
        pledge_class=None,
        n_shifts=0,
    )
    soph = _make_member(
        db,
        sem_id=sem_id,
        status_id=status_id,
        slug="soph",
        class_year=2028,
        pledge_class=None,
        n_shifts=0,
    )
    quota = fairness.build_quota_context(db, semester_id=sem_id)
    scored = fairness.sort_by_fairness(
        db, pool=[senior, soph], semester_id=sem_id, event_date=EVENT_DATE, quota=quota
    )
    assert scored[0].score == scored[1].score == pytest.approx(0.0)  # genuine tie
    targets = {s.member.member_slug: s.target for s in scored}
    assert targets["senior"] < targets["soph"], (
        "the tie must be between members on DIFFERENT quotas, or it proves nothing"
    )
    # Younger picks up the shift first.
    assert [s.member.member_slug for s in scored] == ["soph", "senior"]


def test_a_senior_who_has_worked_still_goes_after_an_idle_sophomore(
    db: sqlite3.Connection,
) -> None:
    """The cross-class case the old tie was really testing, stated as an order.

    Under the phantom, a senior with 0 shifts and a sophomore with 2 scored
    identically and the chain broke the tie. Under the ratio there is no tie —
    the senior has spent 0% of a small quota and the sophomore 15% of a large
    one — so the SCORE puts the senior first, on the merits, without needing a
    tiebreak at all. Asserting the order rather than the equality keeps the
    original intent and drops the arithmetic that no longer holds.
    """
    sem_id, status_id = _seed(db)
    senior = _make_member(
        db,
        sem_id=sem_id,
        status_id=status_id,
        slug="senior",
        class_year=2026,
        pledge_class=None,
        n_shifts=0,
    )
    soph = _make_member(
        db,
        sem_id=sem_id,
        status_id=status_id,
        slug="soph",
        class_year=2028,
        pledge_class=None,
        n_shifts=2,
    )
    quota = fairness.build_quota_context(db, semester_id=sem_id)
    scored = fairness.sort_by_fairness(
        db, pool=[senior, soph], semester_id=sem_id, event_date=EVENT_DATE, quota=quota
    )
    assert scored[0].score < scored[1].score, "not a tie any more — the score decides"
    assert [s.member.member_slug for s in scored] == ["senior", "soph"]


def test_pledge_class_tiebreaker_picks_newer_greek_first(db: sqlite3.Connection) -> None:
    sem_id, status_id = _seed(db)
    # Same class_year + same shifts → tie on score AND class_year. PC decides.
    zeta = _make_member(
        db,
        sem_id=sem_id,
        status_id=status_id,
        slug="zeta-bro",
        class_year=2028,
        pledge_class="Zeta",
        n_shifts=0,  # Greek ordinal 6
    )
    eta = _make_member(
        db,
        sem_id=sem_id,
        status_id=status_id,
        slug="eta-bro",
        class_year=2028,
        pledge_class="Eta",
        n_shifts=0,  # Greek ordinal 7 (newer)
    )
    # Newer pledge class (Eta) first — note lexical order would put eta-bro first
    # anyway, so flip the slugs' alpha order to prove PC, not slug, is deciding.
    assert _order(db, sem_id, [zeta, eta]) == ["eta-bro", "zeta-bro"]


def test_pledge_class_beats_slug_alpha_order(db: sqlite3.Connection) -> None:
    sem_id, status_id = _seed(db)
    # Theta (8, newest) has a slug that sorts LAST alphabetically; if PC works it
    # still comes first, proving PC outranks the slug stabilizer.
    alpha = _make_member(
        db,
        sem_id=sem_id,
        status_id=status_id,
        slug="aaa",
        class_year=2028,
        pledge_class="Zeta",
        n_shifts=0,
    )
    theta = _make_member(
        db,
        sem_id=sem_id,
        status_id=status_id,
        slug="zzz",
        class_year=2028,
        pledge_class="Theta",
        n_shifts=0,
    )
    assert _order(db, sem_id, [alpha, theta]) == ["zzz", "aaa"]


def test_unknown_class_year_sorts_last_on_tie(db: sqlite3.Connection) -> None:
    """An unrecorded class_year is neutral on score and last on the tiebreak.

    ``is_senior_in_term`` reads None as an underclassman, so the unknown member
    draws the SAME (larger) target as the known 2028 — deliberately, since
    over-working an unrecorded member is the direction that gets noticed and
    corrected. Both idle, so both score 0.0 and the chain runs, where None sorts
    behind any real year.
    """
    sem_id, status_id = _seed(db)
    unknown = _make_member(
        db,
        sem_id=sem_id,
        status_id=status_id,
        slug="unknown",
        class_year=None,
        pledge_class=None,
        n_shifts=0,
    )
    known = _make_member(
        db,
        sem_id=sem_id,
        status_id=status_id,
        slug="known",
        class_year=2028,
        pledge_class=None,
        n_shifts=0,
    )
    quota = fairness.build_quota_context(db, semester_id=sem_id)
    scored = fairness.sort_by_fairness(
        db, pool=[unknown, known], semester_id=sem_id, event_date=EVENT_DATE, quota=quota
    )
    assert scored[0].score == scored[1].score == pytest.approx(0.0)
    assert {s.target for s in scored} == {scored[0].target}, "same quota, so a real tie"
    assert [s.member.member_slug for s in scored] == ["known", "unknown"]


def test_tiebreaker_is_reproducible(db: sqlite3.Connection) -> None:
    sem_id, status_id = _seed(db)
    pool = [
        _make_member(
            db,
            sem_id=sem_id,
            status_id=status_id,
            slug=f"m{i}",
            class_year=2026 + (i % 3),
            pledge_class="Zeta",
            n_shifts=i % 2,
        )
        for i in range(6)
    ]
    first = _order(db, sem_id, pool)
    second = _order(db, sem_id, list(reversed(pool)))
    assert first == second, "tiebreaker order must not depend on input order"
