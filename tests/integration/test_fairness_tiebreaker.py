"""Integration tests for the R3.2-A seniority-inverted tiebreaker chain.

The fairness ``score`` already encodes "seniors work less" via phantom shifts.
These tests pin the behaviour when scores *tie*: younger class_year first, then
newer pledge class first, then last_assigned_at, then slug. Scores are made to
tie deliberately by compensating the seniority bonus with real assigned shifts.
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
from risk.services.eligibility import EligibleMember

pytestmark = pytest.mark.integration

EVENT_DATE = "2026-02-14"  # event_year 2026


def _seed(db: sqlite3.Connection) -> tuple[int, int]:
    sem_id = semesters_repo.insert(
        db, name="SP26", starts_on="2026-01-15", ends_on="2026-05-15"
    )
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
        shift_id = shifts_repo.insert_open(
            db, event_id=ev_id, shift_type_id=door.id, slot_index=0
        )
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
    scored = fairness.sort_by_fairness(
        db, pool=pool, semester_id=sem_id, event_date=EVENT_DATE
    )
    return [s.member.member_slug for s in scored]


def test_class_year_tiebreaker_picks_younger_first(db: sqlite3.Connection) -> None:
    sem_id, status_id = _seed(db)
    # senior: class_year 2026 → bonus 3, 0 real shifts → score 3.
    senior = _make_member(
        db, sem_id=sem_id, status_id=status_id, slug="senior",
        class_year=2026, pledge_class=None, n_shifts=0,
    )
    # soph: class_year 2028 → bonus 1, 2 real shifts → score 3. TIE.
    soph = _make_member(
        db, sem_id=sem_id, status_id=status_id, slug="soph",
        class_year=2028, pledge_class=None, n_shifts=2,
    )
    scored = fairness.sort_by_fairness(
        db, pool=[senior, soph], semester_id=sem_id, event_date=EVENT_DATE
    )
    assert scored[0].score == scored[1].score == pytest.approx(3.0)  # genuine tie
    # Younger (soph) picks up the shift first despite having worked more.
    assert [s.member.member_slug for s in scored] == ["soph", "senior"]


def test_pledge_class_tiebreaker_picks_newer_greek_first(db: sqlite3.Connection) -> None:
    sem_id, status_id = _seed(db)
    # Same class_year + same shifts → tie on score AND class_year. PC decides.
    zeta = _make_member(
        db, sem_id=sem_id, status_id=status_id, slug="zeta-bro",
        class_year=2028, pledge_class="Zeta", n_shifts=0,  # Greek ordinal 6
    )
    eta = _make_member(
        db, sem_id=sem_id, status_id=status_id, slug="eta-bro",
        class_year=2028, pledge_class="Eta", n_shifts=0,  # Greek ordinal 7 (newer)
    )
    # Newer pledge class (Eta) first — note lexical order would put eta-bro first
    # anyway, so flip the slugs' alpha order to prove PC, not slug, is deciding.
    assert _order(db, sem_id, [zeta, eta]) == ["eta-bro", "zeta-bro"]


def test_pledge_class_beats_slug_alpha_order(db: sqlite3.Connection) -> None:
    sem_id, status_id = _seed(db)
    # Theta (8, newest) has a slug that sorts LAST alphabetically; if PC works it
    # still comes first, proving PC outranks the slug stabilizer.
    alpha = _make_member(
        db, sem_id=sem_id, status_id=status_id, slug="aaa",
        class_year=2028, pledge_class="Zeta", n_shifts=0,
    )
    theta = _make_member(
        db, sem_id=sem_id, status_id=status_id, slug="zzz",
        class_year=2028, pledge_class="Theta", n_shifts=0,
    )
    assert _order(db, sem_id, [alpha, theta]) == ["zzz", "aaa"]


def test_unknown_class_year_sorts_last_on_tie(db: sqlite3.Connection) -> None:
    sem_id, status_id = _seed(db)
    # unknown: class_year None → bonus 0, 1 real shift → score 1.
    unknown = _make_member(
        db, sem_id=sem_id, status_id=status_id, slug="unknown",
        class_year=None, pledge_class=None, n_shifts=1,
    )
    # known: class_year 2028 → bonus 1, 0 real shifts → score 1. TIE.
    known = _make_member(
        db, sem_id=sem_id, status_id=status_id, slug="known",
        class_year=2028, pledge_class=None, n_shifts=0,
    )
    assert _order(db, sem_id, [unknown, known]) == ["known", "unknown"]


def test_tiebreaker_is_reproducible(db: sqlite3.Connection) -> None:
    sem_id, status_id = _seed(db)
    pool = [
        _make_member(
            db, sem_id=sem_id, status_id=status_id, slug=f"m{i}",
            class_year=2026 + (i % 3), pledge_class="Zeta", n_shifts=i % 2,
        )
        for i in range(6)
    ]
    first = _order(db, sem_id, pool)
    second = _order(db, sem_id, list(reversed(pool)))
    assert first == second, "tiebreaker order must not depend on input order"
