"""Fairness scoring for auto-assign.

Goal: members with fewer shifts go first, with seniors carrying phantom
shifts so they're picked less than freshmen all else equal. Score is the
sort key; lower score = picked first.

Pinned to ``event.date`` (ADR-009): "shifts so far" counts shifts for events
dated on-or-before the current event in the same semester. Reruns on the
same event therefore see the same scores regardless of wall-clock time.

Tiebreaker chain (R3.2-A), applied in order once fairness ``score`` ties:

  1. ``class_year`` younger-first — newer brothers pick up the shift before
     older ones (continues the seniors-work-less philosophy already baked into
     the score). Unknown class_year is neutral (sorts last).
  2. ``pledge_class`` newer-first — later Greek letter picks up first. Ordered
     by Greek-alphabet ordinal, not lexically. Unmapped/None sorts last.
  3. ``last_assigned_at`` ASC NULLS FIRST (ADR-013) — never-assigned before
     just-assigned. Pinned to ``event.date``, so it carries no wall-clock.
  4. ``member_slug`` ASC — final deterministic stabilizer.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from risk.repos import shifts as shifts_repo
from risk.services.eligibility import EligibleMember
from risk.services.policy import pledge_class_ordinal, seniority_phantom_shifts


@dataclass(frozen=True, slots=True)
class ScoredMember:
    member: EligibleMember
    shifts_so_far: int
    seniority_bonus: float
    score: float
    last_assigned_at: str | None


def _event_year(event_date: str) -> int:
    return int(event_date.split("-")[0])


def _younger_first_rank(class_year: int | None) -> float:
    """Sort key for younger-first: higher grad year → smaller key. None last."""
    if class_year is None:
        return float("inf")
    return float(-class_year)


def _newer_pc_first_rank(pledge_class: str | None) -> float:
    """Sort key for newer-PC-first: higher Greek ordinal → smaller key. Unmapped last."""
    ordinal = pledge_class_ordinal(pledge_class)
    if ordinal is None:
        return float("inf")
    return float(-ordinal)


def score_member(
    conn: sqlite3.Connection,
    *,
    member: EligibleMember,
    semester_id: int,
    event_date: str,
) -> ScoredMember:
    """Compute the fairness score for one member relative to one event."""
    shifts_so_far = shifts_repo.count_assignments_in_semester_through_date(
        conn,
        member_id=member.member_id,
        semester_id=semester_id,
        on_or_before=event_date,
    )
    seniority_bonus = seniority_phantom_shifts(member.class_year, _event_year(event_date))
    score = float(shifts_so_far) + seniority_bonus
    last = shifts_repo.last_assigned_at(conn, member.member_id)
    return ScoredMember(
        member=member,
        shifts_so_far=shifts_so_far,
        seniority_bonus=seniority_bonus,
        score=score,
        last_assigned_at=last,
    )


def sort_by_fairness(
    conn: sqlite3.Connection,
    *,
    pool: list[EligibleMember],
    semester_id: int,
    event_date: str,
) -> list[ScoredMember]:
    """Score the pool and return it sorted by the R3.2-A tiebreaker chain.

    Sort order (all ASC on the computed keys): ``score`` → ``class_year``
    younger-first → ``pledge_class`` newer-first → ``last_assigned_at`` NULLS
    FIRST → ``member_slug``. Every key is derived from member attributes or
    ``event.date``, so results are fully deterministic and reproducible across
    re-runs and chairs (no wall-clock, no seed dependence for natural ties).
    """
    scored = [
        score_member(conn, member=m, semester_id=semester_id, event_date=event_date) for m in pool
    ]
    scored.sort(
        key=lambda s: (
            s.score,
            _younger_first_rank(s.member.class_year),
            _newer_pc_first_rank(s.member.pledge_class),
            # NULLS FIRST: never-assigned (None) sorts before any timestamp.
            (0, "") if s.last_assigned_at is None else (1, s.last_assigned_at),
            s.member.member_slug,
        )
    )
    return scored
