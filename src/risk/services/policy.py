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

SENIOR_QUOTA_RATIO = 0.43
"""Shifts a senior is expected to work per shift an underclassman works.

Seniors work less. The question is HOW they work less, and the two answers are
not interchangeable.

The rejected answer was a phantom: add N imaginary shifts to a senior's tally so
he sorts behind everyone. That DEFERS him — he is picked last early in the term
and only surfaces once the underclassmen have caught up, which stacks his real
work into the back half of the calendar. Fine in a vacuum. Not fine here,
because the back half of this calendar is where pledging starts and the
brothers get dropped. A deferred senior's shifts evaporate at the cutoff, and
he ends the term having worked almost nothing while a sophomore worked twelve.

A ratio RATE-LIMITS instead. Every class advances through its own quota in
parallel, so at any date each class has completed the same FRACTION of its
season. Truncate the calendar anywhere — and the pledge date moved twice in one
conversation — and every class is cut at the same percentage. That invariance is
the entire reason this constant exists; see ``quota_targets``.

0.43 comes from the FA26 pool: 30 eligible seniors against 27 eligible
juniors/sophomores over the counted calendar. HANDOFF states the intended
outcome as "Senior 6, Jr/Soph 13.5".
"""


def is_senior_in_term(class_year: int | None, *, term_start_year: int, term_is_fall: bool) -> bool:
    """True when ``class_year`` graduates at the end of THIS academic year.

    ``class_year`` is the expected GRADUATION year, and an academic year spans
    two calendar years: 2026-27 runs from fall 2026 to spring 2027. So the same
    person is a senior in both halves, and the graduating year is one ahead of
    the calendar year in the fall and equal to it in the spring.

    Getting this wrong is not hypothetical. ``seniority_phantom_shifts`` was
    written for spring semesters and reads a 2027 graduate as a junior in a fall
    2026 term. That was harmless there because it shifted every class uniformly
    and only ranking mattered — but a quota is an absolute target, not a rank,
    so the same mistake here would hand seniors the underclassman quota.

    ``<=`` rather than ``==`` so a fifth-year, whose graduation year has already
    passed, is still a senior rather than falling through to the larger quota.
    An unknown ``class_year`` is treated as an underclassman: it is the higher
    target, so an unrecorded member is over-worked rather than under-worked, and
    an over-worked brother complains where an under-worked one stays quiet.
    """
    if class_year is None:
        return False
    graduating_year = term_start_year + 1 if term_is_fall else term_start_year
    return class_year <= graduating_year


def quota_targets(
    *, rotation_slots: float, senior_count: int, underclass_count: int
) -> tuple[float, float]:
    """Season shift targets as ``(senior_target, underclass_target)``.

    Solves for the pair that both (a) sits at ``SENIOR_QUOTA_RATIO``, and
    (b) sums across the actual pool to exactly the slots that must be staffed::

        senior_count * (ratio * U) + underclass_count * U = rotation_slots

    Derived from live counts rather than hardcoded so that the targets follow
    the roster. A brother going alumni, an event being cancelled, or the strike
    make-up shifts coming off the top all change ``rotation_slots`` or a pool
    size, and a hardcoded "Senior 6 / Jr-Soph 13.5" would quietly stop summing
    to the work that actually exists — which is precisely the failure that makes
    a published target indefensible.

    Returns ``(0.0, 0.0)`` when there is nobody to work, rather than dividing by
    zero. Callers must treat a zero target as "never pick this member" instead
    of dividing by it.
    """
    denominator = senior_count * SENIOR_QUOTA_RATIO + underclass_count
    if denominator <= 0 or rotation_slots <= 0:
        return (0.0, 0.0)
    underclass_target = rotation_slots / denominator
    return (underclass_target * SENIOR_QUOTA_RATIO, underclass_target)


DJ_NIGHT_CREDIT = 0.3
"""Rotation credit a DJ earns per night behind the decks.

A DJ night is not risk work and does not appear in anyone's shift total — that
is settled (0016), and the ledger would be lying if it did. But it IS a night on
site, and pretending otherwise produced an absurdity: one of the two DJs was
carrying 22 DJ nights AND a full 13-turn rotation quota, so the app had him at a
party 35 times out of 44 while reporting 13.

The credit it replaced was a flat 2.0 — a thumb on the scale sized for somebody
who might DJ occasionally, applied to somebody doing it every other party. Two
phantom shifts against twenty-two nights is not a correction, it is a rounding
error.

0.3 says a night spent DJing is worth about a third of a party night against
your quota. Not 1.0, because it is a different and easier job, and the chapter
would rightly object to a DJ "working off" a full risk shift by playing music.
Not 0.0, because he is still there, still sober, still not at home.
"""


# --- Pledge-class ordering (seniority-inverted tiebreaker, R3.2-A chain) ---

GREEK_PLEDGE_CLASS_ORDER: dict[str, int] = {
    name: ordinal
    for ordinal, name in enumerate(
        (
            "alpha",
            "beta",
            "gamma",
            "delta",
            "epsilon",
            "zeta",
            "eta",
            "theta",
            "iota",
            "kappa",
            "lambda",
            "mu",
            "nu",
            "xi",
            "omicron",
            "pi",
            "rho",
            "sigma",
            "tau",
            "upsilon",
            "phi",
            "chi",
            "psi",
            "omega",
        ),
        start=1,
    )
}
"""Greek-letter pledge class → ordinal (Alpha=1 … Omega=24).

Later letters denote newer pledge classes. The fairness tiebreaker prefers the
*later* (newer) class first, so newer pledges pick up shifts before older
brothers when fairness scores and class_year both tie. Lexical ordering would be
wrong here (Eta < Theta < Zeta alphabetically, but Zeta < Eta < Theta in Greek),
so ordering goes through this ordinal. Unmapped labels sort last (neutral)."""


def pledge_class_ordinal(pledge_class: str | None) -> int | None:
    """Greek-alphabet position of a pledge-class label, or None if unmapped."""
    if pledge_class is None:
        return None
    return GREEK_PLEDGE_CLASS_ORDER.get(pledge_class.strip().lower())
