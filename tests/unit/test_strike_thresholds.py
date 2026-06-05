"""Unit tests for pure threshold-consequence mapping in ``services.policy``."""

from __future__ import annotations

import pytest

from risk.services.policy import (
    BAD_STANDING_THRESHOLD,
    EXPULSION_REVIEW_AT,
    EXTRA_SHIFT_AT,
    PROBATION_AT,
    consequence_kinds_for_count,
    in_bad_standing,
)


@pytest.mark.parametrize(
    "count,expected",
    [
        (0, ()),
        (1, ()),
        (EXTRA_SHIFT_AT, ("extra_shift",)),
        (3, ("extra_shift",)),
        (PROBATION_AT, ("extra_shift", "probation")),
        (EXPULSION_REVIEW_AT, ("extra_shift", "probation", "expulsion_review")),
        (10, ("extra_shift", "probation", "expulsion_review")),
    ],
)
def test_consequence_kinds_for_count(count: int, expected: tuple[str, ...]) -> None:
    assert consequence_kinds_for_count(count) == expected


def test_consequence_kinds_monotone_in_count() -> None:
    """Adding strikes never removes a kind from the expected set."""
    prev: tuple[str, ...] = ()
    for n in range(0, 8):
        cur = consequence_kinds_for_count(n)
        assert set(prev).issubset(set(cur))
        prev = cur


@pytest.mark.parametrize(
    "count,expected",
    [
        (0, False),
        (BAD_STANDING_THRESHOLD - 1, False),
        (BAD_STANDING_THRESHOLD, True),
        (BAD_STANDING_THRESHOLD + 5, True),
    ],
)
def test_in_bad_standing(count: int, expected: bool) -> None:
    assert in_bad_standing(count) is expected
