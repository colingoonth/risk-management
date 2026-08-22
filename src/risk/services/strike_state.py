"""Strike-state derivation: threshold-consequence emission + removal application.

Per ADR-006, strike numbers live only in `v_strike_numbers`. This module owns
the WRITE BOUNDARY (council session-01): every state transition that affects
strike-count goes through here so threshold-emission stays idempotent.

  issue_strike()   — append a strike + emit any newly-crossed thresholds.
  apply_removal()  — record a removal, close linked strikes, re-derive.

Threshold-emission is monotone: once a member has ever had N open strikes in a
semester, the kind for N stays in `pending_consequences` even if strikes are
later removed. Resolution (`served | waived | carried_forward`) is the chair's
explicit action, not a side effect of removal.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from risk.repos import pending_consequences as pc_repo
from risk.repos import strike_removals as removal_repo
from risk.repos import strikes as strike_repo
from risk.services import policy


@dataclass(frozen=True, slots=True)
class IssueResult:
    strike_id: int
    strike_number: int
    new_consequences: tuple[str, ...]
    """Kinds newly emitted into pending_consequences this call (typically 0 or 1)."""


@dataclass(frozen=True, slots=True)
class RemovalResult:
    removal_id: int
    closed_strike_ids: tuple[int, ...]
    active_count_after: int


def issue_strike(
    conn: sqlite3.Connection,
    *,
    member_id: int,
    semester_id: int,
    issued_on: str,
    reason: str,
    shift_id: int | None = None,
) -> IssueResult:
    """Append a strike and emit threshold-consequences crossed by this insert.

    Idempotent over `pending_consequences`: the UNIQUE(member, semester, kind)
    constraint means re-running with the same inputs would never duplicate a
    kind row. The triggering_strike_id is the strike whose insertion took the
    open count to the threshold.
    """
    if shift_id is not None:
        existing = conn.execute(
            "SELECT id FROM strikes WHERE member_id=? AND shift_id=? AND closed_at IS NULL",
            (member_id, shift_id),
        ).fetchone()
        if existing:
            raise ValueError(
                f"An active strike already exists for this member on shift {shift_id} "
                f"(strike #{existing[0]})."
            )
    else:
        # Shift-less (manual) strikes have no shift to dedup on; guard against a
        # double-submit creating a phantom duplicate (TODO T1-NEW) by rejecting an
        # identical open (member, semester, issued_on, reason) strike.
        dup = conn.execute(
            """
            SELECT id FROM strikes
            WHERE member_id=? AND semester_id=? AND shift_id IS NULL
              AND issued_on=? AND reason=? AND closed_at IS NULL
            """,
            (member_id, semester_id, issued_on, reason),
        ).fetchone()
        if dup:
            raise ValueError(
                f"An identical active strike was already issued for this member on "
                f"{issued_on} (strike #{dup[0]})."
            )

    strike_id = strike_repo.insert(
        conn,
        member_id=member_id,
        semester_id=semester_id,
        shift_id=shift_id,
        issued_on=issued_on,
        reason=reason,
    )
    new_kinds = _sync_consequences(conn, member_id=member_id, semester_id=semester_id)
    strike_number = strike_repo.count_active(conn, member_id=member_id, semester_id=semester_id)
    return IssueResult(
        strike_id=strike_id,
        strike_number=strike_number,
        new_consequences=new_kinds,
    )


def apply_removal(
    conn: sqlite3.Connection,
    *,
    member_id: int,
    removal_method_id: int,
    performed_on: str,
    strike_ids: list[int],
    performed_by_member_id: int | None = None,
    notes: str | None = None,
) -> RemovalResult:
    """Record a removal, close linked open strikes, re-derive numbering.

    The `v_strike_numbers` view recomputes on next read — no explicit
    re-derive call is needed; closed strikes drop out by virtue of the
    `WHERE closed_at IS NULL` clause in the view.

    Threshold-consequences from earlier crossings are NOT removed. Chair
    decides their fate via `pc_repo.resolve`.
    """
    removal_id = removal_repo.insert(
        conn,
        member_id=member_id,
        removal_method_id=removal_method_id,
        performed_on=performed_on,
        performed_by_member_id=performed_by_member_id,
        notes=notes,
    )
    closed: list[int] = []
    semester_id: int | None = None
    for sid in strike_ids:
        strike = strike_repo.get_by_id(conn, sid)
        if strike is None:
            raise LookupError(f"No strike with id={sid}")
        if strike.member_id != member_id:
            raise ValueError(f"strike {sid} belongs to member {strike.member_id}, not {member_id}")
        # Derived from every strike the caller NAMED, not just the ones this
        # call closes. Deriving it below the `continue` meant a removal that
        # closed nothing — re-running the same command, say — left it None and
        # reported active_count_after=0: "in good standing" for a member who
        # still had open strikes. The semester is a property of the strikes
        # pointed at, and those are all validated by this point.
        semester_id = strike.semester_id
        if strike.closed_at is not None:
            continue
        removal_repo.link_strike(conn, removal_id=removal_id, strike_id=sid)
        strike_repo.close(conn, sid, closed_at=performed_on)
        closed.append(sid)
    active_after = (
        strike_repo.count_active(conn, member_id=member_id, semester_id=semester_id)
        if semester_id is not None
        else 0
    )
    return RemovalResult(
        removal_id=removal_id,
        closed_strike_ids=tuple(closed),
        active_count_after=active_after,
    )


def derive_for_member_semester(
    conn: sqlite3.Connection, *, member_id: int, semester_id: int
) -> tuple[str, ...]:
    """Idempotent re-derive: insert any missing pending_consequences rows.

    Returns the kinds inserted by THIS call (empty if no work needed). Useful
    for the property tests that assert idempotency: calling twice in a row
    must return `()` on the second call.
    """
    return _sync_consequences(conn, member_id=member_id, semester_id=semester_id)


def _sync_consequences(
    conn: sqlite3.Connection, *, member_id: int, semester_id: int
) -> tuple[str, ...]:
    """Emit any newly-crossed threshold-consequences.

    The "max open count ever reached" is the maximum over all instantaneous
    open counts in this semester. Because we re-derive on every issue, an
    equivalent invariant is: the existing `pending_consequences` rows ARE the
    record of past crossings. So `target_kinds = existing_kinds ∪ kinds_for(current_open_count)`.
    Re-issuing a removed strike never re-crosses a higher threshold than the
    open count alone would justify — by construction, after N removals and N
    re-issues, the open count is back to the pre-removal level.
    """
    current_open = strike_repo.count_active(conn, member_id=member_id, semester_id=semester_id)
    needed_now = set(policy.consequence_kinds_for_count(current_open))
    existing_pcs = pc_repo.list_for_member_semester(
        conn, member_id=member_id, semester_id=semester_id
    )
    existing_kinds = {pc.kind for pc in existing_pcs}
    to_insert = needed_now - existing_kinds
    if not to_insert:
        return ()
    inserted: list[str] = []
    # Preserve canonical ordering (extra_shift, probation, expulsion_review).
    for kind in policy.CONSEQUENCE_KINDS:
        if kind not in to_insert:
            continue
        threshold = _threshold_for_kind(kind)
        triggering = strike_repo.get_strike_by_number(
            conn,
            member_id=member_id,
            semester_id=semester_id,
            strike_number=threshold,
        )
        # By construction we only emit `kind` when current_open >= threshold,
        # so the threshold-th open strike exists in v_strike_numbers.
        assert triggering is not None
        pc_repo.insert(
            conn,
            member_id=member_id,
            semester_id=semester_id,
            triggering_strike_id=triggering.id,
            kind=kind,
        )
        inserted.append(kind)
    return tuple(inserted)


def _threshold_for_kind(kind: str) -> int:
    if kind == "extra_shift":
        return policy.EXTRA_SHIFT_AT
    if kind == "probation":
        return policy.PROBATION_AT
    assert kind == "expulsion_review", f"unknown consequence kind: {kind!r}"
    return policy.EXPULSION_REVIEW_AT
