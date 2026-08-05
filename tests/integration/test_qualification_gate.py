"""The qualification gate on auto-assign.

``shift_type_required_qualification`` has been seeded since migration 0012 and
declares that ``dj`` requires the ``dj`` qualification — but nothing read it, so
auto-assign happily put a member who cannot DJ on the DJ slot. These tests pin
the gate down from the outside: they assert on who ends up in the slot, not on
the shape of the filter, so they keep holding if the implementation moves.

Two separate properties are covered, and they fail for different reasons:

1. **The gate itself** — an unqualified member must never land on a gated slot.
2. **Scarce pools get first pick** — with two DJs in a 57-person chapter, filling
   shift types in alphabetical order lets ``cleanup`` consume the only qualified
   member before ``dj`` is ever considered, and the DJ slot then goes empty while
   a DJ stands in the cleanup crew.

``over-21`` is deliberately NOT a gate (migration 0014 retracted the bar
requirement); the last test guards that so it cannot be quietly reinstated.
"""

from __future__ import annotations

import sqlite3

import pytest

from risk.db.connection import transaction
from risk.repos import event_types as etypes_repo
from risk.repos import events as events_repo
from risk.repos import member_qualifications as mq_repo
from risk.repos import member_statuses as statuses_repo
from risk.repos import members as members_repo
from risk.repos import qualifications as quals_repo
from risk.repos import semesters as semesters_repo
from risk.services import assignment
from risk.services import shift_requirements as reqs_svc

pytestmark = pytest.mark.integration


def _world(
    db: sqlite3.Connection, *, event_type: str = "mixer", n_members: int = 8
) -> tuple[int, int, dict[str, int]]:
    """A semester, one off-site event of ``event_type``, and N identical members.

    Every member shares a class year and carries no pledge class, so the
    fairness tiebreaker chain collapses to ``member_slug`` ASC — the pick order
    is exactly qm-01, qm-02, ... which is what makes the starvation test
    deterministic rather than lucky.

    ``host_house_id=None`` keeps the host-house filter out of the picture.
    """
    sem_id = semesters_repo.insert(
        db, name="FA26", starts_on="2026-08-25", ends_on="2026-12-05"
    )
    et = etypes_repo.get_by_slug(db, event_type)
    assert et is not None, f"event type {event_type!r} not seeded"
    event_id = events_repo.insert(
        db,
        semester_id=sem_id,
        event_type_id=et.id,
        display_name=f"{event_type} night",
        date="2026-09-11",
        host_house_id=None,
    )
    active = statuses_repo.get_by_slug(db, "active")
    assert active is not None
    members = {
        f"qm-{i:02d}": members_repo.insert(
            db,
            slug=f"qm-{i:02d}",
            display_name=f"Test Member {i:02d}",
            status_id=active.id,
            class_year=2027,
        )
        for i in range(1, n_members + 1)
    }
    reqs_svc.snapshot_for_event(db, event_id)
    db.commit()
    return sem_id, event_id, members


def _grant(db: sqlite3.Connection, *, member_id: int, semester_id: int, slug: str) -> None:
    qual = quals_repo.get_by_slug(db, slug)
    assert qual is not None, f"qualification {slug!r} not seeded"
    with transaction(db):
        mq_repo.grant(
            db, member_id=member_id, qualification_id=qual.id, semester_id=semester_id
        )


def _slot(result: assignment.AutoAssignResult, slug: str) -> assignment.ProposedAssignment:
    matches = [a for a in result.assignments if a.shift_type_slug == slug]
    assert len(matches) == 1, f"expected exactly one {slug} slot, got {len(matches)}"
    return matches[0]


def test_dj_slot_stays_empty_when_nobody_is_qualified(db: sqlite3.Connection) -> None:
    """No DJ in the chapter must mean an unfilled DJ slot, not a random body.

    Before the gate existed this slot came back filled with whoever fairness
    ranked highest — a member who cannot DJ, silently scheduled to DJ.
    """
    _, event_id, _ = _world(db)
    with transaction(db):
        result = assignment.auto_assign(db, event_id=event_id, seed=7)

    dj = _slot(result, "dj")
    assert dj.member_id is None
    assert dj.reason == "pool_empty"
    # The ungated shift types are untouched by the gate.
    assert all(
        a.member_id is not None
        for a in result.assignments
        if a.shift_type_slug in {"door", "driver"}
    )


def test_dj_slot_goes_to_the_qualified_member(db: sqlite3.Connection) -> None:
    """The one member holding ``dj`` gets the slot, however fairness ranks them."""
    sem_id, event_id, members = _world(db)
    _grant(db, member_id=members["qm-07"], semester_id=sem_id, slug="dj")

    with transaction(db):
        result = assignment.auto_assign(db, event_id=event_id, seed=7)

    assert _slot(result, "dj").member_slug == "qm-07"


def test_scarce_qualified_pool_is_filled_before_the_open_ones(
    db: sqlite3.Connection,
) -> None:
    """A gated slot must not be starved by an alphabetically-earlier shift type.

    ``qm-01`` sorts first on every fairness key and is the chapter's only DJ.
    Filling shift types in slug order runs ``cleanup`` (4 slots) first, which
    takes qm-01..qm-04 — and then ``dj`` has an empty pool despite a qualified
    member being available when the run started. The DJ slot is the scarcest in
    the event and has to be filled first.
    """
    sem_id, event_id, members = _world(db)
    _grant(db, member_id=members["qm-01"], semester_id=sem_id, slug="dj")

    with transaction(db):
        result = assignment.auto_assign(db, event_id=event_id, seed=7)

    assert _slot(result, "dj").member_slug == "qm-01"
    # And qm-01 is not double-booked onto cleanup as well.
    assert [a.member_slug for a in result.assignments if a.shift_type_slug == "cleanup"] == [
        "qm-02",
        "qm-03",
        "qm-04",
        "qm-05",
    ]


def test_over_21_does_not_gate_the_bar(db: sqlite3.Connection) -> None:
    """Regression guard: the bar age gate was added, then retracted as wrong.

    The ``over-21`` qualification is informational data only — it records who
    purchases the alcohol, which is the juice task the chair does not track.
    Nobody here holds it, and the bar slots must still fill.
    """
    _, event_id, _ = _world(db, event_type="dage")

    with transaction(db):
        result = assignment.auto_assign(db, event_id=event_id, seed=7)

    bar = [a for a in result.assignments if a.shift_type_slug == "bar"]
    assert bar, "dage should carry bar slots"
    assert all(a.member_id is not None for a in bar)
