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
    ("class_year", "term_start_year", "term_is_fall", "expected"),
    [
        # FA26 — the term this model was written for. class_year is the
        # GRADUATION year, and a fall-2026 senior graduates in May 2027.
        (2027, 2026, True, True),  # senior
        (2028, 2026, True, False),  # junior
        (2029, 2026, True, False),  # sophomore
        (2026, 2026, True, True),  # fifth-year — already past graduating, still senior
        # The same people, one term later. A senior in the fall is a senior in
        # the spring: it is one academic year. The replaced phantom got this
        # wrong in exactly one direction and read the 2027 as a junior.
        (2027, 2027, False, True),
        (2028, 2027, False, False),
        # Future graduation (transferred in late, gap year) is not a senior.
        (2030, 2026, True, False),
        (2099, 2026, True, False),
        # Unknown reads as underclassman: the LARGER quota, so an unrecorded
        # member is over-worked rather than under-worked. Over-worked gets
        # reported; under-worked stays quiet and never gets fixed.
        (None, 2026, True, False),
    ],
)
def test_is_senior_in_term(
    class_year: int | None, term_start_year: int, term_is_fall: bool, expected: bool
) -> None:
    assert (
        policy.is_senior_in_term(
            class_year, term_start_year=term_start_year, term_is_fall=term_is_fall
        )
        is expected
    )


def test_quota_targets_hit_the_ratified_numbers_for_fa26() -> None:
    """The ratified outcome, checked against the real FA26 pool.

    28 eligible seniors, 21 juniors, 11 sophomores, and 318.8 effort under the
    2026-08-26 weights (rides 1.0, door 0.8, bar 0.8, setup 0.4, cleanup 0.4).
    Pinning the arithmetic here means a change to SENIOR_QUOTA_RATIO or
    SOPHOMORE_QUOTA_RATIO has to be a deliberate act with a failing test
    attached, rather than a constant someone nudges.
    """
    senior, junior, sophomore = policy.quota_targets(
        rotation_slots=318.8, senior_count=28, junior_count=21, sophomore_count=11
    )
    assert senior == pytest.approx(2.22, abs=0.01)
    assert junior == pytest.approx(7.39, abs=0.01)
    assert sophomore == pytest.approx(9.24, abs=0.01)


def test_quota_targets_degenerate_to_two_tiers_without_sophomores() -> None:
    """A caller that does not split out sophomores gets the old answer exactly.

    Not an approximation — sophomore_count=0 drops the third term from the
    denominator, so the two-tier solve is a special case of this one rather
    than a separate code path that could drift from it.
    """
    senior, junior, sophomore = policy.quota_targets(
        rotation_slots=543, senior_count=30, junior_count=27
    )
    denominator = 30 * policy.SENIOR_QUOTA_RATIO + 27
    assert junior == pytest.approx(543 / denominator)
    assert senior == pytest.approx(junior * policy.SENIOR_QUOTA_RATIO)
    assert sophomore == pytest.approx(junior * policy.SOPHOMORE_QUOTA_RATIO)


def test_quota_targets_survive_an_empty_pool() -> None:
    """Nobody to work, or no work to do — return zeros, do not divide by zero.

    Reachable on a fresh database: a semester exists, the roster has not been
    imported, and something asks for the targets.
    """
    assert policy.quota_targets(
        rotation_slots=100, senior_count=0, junior_count=0, sophomore_count=0
    ) == (0.0, 0.0, 0.0)
    assert policy.quota_targets(
        rotation_slots=0, senior_count=30, junior_count=17, sophomore_count=10
    ) == (0.0, 0.0, 0.0)


def test_quota_targets_absorb_the_strike_carve_out() -> None:
    """Make-up shifts come off the top, and the targets follow.

    21 strike shifts are penalties owed on top of a normal season, so they are
    not part of anyone's quota. Taking them out of rotation_slots lowers every
    target slightly — which is correct, because those 21 bodies are staffing
    parties that the rotation therefore does not have to.
    """
    full, _, _ = policy.quota_targets(
        rotation_slots=543, senior_count=30, junior_count=17, sophomore_count=10
    )
    carved, _, _ = policy.quota_targets(
        rotation_slots=543 - 21, senior_count=30, junior_count=17, sophomore_count=10
    )
    assert carved < full


def test_sophomores_carry_more_than_juniors_who_carry_more_than_seniors() -> None:
    """The whole point of the third tier, asserted as an ordering.

    Stated as an ordering rather than as two ratios so that it keeps meaning
    something if either constant is retuned: a chair may argue about how much
    harder a sophomore works, but a term where he works LESS than a junior is a
    bug in every version of the policy.
    """
    senior, junior, sophomore = policy.quota_targets(
        rotation_slots=318.8, senior_count=28, junior_count=21, sophomore_count=11
    )
    assert senior < junior < sophomore


def test_sophomore_tier_takes_freshmen_too() -> None:
    """A freshman is cooked at least as hard as a sophomore, never less.

    The roster has no 2030s in it today, so an ``==`` here would look correct
    all term and go wrong the first time a spring pledge class is entered.
    """
    kw = {"term_start_year": 2026, "term_is_fall": True}
    assert policy.is_sophomore_or_younger_in_term(2029, **kw)  # sophomore
    assert policy.is_sophomore_or_younger_in_term(2030, **kw)  # freshman
    assert not policy.is_sophomore_or_younger_in_term(2028, **kw)  # junior
    assert not policy.is_sophomore_or_younger_in_term(2027, **kw)  # senior
    assert not policy.is_sophomore_or_younger_in_term(None, **kw)  # unrecorded


# --- Pledge-class ordinal (seniority-inverted tiebreaker) ---


def test_greek_order_spans_alpha_to_omega() -> None:
    assert policy.GREEK_PLEDGE_CLASS_ORDER["alpha"] == 1
    assert policy.GREEK_PLEDGE_CLASS_ORDER["omega"] == 24
    assert len(policy.GREEK_PLEDGE_CLASS_ORDER) == 24


def test_pledge_class_ordinal_follows_greek_not_lexical_order() -> None:
    """Greek order is Zeta(6) < Eta(7) < Theta(8)."""
    zeta = policy.pledge_class_ordinal("Zeta")
    eta = policy.pledge_class_ordinal("Eta")
    theta = policy.pledge_class_ordinal("Theta")
    assert zeta is not None and eta is not None and theta is not None
    assert zeta < eta < theta
    # Lexical sort would give the wrong order (Eta < Theta < Zeta) — confirm the
    # labels really do disagree, so this test is meaningfully guarding ordinal use.
    assert sorted(["Eta", "Theta", "Zeta"]) == ["Eta", "Theta", "Zeta"]


@pytest.mark.parametrize("label", ["zeta", "Zeta", "ZETA", "  Zeta  "])
def test_pledge_class_ordinal_is_case_and_whitespace_insensitive(label: str) -> None:
    assert policy.pledge_class_ordinal(label) == 6


@pytest.mark.parametrize("label", [None, "", "not-a-letter", "alpha-alpha"])
def test_pledge_class_ordinal_unmapped_is_none(label: str | None) -> None:
    assert policy.pledge_class_ordinal(label) is None
