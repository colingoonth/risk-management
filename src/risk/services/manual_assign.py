"""Chair overrides: putting a specific member on a specific slot, by hand.

Until now there was no way to do this from any shipped surface. ``risk shift``
had only ``list`` and ``show``, the API could not mutate a shift, and the web
client had no control for it — so the one thing a chair does constantly, moving
somebody, required raw SQL.

TWO RULES, and they pull in opposite directions.

An override is a DECISION made with information the database does not hold: who
is reliable, who is falling out with whom, who asked for a favour. The solver
cannot re-derive any of it, so a rebuild must not overwrite it. That is what
``chair_set`` is for.

But an override is still subject to physics. A member away at a tournament
cannot work a shift because the chair says so, and a member who cannot DJ still
cannot DJ. Every check the fill applies is applied here too — the difference is
only WHO chose, not whether the choice is possible. Skipping them would make the
override the one path that can produce an unworkable schedule, which is exactly
the path a chair uses when they are in a hurry.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from risk.repos import events as events_repo
from risk.repos import members as members_repo
from risk.repos import pledge_modes as pmodes_repo
from risk.repos import shifts as shifts_repo
from risk.services import availability, eligibility


@dataclass(frozen=True, slots=True)
class ManualAssignResult:
    shift_id: int
    event_name: str
    event_date: str
    shift_type_slug: str
    slot_index: int
    member_slug: str
    display_name: str
    replaced_slug: str | None
    warnings: list[str]


def resolve_slot(
    conn: sqlite3.Connection,
    *,
    event_key: str,
    shift_type_slug: str,
    slot_index: int,
) -> shifts_repo.Shift:
    """Find a slot by event + job + position, because shift IDs are unmemorable.

    A chair looking at the sheet knows "the second door slot on the KD mixer".
    Making them look up an integer first is how a correction does not get made.
    """
    event = events_repo.resolve(conn, event_key)
    if event is None:
        raise LookupError(f"event {event_key!r} not found")
    for shift in shifts_repo.list_for_event(conn, event.id):
        if shift.shift_type_slug == shift_type_slug and shift.slot_index == slot_index:
            return shift
    raise LookupError(
        f"{event.display_name} has no {shift_type_slug} slot {slot_index} (slots are 0-indexed)"
    )


def check(conn: sqlite3.Connection, *, shift: shifts_repo.Shift, member_id: int) -> list[str]:
    """Everything that would stop this assignment. Empty list means go.

    Returns reasons rather than raising on the first one: a chair told "he is
    unavailable" will fix that and resubmit, only to be told he is also
    already working that night. One round trip, all the problems.
    """
    event = events_repo.get_by_id(conn, shift.event_id)
    if event is None:
        raise LookupError(f"event {shift.event_id} not found")

    problems: list[str] = []
    pool = eligibility.eligible_for(
        conn,
        event_id=event.id,
        semester_id=event.semester_id,
        host_house_id=event.host_house_id,
    )
    if not any(m.member_id == member_id for m in pool.eligible):
        problems.append(
            "not in the eligible pool for this event (status, host house, or a hard-excluded role)"
        )

    narrowed = eligibility.filter_for_shift_type(
        conn,
        pool=pool.eligible,
        shift_type_id=shift.shift_type_id,
        semester_id=event.semester_id,
    )
    if narrowed.required_qualification_slugs and not any(
        m.member_id == member_id for m in narrowed.eligible
    ):
        problems.append(
            f"does not hold {'+'.join(narrowed.required_qualification_slugs)}, "
            f"required for {shift.shift_type_slug}"
        )

    if not availability.can_cover(
        conn,
        member_id=member_id,
        semester_id=event.semester_id,
        shift_type_id=shift.shift_type_id,
        event_date=event.date,
        honor_soft=False,
    ):
        problems.append(
            f"unavailable for the {shift.shift_type_slug} window "
            f"(which is not always the party date — cleanup is the next morning)"
        )

    clash = conn.execute(
        """
        SELECT st.slug FROM shifts s
        JOIN shift_types st ON st.id = s.shift_type_id
        WHERE s.event_id = ? AND s.assigned_member_id = ? AND s.id <> ?
        """,
        (shift.event_id, member_id, shift.id),
    ).fetchone()
    if clash is not None:
        problems.append(f"already working {clash['slug']} at this event")
    return problems


def assign(
    conn: sqlite3.Connection,
    *,
    shift_id: int,
    member_key: str,
    force: bool = False,
) -> ManualAssignResult:
    """Put a member on a slot and mark it chair-set.

    ``force`` records the override anyway and returns the problems as warnings.
    It exists because the chair is sometimes right and the data is sometimes
    stale — a tournament that got cancelled, a role not yet removed. What it
    does NOT do is hide the objection: the reasons come back either way, and on
    the audit row, so "we knew" is answerable later.
    """
    shift = shifts_repo.get_by_id(conn, shift_id)
    if shift is None:
        raise LookupError(f"shift {shift_id} not found")
    member = members_repo.resolve(conn, member_key)
    if member is None:
        raise LookupError(f"no member matching {member_key!r}")
    event = events_repo.get_by_id(conn, shift.event_id)
    assert event is not None

    problems = check(conn, shift=shift, member_id=member.id)
    if problems and not force:
        raise ValueError(
            f"{member.display_name} cannot take {shift.shift_type_slug} "
            f"slot {shift.slot_index} at {event.display_name}: "
            + "; ".join(problems)
            + ". Pass --force to override anyway."
        )

    replaced = shift.assigned_member_slug
    mode = pmodes_repo.get(conn, "normal")
    assert mode is not None
    if shift.assigned_member_id is not None:
        shifts_repo.unassign(conn, shift_id=shift.id)
    shifts_repo.assign(
        conn,
        shift_id=shift.id,
        member_id=member.id,
        effective_pledge_mode_id=(shift.effective_pledge_mode_id or mode.id),
        assigned_at=event.date,
        chair_set=True,
    )
    return ManualAssignResult(
        shift_id=shift.id,
        event_name=event.display_name,
        event_date=event.date,
        shift_type_slug=shift.shift_type_slug,
        slot_index=shift.slot_index,
        member_slug=member.slug,
        display_name=member.display_name,
        replaced_slug=replaced,
        warnings=problems,
    )


def unassign(conn: sqlite3.Connection, *, shift_id: int) -> shifts_repo.Shift:
    """Empty a slot. Clears ``chair_set`` with it — an empty slot holds no
    decision, and leaving the flag would make the next fill skip a hole."""
    shift = shifts_repo.get_by_id(conn, shift_id)
    if shift is None:
        raise LookupError(f"shift {shift_id} not found")
    if shift.assigned_member_id is None:
        raise ValueError(f"shift {shift_id} is already open")
    shifts_repo.unassign(conn, shift_id=shift_id)
    return shift


def clear_semester(
    conn: sqlite3.Connection, *, semester_id: int, keep_chair_set: bool = True
) -> tuple[int, int]:
    """Wipe a term's shifts before a rebuild. Returns (deleted, kept).

    THE SUPPORTED WAY TO REBUILD. Every rebuild so far has been a raw
    ``DELETE FROM shifts``, which discards chair overrides silently — and that
    is the operation reached for whenever anything upstream changes, which is
    constantly. A note reminding somebody to re-apply an override by hand is not
    a mechanism.

    ``keep_chair_set=False`` exists for the rare deliberate reset, and says what
    it is doing at the call site rather than being the accidental default.
    """
    where = "event_id IN (SELECT id FROM events WHERE semester_id = ?)"
    params: list[object] = [semester_id]
    kept = 0
    if keep_chair_set:
        kept = int(
            conn.execute(
                f"SELECT COUNT(*) AS n FROM shifts WHERE {where} AND chair_set = 1",
                params,
            ).fetchone()["n"]
        )
        where += " AND chair_set = 0"
    deleted = conn.execute(f"DELETE FROM shifts WHERE {where}", params).rowcount
    conn.execute(
        """
        DELETE FROM auto_assign_runs
        WHERE event_id IN (SELECT id FROM events WHERE semester_id = ?)
        """,
        (semester_id,),
    )
    return deleted, kept
