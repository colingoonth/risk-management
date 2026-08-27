"""Auto-assign orchestrator.

Wires eligibility → fairness → pledge_mode resolution → greedy fill, snapshots
the resolved mode onto each ``shifts`` row, and writes an ``auto_assign_runs``
audit row carrying the seed + pool counts (ADR-005).

Caller wraps the public entry point in ``with transaction(conn):`` —
``auto_assign`` itself does NOT open a transaction so that ``--dry-run``
callers can roll back trivially. The CLI passes ``commit=False`` to inhibit
writes; dry-runs still query pools and return what would be assigned.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date as _date

from risk.repos import auto_assign_runs as runs_repo
from risk.repos import event_shift_requirements as req_repo
from risk.repos import events as events_repo
from risk.repos import member_shift_preferences as member_prefs_repo
from risk.repos import qualifications as quals_repo
from risk.repos import shifts as shifts_repo
from risk.repos import strikes as strikes_repo
from risk.services import availability, eligibility, fairness, pledge_mode, policy
from risk.services.eligibility import EligibilityResult
from risk.services.pledge_mode import MODE_FULL, MODE_NORMAL, MODE_PARTIAL, ResolvedMode


@dataclass(frozen=True, slots=True)
class ProposedAssignment:
    shift_type_slug: str
    slot_index: int
    member_id: int | None
    member_slug: str | None
    score: float | None
    reason: str  # 'assigned', 'strike_makeup', 'pool_empty', 'pool_below_min', 'preserved'


@dataclass(frozen=True, slots=True)
class AutoAssignResult:
    event_id: int
    resolved_mode: ResolvedMode
    seed: int
    assignments: list[ProposedAssignment]
    warnings: list[str]
    eligibility: EligibilityResult


def _pool_for_mode(
    pool: list[eligibility.EligibleMember], mode_slug: str
) -> list[eligibility.EligibleMember]:
    """Filter the eligibility pool by pledge-mode preference.

    - normal:    brothers only (is_pledge = False)
    - full:      pledges only  (is_pledge = True)
    - partial:   everyone — assignment logic interleaves pledges first
    """
    if mode_slug == MODE_NORMAL:
        return [m for m in pool if not m.is_pledge]
    if mode_slug == MODE_FULL:
        return [m for m in pool if m.is_pledge]
    return list(pool)


def _fill_order_key(
    gated_shift_type_ids: frozenset[int],
) -> Callable[[req_repo.EventShiftRequirement], tuple[int, str]]:
    """Sort key for the per-shift-type fill loop: gated types first.

    The loop is greedy and consumes the shared pool as it goes, so whichever
    shift type runs first gets the pick of the chapter. Filling in slug order
    put ``cleanup`` (4 slots) ahead of ``dj`` (1 slot, 2 qualified people in the
    whole chapter) — cleanup would take a DJ as one of its four bodies and the
    DJ slot then came up empty, with a qualified member sitting in the cleanup
    crew. Most-constrained-first is the fix.

    Slug remains the secondary key, so the order among ungated types is exactly
    what it was before the gate existed.

    Note this reads the CONSTRAINT rather than measuring the pool: pool size
    changes run to run, and a fill order that moves with it would make the
    schedule harder for the chair to predict.
    """

    def key(req: req_repo.EventShiftRequirement) -> tuple[int, str]:
        return (
            0 if req.shift_type_id in gated_shift_type_ids else 1,
            req.shift_type_slug,
        )

    return key


def elig_window_label(conn: sqlite3.Connection, shift_type_id: int, event_date: str) -> str:
    """Human phrasing of the window a shift type is worked in.

    Used only in warnings, but worth the call: "4 member(s) unavailable for
    cleanup" sends the chair looking at the party date, which is not when
    cleanup happens and is exactly the confusion this whole change exists to
    remove.
    """
    from risk.repos import shift_type_windows as windows_repo

    window = windows_repo.get_for_shift_type(conn, shift_type_id)
    if window is None:
        return "no window"
    spans = availability.window_spans(window, event_date)
    if not spans:
        return "empty window"
    return f"{spans[0][0]:%a %d %b %H:%M} to {spans[-1][1]:%a %d %b %H:%M}"


def _place_strike_makeups(
    conn: sqlite3.Connection,
    *,
    event: events_repo.Event,
    requirements: list[req_repo.EventShiftRequirement],
    fill_order: Callable[[req_repo.EventShiftRequirement], tuple[int, str]],
    gated_shift_type_ids: frozenset[int],
    by_type_slot: dict[tuple[int, int], shifts_repo.Shift],
    already_assigned: set[int],
    allowed_keys: frozenset[str],
    resolved: ResolvedMode,
    commit: bool,
) -> tuple[list[ProposedAssignment], list[str]]:
    """Seat the members who owe a make-up shift, before the rotation runs.

    One unserved strike, one shift. The chapter's rule, and Colin's instruction
    was to put them on the first parties of the term rather than spreading them
    — a penalty nobody can point at is not much of a penalty.

    That clustering is emergent rather than computed. This runs on every event,
    but it seats everyone who fits, so the earliest events absorb the whole
    backlog and later events find nothing left to place. It also means a strike
    issued in November is picked up by the next fill without anything special.

    THREE RULES WORTH STATING, because each one is a decision:

    Placeholders are skipped. Those are dates the social chair is holding, and
    roughly half of them will not happen. Working off a strike at a party that
    gets cancelled leaves the strike unserved and the member believing he has
    paid it — the worst of both.

    Gated shift types are skipped. A make-up should be a real risk shift. DJing
    is not risk work, does not count toward anyone's tally, and letting it
    settle a strike would make the penalty free for exactly the two people who
    already have a job that exempts them from the rotation.

    Hard-excluded roles are ignored, via ``honor_hard_role_exclusion=False``.
    The exemption spares an officer the rotation; it does not forgive a strike
    he earned.
    """
    if event.planning_status in events_repo.FILL_LAST_PLANNING_STATUSES:
        return [], []

    debts = strikes_repo.list_unserved(conn, semester_id=event.semester_id)
    if not debts:
        return [], []

    # Same event-level filters as the rotation, minus H3. A strike does not make
    # a member available on a night he is away, nor let him monitor his own
    # house.
    seatable = {
        m.member_id
        for m in eligibility.eligible_for(
            conn,
            event_id=event.id,
            semester_id=event.semester_id,
            host_house_id=event.host_house_id,
            allowed_keys=allowed_keys,
            honor_hard_role_exclusion=False,
        ).eligible
    }

    queue = [d for d in debts if d.member_id in seatable]
    placed: list[ProposedAssignment] = []
    warnings: list[str] = []

    for req in sorted(requirements, key=fill_order):
        if req.shift_type_id in gated_shift_type_ids:
            continue
        for slot_index in range(req.target_count):
            if not queue:
                break
            existing = by_type_slot.get((req.shift_type_id, slot_index))
            if existing is not None and existing.assigned_member_id is not None:
                continue  # the chair put someone here; leave them
            # Skip anyone already standing somewhere at this party. Two debts
            # cannot be worked off on one night.
            # Availability is checked against THIS shift type's window, same as
            # the rotation below. A debt does not make somebody free: a member
            # away at a tournament cannot work it off there, and seating him
            # would leave the post empty AND the strike unpaid.
            idx = next(
                (
                    i
                    for i, d in enumerate(queue)
                    if d.member_id not in already_assigned
                    and availability.can_cover(
                        conn,
                        member_id=d.member_id,
                        semester_id=event.semester_id,
                        shift_type_id=req.shift_type_id,
                        event_date=event.date,
                        honor_soft=False,
                    )
                ),
                None,
            )
            if idx is None:
                break
            debt = queue.pop(idx)
            placed.append(
                ProposedAssignment(
                    shift_type_slug=req.shift_type_slug,
                    slot_index=slot_index,
                    member_id=debt.member_id,
                    member_slug=debt.member_slug,
                    score=None,
                    reason="strike_makeup",
                )
            )
            already_assigned.add(debt.member_id)
            warnings.append(
                f"{debt.display_name} works {req.shift_type_slug} as a strike "
                f"make-up (strike #{debt.strike_id}, issued {debt.issued_on}) — "
                f"does not count toward their season total"
            )
            if commit:
                shift_id = (
                    existing.id
                    if existing is not None
                    else shifts_repo.insert_open(
                        conn,
                        event_id=event.id,
                        shift_type_id=req.shift_type_id,
                        slot_index=slot_index,
                    )
                )
                shifts_repo.assign(
                    conn,
                    shift_id=shift_id,
                    member_id=debt.member_id,
                    effective_pledge_mode_id=resolved.resolved_id,
                    assigned_at=event.date,
                    serves_strike_id=debt.strike_id,
                )
        if not queue:
            break
    return placed, warnings


def auto_assign(
    conn: sqlite3.Connection,
    *,
    event_id: int,
    allowed_keys: frozenset[str] = frozenset(),
    seed: int | None = None,
    reassign: bool = False,
    strict: bool = False,
    commit: bool = True,
) -> AutoAssignResult:
    """Run auto-assign for ``event_id``.

    Returns the resolved mode, the seed (recorded for reproducibility), and
    the per-slot assignments. With ``commit=False`` (dry-run), no writes are
    made — the caller still gets the would-be assignments.
    """
    event = events_repo.get_by_id(conn, event_id)
    if event is None:
        raise LookupError(f"event {event_id} not found")

    if seed is None:
        seed = secrets.randbits(31)

    # NOTE the absent event_date. Unavailability is deliberately NOT tested here
    # any more; it is tested per shift type below, against that type's own
    # window. Asking it here can only compare against the party's date, and two
    # of the six jobs are not worked on the party's date — setup runs up to two
    # days before, cleanup is the morning after. That produced errors in both
    # directions on real data: a runner blacked out for a 9am Saturday meet was
    # still assigned the Friday party's cleanup crew, which is worked Saturday
    # morning, while a member busy only on the party night was struck off setup
    # crews he could have worked on the Wednesday.
    elig = eligibility.eligible_for(
        conn,
        event_id=event_id,
        semester_id=event.semester_id,
        host_house_id=event.host_house_id,
        allowed_keys=allowed_keys,
    )
    pool = elig.eligible
    eligible_pledges = sum(1 for m in pool if m.is_pledge)
    eligible_brothers = sum(1 for m in pool if not m.is_pledge)

    requirements = req_repo.list_for_event(conn, event_id)
    total_required = sum(r.target_count for r in requirements)

    resolved = pledge_mode.resolve(
        conn,
        host_house_id=event.host_house_id,
        semester_id=event.semester_id,
        eligible_pledges=eligible_pledges,
        eligible_brothers=eligible_brothers,
        total_required=total_required,
    )

    if commit and reassign:
        # Clear all open shifts so we redo the fill from scratch. Assigned
        # shifts are preserved by default (chair edits stay) unless the
        # caller also unassigns them via the shift CLI first.
        shifts_repo.delete_open_for_event(conn, event_id)

    existing_shifts = shifts_repo.list_for_event(conn, event_id)
    by_type_slot: dict[tuple[int, int], shifts_repo.Shift] = {
        (s.shift_type_id, s.slot_index): s for s in existing_shifts
    }
    eligible_ids = {m.member_id for m in pool}
    # Drop any preserved assignment whose member is no longer eligible (e.g.,
    # event host changed and they now belong to the host house, status went
    # alumni, role added, etc.). Without this, `--reassign` would still keep
    # them since they're 'assigned', not 'open'.
    stale_preserved: list[shifts_repo.Shift] = [
        s
        for s in existing_shifts
        if s.assigned_member_id is not None and s.assigned_member_id not in eligible_ids
    ]
    already_assigned: set[int] = {
        s.assigned_member_id
        for s in existing_shifts
        if s.assigned_member_id is not None and s.assigned_member_id in eligible_ids
    }

    # Per-shift-type facts the one-per-event rule below needs. COALESCE to 1 for
    # a type with no window row: "assume it occupies the night" is the cautious
    # default, because the cost of guessing wrong that way is a shift not
    # offered, and the cost of guessing wrong the other way is one man standing
    # two posts at once.
    type_flags: dict[int, tuple[bool, bool]] = {
        int(r["id"]): (bool(r["counts_toward_tally"]), bool(r["occupies_event_night"]))
        for r in conn.execute(
            """
            SELECT st.id, st.counts_toward_tally,
                   COALESCE(w.occupies_event_night, 1) AS occupies_event_night
            FROM shift_types st
            LEFT JOIN shift_type_windows w ON w.shift_type_id = st.id
            """
        )
    }

    assignments: list[ProposedAssignment] = []
    warnings: list[str] = []
    if event.resync_pending:
        warnings.append(
            "event.resync_pending=1 — shift requirements may be stale "
            "(run `risk event resync-shift-reqs` before assigning)"
        )
    for stale in stale_preserved:
        warnings.append(
            f"unassigning {stale.assigned_member_slug} from "
            f"{stale.shift_type_slug} slot {stale.slot_index} — no longer eligible"
        )
        if commit:
            shifts_repo.unassign(conn, shift_id=stale.id)
        # Clear the assignment IN THE MAP rather than dropping the entry. The
        # slot is free but the ROW still exists — `unassign` nulls the member and
        # sets status 'open', it does not delete. Dropping the entry made the
        # fill loop below believe there was no row and call `insert_open`, which
        # collides with UNIQUE(event_id, shift_type_id, slot_index): the whole
        # run raised IntegrityError, rolled back with the stale assignment still
        # in place, and then failed identically on every subsequent run. Keeping
        # the entry means the loop finds the row and reuses its id.
        #
        # Done in memory, not re-read, so a dry run models exactly what the real
        # run would do — `unassign` above is skipped when commit=False.
        by_type_slot[(stale.shift_type_id, stale.slot_index)] = replace(
            stale,
            assigned_member_id=None,
            assigned_member_slug=None,
            effective_pledge_mode_id=None,
            effective_pledge_mode_slug=None,
            status="open",
            assigned_at=None,
        )
    pledges_first = resolved.resolved_slug == MODE_PARTIAL

    # Season targets, derived once. Every score in this run divides by them, and
    # they are a property of the semester rather than of any one slot.
    quota = fairness.build_quota_context(conn, semester_id=event.semester_id)

    gated_shift_type_ids = frozenset(quals_repo.shift_type_ids_with_requirements(conn))
    fill_order = _fill_order_key(gated_shift_type_ids)

    # Strike make-ups go in BEFORE the rotation, and the order is load-bearing
    # in two directions. Forward: whoever owes a shift is placed first, so the
    # make-ups land on the earliest parties instead of wherever fairness happens
    # to put them. Backward: a member placed here enters `already_assigned` and
    # is therefore invisible to every pool below, which is what makes the DJ
    # case work without a special case — the DJ who owes a strike takes a risk
    # slot, and the dj gate below then finds only the other DJ.
    makeups, makeup_warnings = _place_strike_makeups(
        conn,
        event=event,
        requirements=requirements,
        fill_order=fill_order,
        gated_shift_type_ids=gated_shift_type_ids,
        by_type_slot=by_type_slot,
        already_assigned=already_assigned,
        allowed_keys=allowed_keys,
        resolved=resolved,
        commit=commit,
    )
    assignments.extend(makeups)
    warnings.extend(makeup_warnings)
    claimed_by_makeup = {(a.shift_type_slug, a.slot_index) for a in makeups}

    # ONE COUNTED SHIFT PER MEMBER PER EVENT, PLUS AN UNCOUNTED ONE ON TOP.
    #
    # This replaced a flat "one shift per member per event". That rule was there
    # to spread load, and for two counted shifts it still is — door+setup at the
    # same party is two turns for one man and stays blocked. But it also stopped
    # a DJ taking setup or cleanup at the party he was DJing, and that was wrong
    # twice over. DJ is 20:00-23:59; setup is a 2h block across the three days up
    # to the party and cleanup is the next morning, so there is no hour where he
    # is in two places. And a DJ night counts toward nobody's tally, so pairing
    # it with a real shift still leaves him exactly one counted turn, like
    # everyone else — he was being charged a full event for a shift the ledger
    # does not even record.
    #
    # Colin's rule, 2026-08-26: "dj is only not available to work with door and
    # rides. they can still setup and cleanup." It also unblocks standing note
    # 15 (both DJs must work a real rotation shift early), which the old rule
    # actively fought: a man who DJs most parties had almost no event left at
    # which he could take a rotation turn.
    #
    # Two independent counters, not one:
    #   counted  — at most one shift that counts toward the tally
    #   night    — at most one shift occupying 20:00-23:59 on the party night
    # dj is (uncounted, night); setup and cleanup are (counted, not night);
    # door, rides and bar are (counted, night).
    counted_at_event: set[int] = set()
    night_at_event: set[int] = set()
    slug_to_type_id = {r.shift_type_slug: r.shift_type_id for r in requirements}

    def _remember(member_id: int, shift_type_id: int) -> None:
        counted, night = type_flags.get(shift_type_id, (True, True))
        if counted:
            counted_at_event.add(member_id)
        if night:
            night_at_event.add(member_id)

    for s_ in existing_shifts:
        if s_.assigned_member_id is not None and s_.assigned_member_id in eligible_ids:
            _remember(s_.assigned_member_id, s_.shift_type_id)
    for a in makeups:
        if a.member_id is not None and a.shift_type_slug in slug_to_type_id:
            _remember(a.member_id, slug_to_type_id[a.shift_type_slug])

    for req in sorted(requirements, key=fill_order):
        type_pool = _pool_for_mode(pool, resolved.resolved_slug)
        # Qualification gate. Only `dj` is gated, and the chapter has two DJs, so
        # this pool is tiny — which is exactly why _fill_order runs it first.
        narrowed = eligibility.filter_for_shift_type(
            conn,
            pool=type_pool,
            shift_type_id=req.shift_type_id,
            semester_id=event.semester_id,
        )
        if narrowed.required_qualification_slugs and not narrowed.eligible:
            warnings.append(
                f"{req.shift_type_slug}: nobody in the pool holds "
                f"{'+'.join(narrowed.required_qualification_slugs)} — "
                f"{narrowed.excluded_by_qualification} member(s) filtered out"
            )
        type_pool = narrowed.eligible
        # See the two counters above. Chair can still override via
        # `risk shift assign`.
        req_counted, req_night = type_flags.get(req.shift_type_id, (True, True))
        type_pool = [
            m
            for m in type_pool
            if not (req_counted and m.member_id in counted_at_event)
            and not (req_night and m.member_id in night_at_event)
        ]

        # Availability, against THIS shift type's window rather than the party
        # date. Two questions, asked separately and answered differently:
        # a hard conflict removes the member; a preference leaves them in the
        # pool and sorts them last, so they are picked only when the alternative
        # is an unstaffed post.
        blocked = 0
        deprioritized: set[int] = set()
        available: list[eligibility.EligibleMember] = []
        for member in type_pool:
            if not availability.can_cover(
                conn,
                member_id=member.member_id,
                semester_id=event.semester_id,
                shift_type_id=req.shift_type_id,
                event_date=event.date,
                honor_soft=False,
            ):
                blocked += 1
                continue
            available.append(member)
            if not availability.can_cover(
                conn,
                member_id=member.member_id,
                semester_id=event.semester_id,
                shift_type_id=req.shift_type_id,
                event_date=event.date,
                honor_soft=True,
            ):
                deprioritized.add(member.member_id)
        if blocked:
            warnings.append(
                f"{req.shift_type_slug}: {blocked} member(s) unavailable for this "
                f"shift's window ({elig_window_label(conn, req.shift_type_id, event.date)})"
            )
        type_pool = available

        # Per-member steers: "not rides at all", "door on a Tuesday not a
        # Friday", "no night post at a Krush". Soft ones join the same
        # deprioritized tier as everything else here, so the man is taken only
        # when the post would otherwise stand empty — the honest reading of "try
        # to avoid". Hard ones are removed outright.
        #
        # This is what replaced the blanket sports steer the chair reversed on
        # 2026-08-26. That version pinned eleven men to setup for the whole term
        # via season-long unavailability rows and made them unhappy enough to be
        # withdrawn; these are per person, per shift type, and individually
        # removable. See migration 0023.
        pref_soft, pref_hard = member_prefs_repo.matching(
            conn,
            semester_id=event.semester_id,
            shift_type_id=req.shift_type_id,
            weekday=_date.fromisoformat(event.date).weekday(),
            event_type_id=event.event_type_id,
        )
        if pref_hard:
            before = len(type_pool)
            type_pool = [m for m in type_pool if m.member_id not in pref_hard]
            if before != len(type_pool):
                warnings.append(
                    f"{req.shift_type_slug}: {before - len(type_pool)} member(s) "
                    f"hard-steered off this shift"
                )
        deprioritized |= pref_soft & {m.member_id for m in type_pool}

        # Krush and Dage: seniors do not stand door, bar or rides.
        #
        # Enforced as LAST RESORT rather than as a filter, and that was a
        # correction. The filter version is the obvious reading of "can't", and
        # it strands the post the moment the remaining pool is thinner than the
        # slot count — reachable, and caught by
        # test_over_21_does_not_gate_the_bar, which builds a dage whose whole
        # pool is seniors and got back an unstaffed bar.
        #
        # An empty bar at a ticketed party at Arena is worse than the thing this
        # rule prevents; it is the failure risk management exists to stop. So a
        # barred senior stays in the pool and sorts behind every other
        # candidate, exactly like soft unavailability: with 27 non-seniors
        # against seven night slots he is never reached in practice, and in the
        # pathological case the post is staffed and the chair is told rather
        # than finding out on the night. See policy.SENIOR_BANNED_EVENT_TYPES.
        barred: set[int] = set()
        if policy.senior_is_barred(event.event_type_slug, req.shift_type_slug):
            barred = {
                m.member_id
                for m in type_pool
                if policy.is_senior_in_term(
                    m.class_year,
                    term_start_year=quota.term_start_year,
                    term_is_fall=quota.term_is_fall,
                )
            }
            deprioritized |= barred

        # Senior night cap. A senior already at his season allowance of THIS
        # party-night post joins the same deprioritized tier a soft
        # unavailability row puts somebody in: still in the pool, sorted last,
        # taken only if the post would otherwise stand empty. Soft rather than a
        # filter for the reason in policy.SENIOR_NIGHT_TYPE_CAPS — an unstaffed
        # door is the one outcome a risk schedule cannot produce.
        #
        # Counted through the event's own date, like every other count in this
        # fill (ADR-009), so re-running one event does not see shifts at parties
        # that come after it and the result stays reproducible.
        cap = policy.SENIOR_NIGHT_TYPE_CAPS.get(req.shift_type_slug)
        if cap is not None:
            at_cap = 0
            for member in type_pool:
                if not policy.is_senior_in_term(
                    member.class_year,
                    term_start_year=quota.term_start_year,
                    term_is_fall=quota.term_is_fall,
                ):
                    continue
                worked = shifts_repo.count_of_shift_type_in_semester_through_date(
                    conn,
                    member_id=member.member_id,
                    semester_id=event.semester_id,
                    shift_type_id=req.shift_type_id,
                    on_or_before=event.date,
                )
                if worked >= cap:
                    deprioritized.add(member.member_id)
                    at_cap += 1
            if at_cap:
                warnings.append(
                    f"{req.shift_type_slug}: {at_cap} senior(s) already at the season "
                    f"cap of {cap} — sorted last, picked only if the post would "
                    f"otherwise be empty"
                )
        scored = fairness.sort_by_fairness(
            conn,
            pool=type_pool,
            semester_id=event.semester_id,
            event_date=event.date,
            quota=quota,
            # Gated types rank by their own turn count first. The gated pool is
            # tiny — two DJs for 43 parties — and once DJ nights stopped
            # counting toward the tally, nothing else in the chain ever
            # separated them: one would have taken all 43 and the other none.
            rotation_shift_type_id=(
                req.shift_type_id if req.shift_type_id in gated_shift_type_ids else None
            ),
            # Preferences are ignored for GATED types, and that is not a
            # loophole — it is the only way they can work. A gated pool is tiny
            # by construction: two members hold the dj qualification and there
            # are 44 parties. Sorting one of them behind the other for a
            # preference does not spread the work, it hands the whole term to
            # whoever is left. Measured, before this line existed: 42 nights to
            # one DJ and 1 to the other, which is the same 43-to-0 collapse the
            # rotation key was added to prevent, arriving through a different
            # door. With no alternative pool there is no "unless the slot goes
            # unfilled" to fall back on, so the preference has nothing to yield
            # to and the turn-count has to win outright.
            deprioritized=(set() if req.shift_type_id in gated_shift_type_ids else deprioritized),
        )
        if pledges_first:
            # Stable secondary sort: pledges before brothers, preserving the
            # fairness order within each group.
            scored = sorted(scored, key=lambda s: (not s.member.is_pledge,))

        seated_barred: list[str] = []
        filled = 0
        for slot_index in range(req.target_count):
            if (req.shift_type_slug, slot_index) in claimed_by_makeup:
                # A make-up already stands here. It is reported above with its
                # own reason, so re-reporting it as 'preserved' would put the
                # slot in the payload twice and hide that it was a penalty.
                # It still counts as filled — a body is a body for min_count.
                filled += 1
                continue
            existing = by_type_slot.get((req.shift_type_id, slot_index))
            if existing is not None and existing.assigned_member_id is not None:
                # Preserve chair-assigned slot.
                assignments.append(
                    ProposedAssignment(
                        shift_type_slug=req.shift_type_slug,
                        slot_index=slot_index,
                        member_id=existing.assigned_member_id,
                        member_slug=existing.assigned_member_slug,
                        score=None,
                        reason="preserved",
                    )
                )
                filled += 1
                continue

            if not scored:
                assignments.append(
                    ProposedAssignment(
                        shift_type_slug=req.shift_type_slug,
                        slot_index=slot_index,
                        member_id=None,
                        member_slug=None,
                        score=None,
                        reason="pool_empty",
                    )
                )
                continue

            chosen = scored.pop(0)
            if chosen.member.member_id in barred:
                # The pool ran out of everybody else. Say so loudly: this is a
                # rule being broken to keep a post staffed, and the chair needs
                # to know on the day rather than at the party.
                seated_barred.append(chosen.member.display_name)
            assignments.append(
                ProposedAssignment(
                    shift_type_slug=req.shift_type_slug,
                    slot_index=slot_index,
                    member_id=chosen.member.member_id,
                    member_slug=chosen.member.member_slug,
                    score=chosen.score,
                    reason="assigned",
                )
            )
            already_assigned.add(chosen.member.member_id)
            _remember(chosen.member.member_id, req.shift_type_id)
            filled += 1

            if commit:
                shift_id = (
                    existing.id
                    if existing is not None
                    else shifts_repo.insert_open(
                        conn,
                        event_id=event_id,
                        shift_type_id=req.shift_type_id,
                        slot_index=slot_index,
                    )
                )
                shifts_repo.assign(
                    conn,
                    shift_id=shift_id,
                    member_id=chosen.member.member_id,
                    effective_pledge_mode_id=resolved.resolved_id,
                    assigned_at=event.date,
                )

        if seated_barred:
            warnings.append(
                f"{req.shift_type_slug}: RULE BROKEN TO KEEP THE POST STAFFED — "
                f"{', '.join(seated_barred)} seated at a {event.event_type_slug} "
                f"despite seniors not working {req.shift_type_slug} there. The "
                f"pool held nobody else. Replace by hand if you can."
            )

        if filled < req.min_count:
            warning = (
                f"{req.shift_type_slug}: filled {filled}/{req.target_count} "
                f"(min {req.min_count}) — pool short"
            )
            warnings.append(warning)
            if strict:
                raise RuntimeError(warning)

    if commit:
        runs_repo.insert(
            conn,
            event_id=event_id,
            resolved_mode_id=resolved.resolved_id,
            eligible_pledges_at_run=eligible_pledges,
            eligible_brothers_at_run=eligible_brothers,
            seed=seed,
            payload_json=json.dumps(
                {
                    "configured_mode": resolved.configured_slug,
                    "resolved_mode": resolved.resolved_slug,
                    "total_required": total_required,
                    "allowed_keys": sorted(allowed_keys),
                    "assignments": [
                        {
                            "shift_type": a.shift_type_slug,
                            "slot": a.slot_index,
                            "member_id": a.member_id,
                            "reason": a.reason,
                        }
                        for a in assignments
                    ],
                    "warnings": warnings,
                }
            ),
        )

    return AutoAssignResult(
        event_id=event_id,
        resolved_mode=resolved,
        seed=seed,
        assignments=assignments,
        warnings=warnings,
        eligibility=elig,
    )


def semester_fill_order(events: list[events_repo.Event]) -> list[events_repo.Event]:
    """Order a term's events for the bulk fill: confirmed calendar first.

    Two passes, not one. Every confirmed and potential party is filled in date
    order, and only then are the held placeholder dates filled, also in date
    order.

    THE PROPERTY THIS BUYS. During the first pass no placeholder shift exists
    yet, so no placeholder can influence anyone's fairness score. The confirmed
    calendar therefore comes out byte-identical to the calendar you would get if
    the placeholders had never been entered at all — which means cancelling one,
    as roughly half of them will be, disturbs nothing. A single date-ordered
    pass does the opposite: an August placeholder consumes early slots, shifts
    every later score, and if it never happens the whole term was balanced
    around a party that did not exist.

    Within the confirmed pass, date order still matters for its own reason —
    fairness counts shifts at events dated on or before the current one, so
    filling out of order would have later events scoring against a partially
    built past.

    ``potential`` sorts WITH the confirmed calendar. There is one such event
    (Sep 5) and both source workbooks describe a real party that is merely
    unconfirmed, which is a different thing from a date being held open.
    ``start_time`` and ``id`` are the final tiebreaks because every FA26 row has
    a NULL start_time and several share a date, so without them SQLite is free
    to return same-date events in a different order between runs and the
    schedule would not reproduce.
    """
    return sorted(
        events,
        key=lambda e: (
            e.planning_status in events_repo.FILL_LAST_PLANNING_STATUSES,
            e.date,
            e.start_time or "",
            e.id,
        ),
    )


def auto_assign_semester(
    conn: sqlite3.Connection,
    *,
    semester_id: int,
    allowed_keys: frozenset[str] = frozenset(),
    seed: int | None = None,
    reassign: bool = False,
    commit: bool = True,
    on_or_after: str | None = None,
    on_or_before: str | None = None,
) -> list[tuple[events_repo.Event, AutoAssignResult]]:
    """Fill a whole term, in the order :func:`semester_fill_order` defines.

    This loop previously existed only inside ``POST /api/events/auto-assign-bulk``,
    which is why there has never been a CLI equivalent and why the placeholder
    rule had nowhere to live. Both surfaces now call this.

    Cancelled events are skipped: their requirement rows may still exist, and
    staffing a party that is not happening would burn real slots.

    ``on_or_after`` limits the fill to events on or after a date, for re-running
    the back half of a term after something changes — a conflict arriving, or
    the pledge cutoff landing — without disturbing parties that have already
    been worked. It is applied AFTER ordering, so the two-pass shape holds
    within whatever range is chosen.

    ``on_or_before`` closes the range at the other end. Added 2026-08-26, when
    the chapter moved to publishing a FORTNIGHT at a time instead of a whole
    term: a chair now fills 30 Aug to 13 Sep, sends it, and comes back for the
    next block. Bounded at both ends rather than open-ended so that the fill
    cannot quietly reach past what is being published and seat somebody at a
    party the social chair has not confirmed yet.

    The quota targets stay SEASON-wide either way, deliberately. They are
    computed from the whole calendar in ``fairness.build_quota_context``, so a
    member's score is his progress through his year, not through the fortnight,
    and the blocks stay comparable to each other instead of each one restarting
    everybody at zero.

    No transaction is opened here, matching ``auto_assign``: the caller owns the
    boundary, so a bulk fill either lands whole or not at all.
    """
    events = [
        e for e in events_repo.list_for_semester(conn, semester_id) if e.status != "cancelled"
    ]
    if on_or_after is not None:
        events = [e for e in events if e.date >= on_or_after]
    if on_or_before is not None:
        events = [e for e in events if e.date <= on_or_before]

    out: list[tuple[events_repo.Event, AutoAssignResult]] = []
    for event in semester_fill_order(events):
        out.append(
            (
                event,
                auto_assign(
                    conn,
                    event_id=event.id,
                    allowed_keys=allowed_keys,
                    seed=seed,
                    reassign=reassign,
                    commit=commit,
                ),
            )
        )
    return out
