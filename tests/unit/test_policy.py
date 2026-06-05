"""Unit tests for ``services.policy``: thresholds + seniority phantom shifts."""

from __future__ import annotations

import pytest

from risk.services import policy


def test_strike_thresholds_are_canonical() -> None:
    assert policy.EXTRA_SHIFT_AT == 2
    assert policy.BAD_STANDING_THRESHOLD == 3
    assert policy.PROBATION_AT == 4
    assert policy.EXPULSION_REVIEW_AT == 5


def test_strike_thresholds_strictly_increase() -> None:
    """Locked monotonicity — if any future bump rearranges these, a test fails."""
    assert (
        policy.EXTRA_SHIFT_AT
        < policy.BAD_STANDING_THRESHOLD
        < policy.PROBATION_AT
        < policy.EXPULSION_REVIEW_AT
    )


@pytest.mark.parametrize(
    ("class_year", "event_year", "expected"),
    [
        # Standard 4-year track in spring 2026
        (2029, 2026, 0.0),  # freshman
        (2028, 2026, 1.0),  # sophomore
        (2027, 2026, 2.0),  # junior
        (2026, 2026, 3.0),  # senior in spring
        (2025, 2026, 4.0),  # super-senior (capped at SENIORITY_CAP_YEARS)
        (2024, 2026, 4.0),  # alumnus-ish — capped
        # Future graduations (gap year, transferred-in late) → no penalty
        (2030, 2026, 0.0),
        (2099, 2026, 0.0),
        # Unknown class_year → no contribution either direction
        (None, 2026, 0.0),
    ],
)
def test_seniority_phantom_shifts(class_year: int | None, event_year: int, expected: float) -> None:
    assert policy.seniority_phantom_shifts(class_year, event_year) == expected


def test_seniority_phantom_shifts_monotone_in_class_year() -> None:
    """For a fixed event year, older members (lower class_year) score >= younger."""
    event_year = 2026
    scores = [policy.seniority_phantom_shifts(cy, event_year) for cy in range(2030, 2024, -1)]
    for a, b in zip(scores, scores[1:], strict=False):
        assert a <= b, f"non-monotone: {scores}"
