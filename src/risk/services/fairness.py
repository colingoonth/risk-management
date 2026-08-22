"""Fairness scoring for auto-assign.

Members further behind their season quota go first. Score is the sort key;
lower score = picked first.

    score = (rotation shifts so far + phantoms) / season target for that member

The denominator is what makes this a *ratio* model rather than the phantom model
it replaced, and the difference is not cosmetic. A phantom pushes seniors to the
back of the queue, so they surface only once everyone else has caught up and
their real work stacks into the back half of the term. The back half of this
calendar is where pledging starts and the brothers are dropped, so a deferred
senior's shifts evaporate at the cutoff. A ratio rate-limits instead: every
class advances through its own quota in parallel, each has completed the same
FRACTION of its season at any date, and truncating the calendar anywhere cuts
every class by the same percentage. The pledge date moved twice in one
conversation, which is exactly why that invariance is worth the extra machinery.
See ``policy.SENIOR_QUOTA_RATIO``.

Pinned to ``event.date`` (ADR-009): "shifts so far" counts shifts for events
dated on-or-before the current event in the same semester. Reruns on the same
event therefore see the same scores regardless of wall-clock time.

Tiebreaker chain (R3.2-A), applied in order once fairness ``score`` ties:

  1. ``class_year`` younger-first — newer brothers pick up the shift before
     older ones (continues the seniors-work-less philosophy already baked into
     the score). Unknown class_year is neutral (sorts last).
  2. ``pledge_class`` newer-first — later Greek letter picks up first. Ordered
     by Greek-alphabet ordinal, not lexically. Unmapped/None sorts last.
  3. ``last_assigned_at`` ASC NULLS FIRST (ADR-013) — never-assigned before
     just-assigned. Pinned to ``event.date``, so it carries no wall-clock.
  4. ``member_slug`` ASC — final deterministic stabilizer.

The chain does most of the deciding. Measured over the FA26 fill, the top score
was tied among 2 to 53 pool members in 179 of 224 fills, so roughly four picks
in five are settled below the score. That is not a defect — with 57 members and
13 slots a night, ties are the normal case — but it does mean "why him?" is
usually answered by the chain, not by the number.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from risk.repos import shifts as shifts_repo
from risk.repos import strikes as strikes_repo
from risk.services.eligibility import EligibleMember
from risk.services.policy import (
    DJ_NIGHT_CREDIT,
    is_senior_in_term,
    pledge_class_ordinal,
    quota_targets,
)


@dataclass(frozen=True, slots=True)
class QuotaContext:
    """Season-level facts every score in one run shares.

    Built once per auto-assign call rather than recomputed per member: the
    targets are a property of the semester, and deriving them 14,700 times
    (57 members x 6 shift types x 43 events) would be the same three aggregates
    over and over.

    Holding it as an explicit value passed down the call chain, rather than
    caching it on the connection, keeps ``score_member`` a pure function of its
    arguments — which is what makes a score reproducible from the audit row
    months later without re-deriving the whole semester.
    """

    senior_target: float
    underclass_target: float
    rotation_slots: float
    """Total EFFORT the rotation must absorb, not a slot count."""
    senior_count: int
    underclass_count: int
    term_start_year: int
    term_is_fall: bool
    dj_qualified: frozenset[int]
    dj_phantom: float = 0.0
    """Rotation credit each qualified DJ carries for the term.

    Derived from the DJ slots that exist and how many people can fill them,
    rather than fixed: two DJs against 44 parties is a very different burden
    from six DJs against 12, and a constant cannot know which it is looking at.
    Computed once for the term instead of accumulating per night worked, so a
    DJ's rotation load is predictable in August rather than drifting as the
    season fills in."""

    def target_for(self, class_year: int | None) -> float:
        if is_senior_in_term(
            class_year,
            term_start_year=self.term_start_year,
            term_is_fall=self.term_is_fall,
        ):
            return self.senior_target
        return self.underclass_target


def _term_start_year_and_season(starts_on: str) -> tuple[int, bool]:
    """``(year, is_fall)`` for a semester, from its start date.

    A term starting in July or later is a fall term. July rather than August
    because a chapter that opens its calendar with a summer rush week should
    not have every senior silently re-graded as a junior.
    """
    year, month = int(starts_on[:4]), int(starts_on[5:7])
    return year, month >= 7


def build_quota_context(conn: sqlite3.Connection, *, semester_id: int) -> QuotaContext:
    """Derive this semester's quota targets from the work and the roster.

    ``rotation_slots`` is the work the rotation actually has to absorb: every
    counted slot on a non-cancelled event, minus the strike make-up shifts,
    which are penalties owed on top of a normal season and so are not part of
    anyone's quota.

    The pool is counted the way the season counts it — status-eligible and not
    hard-excluded — and deliberately NOT per-event. Host-house exclusion moves
    with the party and would make the season target wobble from event to event;
    a quota that changes depending on who is hosting is not a quota.

    Requirements are the source for the slot count, never ``shifts``: shift rows
    are created lazily by the fill, so on an unassigned calendar ``shifts`` is
    empty and the targets would come out at zero for everyone.
    """
    sem = conn.execute("SELECT starts_on FROM semesters WHERE id = ?", (semester_id,)).fetchone()
    if sem is None:
        raise LookupError(f"semester {semester_id} not found")
    term_start_year, term_is_fall = _term_start_year_and_season(sem["starts_on"])

    # Effort, not slots — the denominator must match the numerator in
    # score_member, or a term full of cheap shifts would set targets nobody
    # could reach and a term full of expensive ones would set targets everybody
    # blew past in October.
    counted_effort = float(
        conn.execute(
            """
            SELECT COALESCE(SUM(r.target_count * st.effort_weight), 0.0) AS n
            FROM event_shift_requirements r
            JOIN events e ON e.id = r.event_id
            JOIN shift_types st ON st.id = r.shift_type_id
            WHERE e.semester_id = ?
              AND e.status <> 'cancelled'
              AND st.counts_toward_tally = 1
            """,
            (semester_id,),
        ).fetchone()["n"]
    )
    strike_slots = strikes_repo.count_makeup_slots_owed(conn, semester_id=semester_id)

    pool = conn.execute(
        """
        SELECT m.class_year
        FROM members m
        JOIN member_statuses ms ON ms.id = m.status_id
        WHERE ms.excludes_from_assignment = 0
          AND NOT EXISTS (
                SELECT 1 FROM member_roles mr
                JOIN roles r ON r.id = mr.role_id
                WHERE mr.member_id = m.id
                  AND mr.semester_id = ?
                  AND r.default_excluded_from_assignment = 1
                  AND r.exclude_is_soft = 0
              )
        """,
        (semester_id,),
    ).fetchall()
    senior_count = sum(
        1
        for r in pool
        if is_senior_in_term(
            r["class_year"], term_start_year=term_start_year, term_is_fall=term_is_fall
        )
    )
    underclass_count = len(pool) - senior_count

    dj_qualified = frozenset(
        int(r["member_id"])
        for r in conn.execute(
            """
            SELECT mq.member_id
            FROM member_qualifications mq
            JOIN qualifications q ON q.id = mq.qualification_id
            WHERE mq.semester_id = ? AND q.slug = 'dj'
            """,
            (semester_id,),
        )
    )

    # Make-ups come off at full weight. A penalty shift is a penalty whichever
    # post it is worked at, and discounting it would refund part of it.
    # What each DJ is carrying: the DJ slots that exist, split across the people
    # who can fill them, valued at DJ_NIGHT_CREDIT apiece.
    dj_slots = int(
        conn.execute(
            """
            SELECT COALESCE(SUM(r.target_count), 0) AS n
            FROM event_shift_requirements r
            JOIN events e ON e.id = r.event_id
            JOIN shift_types st ON st.id = r.shift_type_id
            WHERE e.semester_id = ? AND e.status <> 'cancelled' AND st.slug = 'dj'
            """,
            (semester_id,),
        ).fetchone()["n"]
    )
    dj_phantom = (dj_slots / len(dj_qualified)) * DJ_NIGHT_CREDIT if dj_qualified else 0.0

    rotation_slots = max(counted_effort - strike_slots, 0.0)
    senior_target, underclass_target = quota_targets(
        rotation_slots=rotation_slots,
        senior_count=senior_count,
        underclass_count=underclass_count,
    )
    return QuotaContext(
        senior_target=senior_target,
        underclass_target=underclass_target,
        rotation_slots=rotation_slots,
        senior_count=senior_count,
        underclass_count=underclass_count,
        term_start_year=term_start_year,
        term_is_fall=term_is_fall,
        dj_qualified=dj_qualified,
        dj_phantom=dj_phantom,
    )


@dataclass(frozen=True, slots=True)
class ScoredMember:
    member: EligibleMember
    shifts_so_far: float
    """Weighted EFFORT so far, not a headcount — see shifts.rotation_effort_*."""
    target: float
    phantom: float
    score: float
    last_assigned_at: str | None


def score_member(
    conn: sqlite3.Connection,
    *,
    member: EligibleMember,
    semester_id: int,
    event_date: str,
    quota: QuotaContext,
) -> ScoredMember:
    """Compute the fairness score for one member relative to one event."""
    shifts_so_far = shifts_repo.rotation_effort_in_semester_through_date(
        conn,
        member_id=member.member_id,
        semester_id=semester_id,
        on_or_before=event_date,
    )
    target = quota.target_for(member.class_year)
    # The DJ credit does not apply until the member has stood at least one
    # rotation shift. Colin's rule, and it earns its place: with the credit
    # applied from the first party, a DJ's phantom (6.45 effort) exceeds a
    # senior's entire quota (5.09), so that DJ came out at zero rotation
    # turns for the whole term — on site 22 nights and reading as somebody who
    # never worked.
    #
    # Withholding it until the first turn means both DJs start the term on the
    # same footing as everyone else, get picked in the opening wave, and only
    # then drop out of contention. One real shift each, early, which is what the
    # chapter needs to see. Keyed on rotation effort specifically: a strike
    # make-up is a penalty, not a turn in the rotation, and should not satisfy
    # a rule about doing your share.
    phantom = (
        quota.dj_phantom
        if member.member_id in quota.dj_qualified and shifts_so_far > 0
        else 0.0
    )
    # A zero target means there is no work, or nobody to do it. Sorting such a
    # member to the very back is the safe direction: the alternative is a
    # ZeroDivisionError mid-fill, and the one after that is treating "no quota"
    # as "infinite appetite" and handing them every slot.
    score = float("inf") if target <= 0 else (shifts_so_far + phantom) / target
    last = shifts_repo.last_assigned_at(conn, member.member_id)
    return ScoredMember(
        member=member,
        shifts_so_far=shifts_so_far,
        target=target,
        phantom=phantom,
        score=score,
        last_assigned_at=last,
    )


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


def _tiebreak_key(scored: ScoredMember) -> tuple:
    """The R3.2-A chain, below whatever primary key the caller put first."""
    return (
        _younger_first_rank(scored.member.class_year),
        _newer_pc_first_rank(scored.member.pledge_class),
        # NULLS FIRST: never-assigned (None) sorts before any timestamp.
        (0, "") if scored.last_assigned_at is None else (1, scored.last_assigned_at),
        scored.member.member_slug,
    )


def sort_by_fairness(
    conn: sqlite3.Connection,
    *,
    pool: list[EligibleMember],
    semester_id: int,
    event_date: str,
    quota: QuotaContext,
    rotation_shift_type_id: int | None = None,
    deprioritized: set[int] | None = None,
) -> list[ScoredMember]:
    """Score the pool and return it sorted, fairest-first.

    Sort order: ``score`` → the R3.2-A tiebreak chain. Every key is derived from
    member attributes or ``event.date``, so results are fully deterministic and
    reproducible across re-runs and chairs (no wall-clock, no seed dependence).

    ``rotation_shift_type_id`` puts a per-type turn-count AHEAD of the score, and
    is passed for gated shift types. Without it, removing DJ from the tally
    leaves the two DJs tied on every key for the whole term and a stable sort
    gives all 43 nights to whichever sorts first — verified, 43 to 0, which is
    worse than the double-counting it replaced. With it, they alternate.

    ``deprioritized`` is the soft-unavailability tier: members who CAN work this
    shift but asked not to. They sort behind everyone else regardless of score,
    so they are picked only once the rest of the pool is exhausted — which is
    exactly "avoid unless the slot would otherwise go unfilled". A tier rather
    than a score penalty because a penalty is a number somebody has to tune, and
    any number large enough to be reliable is indistinguishable from a tier.
    """
    scored = [
        score_member(
            conn,
            member=m,
            semester_id=semester_id,
            event_date=event_date,
            quota=quota,
        )
        for m in pool
    ]
    soft = deprioritized or set()
    if rotation_shift_type_id is None:
        scored.sort(key=lambda s: (s.member.member_id in soft, s.score, *_tiebreak_key(s)))
        return scored

    turns = {
        s.member.member_id: shifts_repo.count_of_shift_type_in_semester_through_date(
            conn,
            member_id=s.member.member_id,
            semester_id=semester_id,
            shift_type_id=rotation_shift_type_id,
            on_or_before=event_date,
        )
        for s in scored
    }
    scored.sort(
        key=lambda s: (
            s.member.member_id in soft,
            turns[s.member.member_id],
            s.score,
            *_tiebreak_key(s),
        )
    )
    return scored
