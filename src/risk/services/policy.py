"""Governance constants and pure predicates.

Per canonical plan §3.3 and ADR-003 this module has NO DB or repo imports.
Enforced by ``.importlinter`` config + AST test in ``tests/unit/test_dependency_graph.py``.

Strike-axis thresholds (Phase 5) and fairness weights (Phase 4) both live here
so that all tunable governance lives in one inspectable file.
"""

from __future__ import annotations

# --- Strike thresholds (Phase 5) ---

EXTRA_SHIFT_AT = 2
"""At this strike count, the member is assigned an extra (consequence) shift."""

BAD_STANDING_THRESHOLD = 3
"""At this strike count, the member is in bad standing (display-only badge)."""

PROBATION_AT = 4
"""At this strike count, a ``probation`` pending_consequence is created."""

EXPULSION_REVIEW_AT = 5
"""At this strike count, an ``expulsion_review`` pending_consequence is created."""

CONSEQUENCE_KINDS: tuple[str, ...] = ("extra_shift", "probation", "expulsion_review")
"""Threshold-triggered chair-owed actions (ADR-007 — distinct from `strike_categories`)."""


def consequence_kinds_for_count(active_strike_count: int) -> tuple[str, ...]:
    """Threshold-consequences that should exist given a current active-strike count.

    Pure mapping: which `pending_consequences.kind` rows ought to exist for a
    member whose open-strike count in the semester is `active_strike_count`.
    Threshold-emission is monotone in the issuance sequence — once a member has
    ever crossed a threshold, the kind stays in the result. The caller
    (`strike_state.derive`) tracks the max-reached count, not the current count.
    """
    kinds: list[str] = []
    if active_strike_count >= EXTRA_SHIFT_AT:
        kinds.append("extra_shift")
    if active_strike_count >= PROBATION_AT:
        kinds.append("probation")
    if active_strike_count >= EXPULSION_REVIEW_AT:
        kinds.append("expulsion_review")
    return tuple(kinds)


def in_bad_standing(active_strike_count: int) -> bool:
    """True when active-strike count meets the bad-standing threshold."""
    return active_strike_count >= BAD_STANDING_THRESHOLD


# --- Fairness weights (Phase 4) ---

SENIORITY_PHANTOM_SHIFTS_PER_YEAR = 1.0
"""How many phantom shifts each year of seniority adds to a member's score.

Higher score = picked less. Older members (closer to graduation) carry more
phantom shifts and get assigned proportionally less often.
"""

SENIORITY_CAP_YEARS = 4
"""Cap on years-of-seniority so a 5th-year doesn't get an unbounded score.

Freshman → 0, sophomore → 1, junior → 2, senior → 3, super-senior → 4 (capped).
"""


def seniority_phantom_shifts(class_year: int | None, event_year: int) -> float:
    """Phantom-shift score contribution from class_year.

    ``class_year`` is the member's expected graduation year. Mapping for an
    event in year Y:

      - Freshman  (class_year = Y + 3) → 0 phantom shifts
      - Sophomore (class_year = Y + 2) → 1
      - Junior    (class_year = Y + 1) → 2
      - Senior    (class_year = Y    ) → 3
      - 5th-year  (class_year = Y - 1) → 4 (capped at SENIORITY_CAP_YEARS)

    Returns ``0`` when ``class_year`` is unknown — unknown class year should
    not push members up or down the fairness queue.
    """
    if class_year is None:
        return 0.0
    years_of_seniority = (event_year + 3) - class_year
    if years_of_seniority < 0:
        return 0.0
    if years_of_seniority > SENIORITY_CAP_YEARS:
        years_of_seniority = SENIORITY_CAP_YEARS
    return float(years_of_seniority) * SENIORITY_PHANTOM_SHIFTS_PER_YEAR
