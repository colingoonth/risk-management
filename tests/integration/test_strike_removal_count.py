"""``active_count_after`` must report the member's real standing.

``apply_removal`` derives the semester to count against from the strikes it
CLOSES. When it closes none — because every id passed was already closed — no
semester was ever set, and the count fell back to a hard-coded 0. The chair was
told "in good standing" about a member who still had open strikes.

The stored data was never wrong; ``risk strike standing`` shows the truth. It is
the removal's own report that lied, and that is the number the chair reads at
the moment they act. Re-running a strike-removal command is the ordinary way to
hit it.

The semester is a property of the strikes the caller NAMED, not of the subset
that happened to still be open, so it is derived a step earlier.
"""

from __future__ import annotations

import sqlite3

import pytest

from risk.db.connection import transaction
from risk.repos import member_statuses as statuses_repo
from risk.repos import members as members_repo
from risk.repos import removal_methods as methods_repo
from risk.repos import semesters as semesters_repo
from risk.repos import strikes as strikes_repo
from risk.services import strike_state

pytestmark = pytest.mark.integration


@pytest.fixture()
def member_with_three_strikes(
    db: sqlite3.Connection,
) -> tuple[int, int, list[int], int]:
    sem_id = semesters_repo.insert(
        db, name="FA26", starts_on="2026-08-25", ends_on="2026-12-05"
    )
    active = statuses_repo.get_by_slug(db, "active")
    assert active is not None
    member_id = members_repo.insert(
        db, slug="striker", display_name="Striker", status_id=active.id
    )
    strike_ids = []
    with transaction(db):
        for i in range(3):
            strike_ids.append(
                strikes_repo.insert(
                    db,
                    member_id=member_id,
                    semester_id=sem_id,
                    reason=f"no-show {i + 1}",
                    issued_on="2026-09-11",
                )
            )
    methods = methods_repo.list_active(db)
    assert methods, "removal methods should be seeded"
    return member_id, sem_id, strike_ids, methods[0].id


def test_removal_reports_the_true_count_after_closing_one(
    db: sqlite3.Connection, member_with_three_strikes: tuple[int, int, list[int], int]
) -> None:
    member_id, _, strike_ids, method_id = member_with_three_strikes
    with transaction(db):
        result = strike_state.apply_removal(
            db,
            member_id=member_id,
            removal_method_id=method_id,
            strike_ids=[strike_ids[0]],
            performed_on="2026-09-20",
        )
    assert result.closed_strike_ids == (strike_ids[0],)
    assert result.active_count_after == 2


def test_a_no_op_removal_still_reports_the_true_count(
    db: sqlite3.Connection, member_with_three_strikes: tuple[int, int, list[int], int]
) -> None:
    """Re-running the same removal closes nothing — and must not claim zero."""
    member_id, _, strike_ids, method_id = member_with_three_strikes
    with transaction(db):
        strike_state.apply_removal(
            db,
            member_id=member_id,
            removal_method_id=method_id,
            strike_ids=[strike_ids[0]],
            performed_on="2026-09-20",
        )
    with transaction(db):
        second = strike_state.apply_removal(
            db,
            member_id=member_id,
            removal_method_id=method_id,
            strike_ids=[strike_ids[0]],
            performed_on="2026-09-21",
        )

    assert second.closed_strike_ids == ()
    assert second.active_count_after == 2, (
        "a removal that closed nothing must report the standing that still holds"
    )


def test_removal_of_every_strike_really_does_report_zero(
    db: sqlite3.Connection, member_with_three_strikes: tuple[int, int, list[int], int]
) -> None:
    """Guard the other direction: zero must still mean zero."""
    member_id, _, strike_ids, method_id = member_with_three_strikes
    with transaction(db):
        result = strike_state.apply_removal(
            db,
            member_id=member_id,
            removal_method_id=method_id,
            strike_ids=strike_ids,
            performed_on="2026-09-20",
        )
    assert set(result.closed_strike_ids) == set(strike_ids)
    assert result.active_count_after == 0
