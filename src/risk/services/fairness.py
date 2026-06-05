"""Fairness scoring for auto-assign.

Goal: members with fewer shifts go first, with seniors carrying phantom
shifts so they're picked less than freshmen all else equal. Score is the
sort key; lower score = picked first.

Pinned to ``event.date`` (ADR-009): "shifts so far" counts shifts for events
dated on-or-before the current event in the same semester. Reruns on the
same event therefore see the same scores regardless of wall-clock time.

Tiebreaker (ADR-013): ``last_assigned_at ASC NULLS FIRST`` — members who
have never been assigned go before members who were just assigned.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from risk.repos import shifts as shifts_repo
from risk.services.eligibility import EligibleMember
from risk.services.policy import seniority_phantom_shifts


@dataclass(frozen=True, slots=True)
class ScoredMember:
    member: EligibleMember
    shifts_so_far: int
    seniority_bonus: float
    score: float
    last_assigned_at: str | None


def _event_year(event_date: str) -> int:
    return int(event_date.split("-")[0])


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
    """Score the pool and return it sorted by score ASC, last_assigned_at ASC NULLS FIRST.

    Stable order on full ties: ``member_slug`` ASC, which matches the alphabetical
    ordering already returned by ``eligible_for`` — gives deterministic results
    when score + last_assigned_at coincide, without the chair having to think
    about seed effects for natural ties.
    """
    scored = [
        score_member(conn, member=m, semester_id=semester_id, event_date=event_date) for m in pool
    ]
    scored.sort(
        key=lambda s: (
            s.score,
            # NULLS FIRST: never-assigned (None) sorts before any timestamp.
            (0, "") if s.last_assigned_at is None else (1, s.last_assigned_at),
            s.member.member_slug,
        )
    )
    return scored
