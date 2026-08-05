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

from risk.repos import auto_assign_runs as runs_repo
from risk.repos import event_shift_requirements as req_repo
from risk.repos import events as events_repo
from risk.repos import qualifications as quals_repo
from risk.repos import shifts as shifts_repo
from risk.services import eligibility, fairness, pledge_mode
from risk.services.eligibility import EligibilityResult
from risk.services.pledge_mode import MODE_FULL, MODE_NORMAL, MODE_PARTIAL, ResolvedMode


@dataclass(frozen=True, slots=True)
class ProposedAssignment:
    shift_type_slug: str
    slot_index: int
    member_id: int | None
    member_slug: str | None
    score: float | None
    reason: str  # 'assigned', 'pool_empty', 'pool_below_min', 'preserved'


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

    elig = eligibility.eligible_for(
        conn,
        event_id=event_id,
        semester_id=event.semester_id,
        host_house_id=event.host_house_id,
        allowed_keys=allowed_keys,
        event_date=event.date,
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

    fill_order = _fill_order_key(
        frozenset(quals_repo.shift_type_ids_with_requirements(conn))
    )
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
        # One shift per member per event — spreads load even when the partial
        # unique index would technically allow door+setup for the same person.
        # Chair can still manually multi-assign via `risk shift assign`.
        type_pool = [m for m in type_pool if m.member_id not in already_assigned]
        scored = fairness.sort_by_fairness(
            conn,
            pool=type_pool,
            semester_id=event.semester_id,
            event_date=event.date,
        )
        if pledges_first:
            # Stable secondary sort: pledges before brothers, preserving the
            # fairness order within each group.
            scored = sorted(scored, key=lambda s: (not s.member.is_pledge,))

        filled = 0
        for slot_index in range(req.target_count):
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
