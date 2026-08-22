"""Strike make-up shifts: the penalty half of the schedule.

The chapter's rule is one unserved strike, one make-up shift. Twenty-one active
members carried one into FA26 from the spring strike sheet. Colin's rulings,
each of which is a test below:

  - the make-ups go on the FIRST confirmed parties, not spread across the term
  - they do NOT count toward the member's season total
  - a hard exemption spares an officer the rotation, not a strike he earned
  - the DJ who owes one works a real risk shift; the other DJ covers that night

Every one of these is a place where the obvious implementation is wrong in a way
that would not show up until someone read the published sheet.
"""

from __future__ import annotations

import sqlite3

import pytest

from risk.db.connection import transaction
from risk.repos import event_types as etypes_repo
from risk.repos import events as events_repo
from risk.repos import member_qualifications as mq_repo
from risk.repos import member_roles as mroles_repo
from risk.repos import member_statuses as statuses_repo
from risk.repos import members as members_repo
from risk.repos import qualifications as quals_repo
from risk.repos import roles as roles_repo
from risk.repos import semesters as semesters_repo
from risk.repos import shifts as shifts_repo
from risk.repos import strikes as strikes_repo
from risk.services import assignment
from risk.services import shift_requirements as reqs_svc

pytestmark = pytest.mark.integration


def _world(db: sqlite3.Connection, *, n_members: int = 30) -> tuple[int, list[int]]:
    sem_id = semesters_repo.insert(db, name="FA26", starts_on="2026-08-20", ends_on="2026-12-19")
    active = statuses_repo.get_by_slug(db, "active")
    assert active is not None
    ids = [
        members_repo.insert(
            db,
            slug=f"brother-{i:02d}",
            display_name=f"Brother {i:02d}",
            status_id=active.id,
            class_year=2028,
            pledge_class="Zeta",
        )
        for i in range(n_members)
    ]
    return sem_id, ids


def _event(
    db: sqlite3.Connection,
    sem_id: int,
    *,
    date: str,
    planning_status: str = "confirmed",
    type_slug: str = "mixer",
) -> int:
    etype = etypes_repo.get_by_slug(db, type_slug)
    assert etype is not None
    event_id = events_repo.insert(
        db,
        semester_id=sem_id,
        event_type_id=etype.id,
        display_name=f"{type_slug} {date} {planning_status}",
        date=date,
        planning_status=planning_status,
    )
    reqs_svc.snapshot_for_event(db, event_id)
    return event_id


def _strike(db: sqlite3.Connection, *, member_id: int, sem_id: int, on: str = "2026-04-19") -> int:
    return strikes_repo.insert(
        db,
        member_id=member_id,
        semester_id=sem_id,
        issued_on=on,
        reason="Social Risk (carried from SP26)",
    )


def _shifts_of(db: sqlite3.Connection, member_id: int) -> list[shifts_repo.Shift]:
    return shifts_repo.list_filtered(db, member_id=member_id)


def test_a_member_who_owes_a_strike_is_seated_at_the_first_party(
    db: sqlite3.Connection,
) -> None:
    sem_id, ids = _world(db)
    _strike(db, member_id=ids[7], sem_id=sem_id)
    first = _event(db, sem_id, date="2026-08-28")
    _event(db, sem_id, date="2026-09-04")

    with transaction(db):
        result = assignment.auto_assign(db, event_id=first, seed=1)

    makeups = [a for a in result.assignments if a.reason == "strike_makeup"]
    assert len(makeups) == 1
    assert makeups[0].member_id == ids[7]
    assert any("strike make-up" in w for w in result.warnings), (
        "the chair must be told, by name, who is working a penalty"
    )

    worked = _shifts_of(db, ids[7])
    assert len(worked) == 1
    assert worked[0].serves_strike_id is not None


def test_a_make_up_does_not_count_toward_the_season_total(db: sqlite3.Connection) -> None:
    """The point of the penalty. Counting it would refund it.

    If a make-up counted, the extra work would push the member down the fairness
    queue and take a rotation shift back off him — same season load, strike
    costs nothing.
    """
    sem_id, ids = _world(db)
    _strike(db, member_id=ids[3], sem_id=sem_id)
    event_id = _event(db, sem_id, date="2026-08-28")
    with transaction(db):
        assignment.auto_assign(db, event_id=event_id, seed=1)

    assert len(_shifts_of(db, ids[3])) == 1, "he is standing a shift"
    assert (
        shifts_repo.rotation_effort_in_semester_through_date(
            db, member_id=ids[3], semester_id=sem_id, on_or_before="2026-12-31"
        )
        == 0
    ), "but it buys him nothing against his quota"


def test_a_served_strike_is_not_re_placed_at_the_next_party(
    db: sqlite3.Connection,
) -> None:
    """One strike, one shift — not one shift per remaining event.

    The guard is ``shifts.serves_strike_id`` rather than ``closed_at``: the
    chair closes a strike after the shift is actually worked, which can be weeks
    later, and until then a closed_at-only test would re-seat the member at
    every single party in between.
    """
    sem_id, ids = _world(db)
    _strike(db, member_id=ids[5], sem_id=sem_id)
    first = _event(db, sem_id, date="2026-08-28")
    second = _event(db, sem_id, date="2026-09-04")

    with transaction(db):
        assignment.auto_assign(db, event_id=first, seed=1)
        second_result = assignment.auto_assign(db, event_id=second, seed=1)

    assert [a for a in second_result.assignments if a.reason == "strike_makeup"] == []
    makeup_shifts = [s for s in _shifts_of(db, ids[5]) if s.serves_strike_id is not None]
    assert len(makeup_shifts) == 1


def test_placeholders_never_carry_a_make_up(db: sqlite3.Connection) -> None:
    """A cancelled placeholder would leave the strike unserved and unnoticed.

    Roughly half the held dates never become parties. A member who worked one
    off there believes he has paid, and the ledger agrees, and neither is true.
    """
    sem_id, ids = _world(db)
    _strike(db, member_id=ids[2], sem_id=sem_id)
    held = _event(db, sem_id, date="2026-08-25", planning_status="placeholder")

    with transaction(db):
        result = assignment.auto_assign(db, event_id=held, seed=1)

    assert [a for a in result.assignments if a.reason == "strike_makeup"] == []
    assert [s for s in _shifts_of(db, ids[2]) if s.serves_strike_id is not None] == []


def test_a_hard_exempt_officer_still_works_his_strike(db: sqlite3.Connection) -> None:
    """Colin's ruling: the exemption is from the rotation, not from discipline.

    The social chair carrying a spring strike appears in the ledger with one
    make-up shift and zero rotation shifts, his exemption reason still printed.
    """
    sem_id, ids = _world(db)
    officer = ids[0]
    role_id = roles_repo.insert(
        db,
        slug="fixture-hard-exempt",
        display_name="Fixture Hard Exempt",
        default_excluded=True,
        soft=False,
    )
    mroles_repo.set_role(db, member_id=officer, role_id=role_id, semester_id=sem_id)
    _strike(db, member_id=officer, sem_id=sem_id)
    event_id = _event(db, sem_id, date="2026-08-28")

    with transaction(db):
        result = assignment.auto_assign(db, event_id=event_id, seed=1)

    makeups = [a for a in result.assignments if a.reason == "strike_makeup"]
    assert [a.member_id for a in makeups] == [officer]
    # He is still excluded from the ROTATION — the exemption did its normal job.
    assert result.eligibility.excluded_by_hard_role == 1
    assert officer not in [a.member_id for a in result.assignments if a.reason == "assigned"]


def test_the_dj_who_owes_a_strike_works_risk_and_the_other_dj_covers(
    db: sqlite3.Connection,
) -> None:
    """Colin's ruling, and it needs no special case to hold.

    The make-up pass runs before the gated fill, so seating the indebted DJ on a
    risk slot puts him in ``already_assigned``, and the dj pool below is left
    with exactly one candidate. If the passes were ever reordered, this test is
    what would catch it.
    """
    sem_id, ids = _world(db)
    dj_qual = quals_repo.get_by_slug(db, "dj")
    assert dj_qual is not None
    indebted_dj, other_dj = ids[0], ids[1]
    for member_id in (indebted_dj, other_dj):
        mq_repo.grant(db, member_id=member_id, qualification_id=dj_qual.id, semester_id=sem_id)
    _strike(db, member_id=indebted_dj, sem_id=sem_id)
    event_id = _event(db, sem_id, date="2026-08-28")

    with transaction(db):
        result = assignment.auto_assign(db, event_id=event_id, seed=1)

    by_slug = {(a.shift_type_slug, a.slot_index): a for a in result.assignments if a.member_id}
    dj_pick = by_slug[("dj", 0)]
    assert dj_pick.member_id == other_dj, "the indebted DJ is busy; the other covers"

    indebted_shifts = _shifts_of(db, indebted_dj)
    assert len(indebted_shifts) == 1
    assert indebted_shifts[0].shift_type_slug != "dj", (
        "a DJ night is not risk work and must not settle a strike"
    )
    assert indebted_shifts[0].serves_strike_id is not None


def test_two_strikes_on_one_member_are_worked_on_different_nights(
    db: sqlite3.Connection,
) -> None:
    """Two debts cannot be settled by standing one post.

    Reachable: the spring sheet has members carrying two, and the app's own
    threshold ladder issues a second at EXTRA_SHIFT_AT.
    """
    sem_id, ids = _world(db)
    member = ids[9]
    _strike(db, member_id=member, sem_id=sem_id, on="2026-04-12")
    _strike(db, member_id=member, sem_id=sem_id, on="2026-04-19")
    first = _event(db, sem_id, date="2026-08-28")
    second = _event(db, sem_id, date="2026-09-04")

    with transaction(db):
        assignment.auto_assign(db, event_id=first, seed=1)
        assignment.auto_assign(db, event_id=second, seed=1)

    makeups = [s for s in _shifts_of(db, member) if s.serves_strike_id is not None]
    assert len(makeups) == 2
    assert len({s.event_id for s in makeups}) == 2, "one per night"
    assert len({s.serves_strike_id for s in makeups}) == 2, "one per strike"


def test_freeing_a_make_up_slot_releases_the_strike(db: sqlite3.Connection) -> None:
    """Unassigning must clear the link, or the strike becomes unservable.

    The unique index allows one shift per strike. If ``unassign`` left the link
    on an empty slot, the strike would be permanently claimed by a shift nobody
    is standing, and no later fill could place it anywhere else.
    """
    sem_id, ids = _world(db)
    strike_id = _strike(db, member_id=ids[4], sem_id=sem_id)
    event_id = _event(db, sem_id, date="2026-08-28")
    with transaction(db):
        assignment.auto_assign(db, event_id=event_id, seed=1)

    makeup = next(s for s in _shifts_of(db, ids[4]) if s.serves_strike_id == strike_id)
    with transaction(db):
        shifts_repo.unassign(db, shift_id=makeup.id)

    assert [d.strike_id for d in strikes_repo.list_unserved(db, semester_id=sem_id)] == [
        strike_id
    ], "the debt is owed again once nobody is standing it"


def test_a_preference_cannot_hand_a_gated_shift_to_one_person(
    db: sqlite3.Connection,
) -> None:
    """Soft unavailability is ignored for gated shift types, by design.

    A gated pool is tiny by construction — two members hold the dj
    qualification against a 40-party term. Sorting one of them behind the other
    for a preference does not spread the work; it hands the entire term to
    whoever is left. Measured on the real calendar before the exception existed:
    42 nights to one DJ, 1 to the other, which is the same collapse the per-type
    rotation key was written to prevent.

    With no alternative pool there is no "unless the slot goes unfilled" for the
    preference to yield to, so the turn count has to win outright.
    """
    sem_id, ids = _world(db)
    dj_qual = quals_repo.get_by_slug(db, "dj")
    assert dj_qual is not None
    a, b = ids[0], ids[1]
    for member_id in (a, b):
        mq_repo.grant(db, member_id=member_id, qualification_id=dj_qual.id, semester_id=sem_id)
    # `a` would rather not work party nights. dj IS a party-night window.
    from risk.repos import unavailability as unav_repo

    with transaction(db):
        unav_repo.insert(
            db,
            member_id=a,
            semester_id=sem_id,
            starts_on="2026-08-20",
            ends_on="2026-12-19",
            starts_at_time="20:00",
            ends_at_time="23:59",
            is_soft=True,
            reason="prefers setup",
        )
    events = [_event(db, sem_id, date=f"2026-09-{d:02d}") for d in (4, 11, 18, 25)]
    with transaction(db):
        for event_id in events:
            assignment.auto_assign(db, event_id=event_id, seed=1)

    counts = {
        m: db.execute(
            """SELECT COUNT(*) AS n FROM shifts s
               JOIN shift_types st ON st.id = s.shift_type_id
               WHERE s.assigned_member_id = ? AND st.slug = 'dj'""",
            (m,),
        ).fetchone()["n"]
        for m in (a, b)
    }
    assert sum(counts.values()) == len(events), "every dj slot filled"
    assert abs(counts[a] - counts[b]) <= 1, (
        f"the two DJs must still alternate despite the preference: {counts}"
    )
