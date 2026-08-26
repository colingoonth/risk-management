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
from risk.services import availability, eligibility, policy


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

    # Krush and Dage bar seniors from door, bar and rides outright. Checked here
    # as well as in the fill because the two write paths must agree: a rule the
    # auto-assign honours and the chair's own `risk shift assign` does not is a
    # rule that gets broken by the person most likely to be asked why.
    # --force still lands it, with this in the warnings and on the audit row.
    if policy.senior_is_barred(event.event_type_slug, shift.shift_type_slug):
        member = conn.execute(
            "SELECT class_year FROM members WHERE id = ?", (member_id,)
        ).fetchone()
        sem = conn.execute(
            "SELECT starts_on FROM semesters WHERE id = ?", (event.semester_id,)
        ).fetchone()
        if member is not None and sem is not None:
            term_start_year = int(sem["starts_on"][:4])
            term_is_fall = int(sem["starts_on"][5:7]) >= 7
            if policy.is_senior_in_term(
                member["class_year"],
                term_start_year=term_start_year,
                term_is_fall=term_is_fall,
            ):
                problems.append(
                    f"seniors do not work {shift.shift_type_slug} at a "
                    f"{event.event_type_slug}"
                )

    # One COUNTED shift per member per event, plus an uncounted one (dj) on top
    # provided it does not occupy the same hours. Mirrors the rule in
    # assignment.auto_assign — see the long comment there. A flat "already
    # working X at this event" was blocking a DJ from the setup crew at the
    # party he was DJing, which is neither a clash of time (setup runs the three
    # days up to the party, dj is 20:00-23:59) nor a clash of load (a dj night
    # counts toward no tally).
    want = conn.execute(
        """
        SELECT st.counts_toward_tally,
               COALESCE(w.occupies_event_night, 1) AS occupies_event_night
        FROM shift_types st
        LEFT JOIN shift_type_windows w ON w.shift_type_id = st.id
        WHERE st.id = ?
        """,
        (shift.shift_type_id,),
    ).fetchone()
    held = conn.execute(
        """
        SELECT st.slug, st.counts_toward_tally,
               COALESCE(w.occupies_event_night, 1) AS occupies_event_night
        FROM shifts s
        JOIN shift_types st ON st.id = s.shift_type_id
        LEFT JOIN shift_type_windows w ON w.shift_type_id = st.id
        WHERE s.event_id = ? AND s.assigned_member_id = ? AND s.id <> ?
        """,
        (shift.event_id, member_id, shift.id),
    ).fetchall()
    if want is not None:
        for r in held:
            if want["counts_toward_tally"] and r["counts_toward_tally"]:
                problems.append(f"already working {r['slug']} at this event")
                break
            if want["occupies_event_night"] and r["occupies_event_night"]:
                problems.append(
                    f"already working {r['slug']} on the night of this event "
                    f"(both are 20:00-23:59)"
                )
                break
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
    conn: sqlite3.Connection,
    *,
    semester_id: int,
    keep_chair_set: bool = True,
    on_or_after: str | None = None,
    on_or_before: str | None = None,
) -> tuple[int, int]:
    """Wipe a term's shifts before a rebuild. Returns (deleted, kept).

    THE SUPPORTED WAY TO REBUILD. Every rebuild so far has been a raw
    ``DELETE FROM shifts``, which discards chair overrides silently — and that
    is the operation reached for whenever anything upstream changes, which is
    constantly. A note reminding somebody to re-apply an override by hand is not
    a mechanism.

    ``keep_chair_set=False`` exists for the rare deliberate reset, and says what
    it is doing at the call site rather than being the accidental default.

    ``on_or_after`` / ``on_or_before`` bound the wipe by EVENT DATE. Added
    2026-08-26 with the move to fortnightly publishing, and it is the half of
    that change that actually needed code: a chair rebuilding 30 Aug onward must
    not touch the week already sent out, because those brothers have been told
    what they are working and a missed shift is a strike. Without a bound the
    only available operation was "clear the whole term", which is precisely the
    unbounded delete this function exists to replace — one date wrong and a
    published weekend evaporates.

    The auto-assign audit rows are deleted over the same range, not the whole
    term, so a run record survives for the events that were left alone.
    """
    where = "event_id IN (SELECT id FROM events WHERE semester_id = ?"
    params: list[object] = [semester_id]
    if on_or_after is not None:
        where += " AND date >= ?"
        params.append(on_or_after)
    if on_or_before is not None:
        where += " AND date <= ?"
        params.append(on_or_before)
    where += ")"

    kept = 0
    if keep_chair_set:
        kept = int(
            conn.execute(
                f"SELECT COUNT(*) AS n FROM shifts WHERE {where} AND chair_set = 1",
                params,
            ).fetchone()["n"]
        )
    deleted = conn.execute(
        f"DELETE FROM shifts WHERE {where}" + (" AND chair_set = 0" if keep_chair_set else ""),
        params,
    ).rowcount
    conn.execute(f"DELETE FROM auto_assign_runs WHERE {where}", params)
    return deleted, kept
