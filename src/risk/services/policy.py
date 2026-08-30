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

SENIOR_QUOTA_RATIO = 0.30
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

0.43 came from the FA26 pool as first drawn: 30 eligible seniors against 27
eligible juniors/sophomores. HANDOFF stated the intended outcome as
"Senior 6, Jr/Soph 13.5".

CUT TO 0.30 on 2026-08-26, at Colin's call, because the seniors said they had
too many shifts and the effort re-weighting on its own does not answer that.
Worth writing down, because the reasoning is not obvious and the next chair will
otherwise reach for the weights again:

Lowering the effort weight of setup and cleanup shrinks the total effort the
term has to absorb AND shrinks every quota derived from it, by close to the same
proportion. So a member who works mostly setup needs about the same NUMBER of
setups either way -- modelled against the real 546-slot FA26 calendar, the
re-weighting moved a senior from 6.0 shifts a term to 5.3. What the weights DO
buy is the mix: door plus rides now costs 1.8 of a senior's 3.11 quota, 58% of
his season in two shifts against 41% before, so the fairness sort pushes him off
party nights and onto setup without anybody hand-editing it.

The COUNT is this ratio, and only this ratio. 0.30 puts a senior at roughly 3.4
shifts a term and moves an underclassman from 7.24 to 7.89 effort -- about half
a shift more each, spread across 32 of them, which is the whole reason it is
affordable.
"""


SENIOR_NIGHT_TYPE_HARD_CAPS: dict[str, int] = {"driver": 2}
"""Per-SEASON ceiling a senior may not pass, as opposed to the soft target below.

Colin, 2026-08-30: "Id like to have seniors at max 2 rides a sem but pref 1."
Two numbers, so two mechanisms. The preference is SENIOR_NIGHT_TYPE_CAPS below,
which sorts a senior on his second rides shift behind everyone and lets him
through only if the post would otherwise be empty. This is the ceiling: at three
he is removed from the pool outright.

Hard is defensible here in a way it is not for the Krush ban, because the
constraint is on ONE MAN rather than on a whole class. Removing every senior can
strand a post; removing the individuals who have already driven twice cannot,
because the underclassmen and every senior under the cap are still in the pool.

Only rides. Door has a soft cap and no ceiling — standing at a door is not the
job anybody was complaining about.
"""

MAX_SENIOR_FRACTION_PER_EVENT = 0.4
"""Most of one event's counted slots that may go to seniors.

"Seniors can now work but dont like flood it with just seniors." Measured on the
first build after the block-1 senior steer was lifted, without this: 8 Sep, 11
Sep, 12 Sep and 26 Sep each came out at ten or eleven seniors from twelve slots,
while 15, 18, 19 and 22 Sep had none at all. Entire nights staffed by the class
that works least, and entire nights with none of them.

That is the ratio model behaving correctly and looking terrible. Holding seniors
out of a block leaves every one of them on a score of zero, so when the steer
lifts they are all at the front of the queue at once, fill their small season
quota in a burst, and then disappear for the rest of the term. Nothing was
wrong with any individual assignment; the DISTRIBUTION was indefensible.

A fraction rather than a count because events are not the same size — twelve
slots on a mixer, fifteen on a dage. 0.4 of twelve is four, which is close to
the proportionate share of a pool that is 46% senior but carries a 0.30 quota
ratio, and it leaves the majority of every crew to everyone else.

Like every other cap here it YIELDS rather than strand a post.
"""

MAX_SENIORS_PER_EVENT_SHIFT: dict[str, int] = {"driver": 1}
"""Most seniors allowed on ONE crew at ONE event.

"Try to keep seniors off rides or at least not 2 seniors on one ride shift."
The season caps above limit what any single man works; they say nothing about
who he works it WITH, so three seniors under their own caps could still crew the
same night's rides between them. Rides is the job the seniors like least and the
one where a thin crew is most visible, so a night staffed entirely by seniors is
exactly the picture this is meant to prevent.

Applied while seating, not while filtering: the count is of seniors already
placed on THIS shift type at THIS event, so the cap binds on the crew being
built rather than on the pool. If the remaining pool holds nobody else, the slot
is still filled — an unstaffed rides post is worse than two seniors on one.
"""

SENIOR_NIGHT_TYPE_CAPS: dict[str, int] = {"door": 1, "driver": 1}
"""Per-SEASON soft cap on the party-night posts a senior is steered into.

The seniors will work, but they want the easy jobs: setup and cleanup are two
hours they choose, door and rides are a Saturday night standing sober at the
party. Colin's instruction was "keep them to 1 door and 1 rides at most and then
throw them into setup and cleanup".

SOFT, not hard, and the distinction is load-bearing. Over the term there are 86
door slots and 98 rides slots against 32 underclassmen; a hard filter would run
the underclass pool dry on a bad night and leave a post unstaffed, which is the
one outcome a risk schedule cannot have. Instead an over-cap senior drops into
the same deprioritized tier that soft unavailability uses -- he sorts behind
every other candidate and is picked only when the alternative is an empty post.
A tier rather than a score penalty for the reason given in
``fairness.sort_by_fairness``: any penalty large enough to be reliable is
indistinguishable from a tier, and it is one fewer number to tune.

PER SEASON, not per two-week block. With 28 seniors against 14 door and 16 rides
slots in a fortnight, a per-block cap never binds -- the seniors could staff
every night post in the block and still be under it. A cap that cannot bind is
not a cap.

Keyed by shift-type slug so setup, cleanup and bar are untouched: this is about
party nights specifically, not about working less in general, which is what
``SENIOR_QUOTA_RATIO`` above is for.
"""


SENIOR_BANNED_EVENT_TYPES: frozenset[str] = frozenset({"krush", "dage"})
"""Event types where a senior may not stand a party-night post at all.

Colin's rule, 2026-08-26: "seniors can't be on door, bar, or rides for a Krush
or a Dage." Dan had asked for the Krush half of it in the scratch pad on 23 Aug
and it had been honoured by hand, which is exactly why it is here now — a rule
that lives in somebody's memory survives until the next rebuild and no longer.

These are the big nights: a Krush is a ticketed party at Arena and a Dage runs
all day. They are the two the chapter most wants staffed by people who will
actually stand there, and the two a senior is least likely to.

Enforced as LAST RESORT rather than as a filter, which was a correction made the
same day. A filter is the obvious reading of "can't" and it strands the post the
moment the remaining pool is thinner than the slot count — reachable, and caught
immediately by a test that builds a dage whose entire pool is seniors and got
back an unstaffed bar. An empty bar at a ticketed party at Arena is worse than
the thing this rule prevents.

So a barred senior stays in the pool and sorts behind every other candidate. In
FA26 that is 22 seniors behind 27 underclassmen for seven night slots, so he is
never reached; if he ever is, the run warns loudly instead of the chair finding
out on the night.
"""

SENIOR_BANNED_SHIFT_TYPES: frozenset[str] = frozenset({"door", "bar", "driver"})
"""The posts ``SENIOR_BANNED_EVENT_TYPES`` bars seniors from.

Door, bar and rides — the three jobs that mean standing sober at the party until
it ends. Setup and cleanup are NOT here: those are the jobs the seniors said
they wanted, and the point of the rule is to move them onto those, not off the
event entirely.

``dj`` is NOT here either, and that is deliberate rather than an oversight. DJ is
qualification-gated, the chapter has exactly two people holding it, and one of
them is a senior. Banning him would leave the decks empty on any
night the other DJ is at a tennis tournament — 19 Sep, the first Krush of the
term, is precisely that night. DJing is also not risk work and counts toward
nobody's tally, so a senior behind the decks is not a senior dodging a shift.
"""


def senior_is_barred(event_type_slug: str, shift_type_slug: str) -> bool:
    """True when a senior may not work ``shift_type_slug`` at this event type."""
    return (
        event_type_slug in SENIOR_BANNED_EVENT_TYPES
        and shift_type_slug in SENIOR_BANNED_SHIFT_TYPES
    )


SOPHOMORE_QUOTA_RATIO = 1.25
"""Shifts a sophomore-or-younger works per shift a junior works.

The third tier, added 2026-08-26. Colin: "make sophomores get cooked a little."

Newer brothers carrying more is the chapter's own norm and it was already half
expressed here — the R3.2-A tiebreak chain prefers younger-first, and newer
pledge class first, whenever fairness scores tie. But a TIEBREAK cannot produce
this outcome, and it is worth being precise about why, because the chain looks
like it should. Ties are broken in the sophomore's favour, so he is picked
first; being picked raises his ``shifts_so_far``, which raises his score, which
puts the junior ahead at the next event. The chain decides ORDER at equal load
and then self-equalizes. Over a season it hands sophomores and juniors the same
total.

Cooking somebody takes a different TARGET, not a different order.

1.25 against the FA26 pool (28 seniors, 21 juniors, 11 sophomores, 318.8 effort)
moves a sophomore from 7.89 to 9.24 effort and a junior from 7.89 to 7.39 --
roughly two more shifts for a sophomore across the term and half a shift fewer
for a junior. "A little", as asked, and small enough that no sophomore can point
at a junior and name a night he is working that the junior is not.

SOPHOMORE-OR-YOUNGER, not sophomores exactly: see
``is_sophomore_or_younger_in_term``.
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


def is_sophomore_or_younger_in_term(
    class_year: int | None, *, term_start_year: int, term_is_fall: bool
) -> bool:
    """True when ``class_year`` graduates two or more years after this one.

    ``>=`` rather than ``==`` so a freshman is cooked at least as hard as a
    sophomore rather than falling through to the junior target. A chapter that
    takes freshmen and works them LESS than the sophomores has an ordering
    nobody would defend out loud, and the mistake would be silent -- the roster
    simply has no 2030s in it today, so an ``==`` would look correct all term
    and go wrong the first time a spring pledge class is entered.

    An unknown ``class_year`` is False, landing in the middle (junior) tier. The
    same instinct as ``is_senior_in_term`` reading None as an underclassman:
    when the record is missing, guess the target that is neither the lightest
    nor the heaviest, so the error is small in whichever direction it turns out
    to be wrong.
    """
    if class_year is None:
        return False
    graduating_year = term_start_year + 1 if term_is_fall else term_start_year
    return class_year >= graduating_year + 2


def quota_targets(
    *, rotation_slots: float, senior_count: int, junior_count: int, sophomore_count: int = 0
) -> tuple[float, float, float]:
    """Season shift targets as ``(senior, junior, sophomore)``.

    Solves for the triple that both (a) sits at ``SENIOR_QUOTA_RATIO`` and
    ``SOPHOMORE_QUOTA_RATIO`` against the junior target, and (b) sums across the
    actual pool to exactly the effort that must be staffed::

        senior_count    * (SENIOR_QUOTA_RATIO    * J)
      + junior_count    * J
      + sophomore_count * (SOPHOMORE_QUOTA_RATIO * J) = rotation_slots

    The junior is the unit the other two are quoted against, because he is the
    middle tier and neither ratio then needs to be greater than the other's
    reciprocal to read naturally: 0.30 and 1.25 are both statements about a
    junior's season.

    Derived from live counts rather than hardcoded so that the targets follow
    the roster. A brother going alumni, an event being cancelled, or the strike
    make-up shifts coming off the top all change ``rotation_slots`` or a pool
    size, and a hardcoded "Senior 6 / Jr-Soph 13.5" would quietly stop summing
    to the work that actually exists — which is precisely the failure that makes
    a published target indefensible. That conservation property is asserted
    directly in ``tests/property/test_fairness_monotonicity.py``.

    ``sophomore_count`` defaults to 0 so a two-tier caller still gets the old
    two-tier answer in the first two elements — the arithmetic degenerates
    exactly, it is not an approximation.

    Returns ``(0.0, 0.0, 0.0)`` when there is nobody to work, rather than
    dividing by zero. Callers must treat a zero target as "never pick this
    member" instead of dividing by it.
    """
    denominator = (
        senior_count * SENIOR_QUOTA_RATIO
        + junior_count
        + sophomore_count * SOPHOMORE_QUOTA_RATIO
    )
    if denominator <= 0 or rotation_slots <= 0:
        return (0.0, 0.0, 0.0)
    junior_target = rotation_slots / denominator
    return (
        junior_target * SENIOR_QUOTA_RATIO,
        junior_target,
        junior_target * SOPHOMORE_QUOTA_RATIO,
    )


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
