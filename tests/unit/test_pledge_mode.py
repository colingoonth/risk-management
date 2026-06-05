"""Unit tests for ``services.pledge_mode``: 3-mode resolver + auto-downgrade table.

Uses the DB fixture only to look up the seeded pledge_modes lookup IDs;
all behavior under test is pure-function decision-table logic.
"""

from __future__ import annotations

import sqlite3

import pytest

from risk.repos import houses as houses_repo
from risk.repos import pledge_modes as pmodes_repo
from risk.repos import semesters as semesters_repo
from risk.services import pledge_mode
from risk.services.pledge_mode import MODE_FULL, MODE_NORMAL, MODE_PARTIAL

pytestmark = pytest.mark.integration


def _setup_house_and_semester(db: sqlite3.Connection) -> tuple[int, int]:
    sem_id = semesters_repo.insert(db, name="SP26", starts_on="2026-01-15", ends_on="2026-05-15")
    house_id = houses_repo.insert(db, slug="zta", display_name="ZTA")
    return sem_id, house_id


def _set_house_mode(db: sqlite3.Connection, house_id: int, sem_id: int, mode_slug: str) -> None:
    from risk.repos import house_semester_status as hss_repo

    mode = pmodes_repo.get(db, mode_slug)
    assert mode is not None
    hss_repo.set_mode(db, house_id=house_id, semester_id=sem_id, pledge_mode_id=mode.id)


@pytest.mark.parametrize(
    ("configured", "eligible_pledges", "total_required", "expected"),
    [
        # normal: no auto-upgrades
        (MODE_NORMAL, 0, 5, MODE_NORMAL),
        (MODE_NORMAL, 10, 5, MODE_NORMAL),
        # partial: stays partial while any pledges available; downgrades when zero
        (MODE_PARTIAL, 1, 5, MODE_PARTIAL),
        (MODE_PARTIAL, 5, 5, MODE_PARTIAL),
        (MODE_PARTIAL, 0, 5, MODE_NORMAL),
        # full: downgrades to partial when pledges < required, then to normal if zero
        (MODE_FULL, 5, 5, MODE_FULL),
        (MODE_FULL, 10, 5, MODE_FULL),
        (MODE_FULL, 4, 5, MODE_PARTIAL),
        (MODE_FULL, 1, 5, MODE_PARTIAL),
        (MODE_FULL, 0, 5, MODE_NORMAL),
        # total_required = 0 (no shifts): full stays full since 0 pledges suffices
        (MODE_FULL, 0, 0, MODE_FULL),
        (MODE_PARTIAL, 0, 0, MODE_NORMAL),
    ],
)
def test_pledge_mode_decision_table(
    db: sqlite3.Connection,
    configured: str,
    eligible_pledges: int,
    total_required: int,
    expected: str,
) -> None:
    sem_id, house_id = _setup_house_and_semester(db)
    _set_house_mode(db, house_id, sem_id, configured)
    result = pledge_mode.resolve(
        db,
        host_house_id=house_id,
        semester_id=sem_id,
        eligible_pledges=eligible_pledges,
        eligible_brothers=10,
        total_required=total_required,
    )
    assert result.configured_slug == configured
    assert result.resolved_slug == expected


def test_off_site_event_defaults_to_normal(db: sqlite3.Connection) -> None:
    sem_id, _ = _setup_house_and_semester(db)
    result = pledge_mode.resolve(
        db,
        host_house_id=None,
        semester_id=sem_id,
        eligible_pledges=10,
        eligible_brothers=10,
        total_required=5,
    )
    assert result.configured_slug == MODE_NORMAL
    assert result.resolved_slug == MODE_NORMAL


def test_unset_house_mode_defaults_to_normal(db: sqlite3.Connection) -> None:
    sem_id, house_id = _setup_house_and_semester(db)
    # No house_semester_status row — should default to 'normal'.
    result = pledge_mode.resolve(
        db,
        host_house_id=house_id,
        semester_id=sem_id,
        eligible_pledges=10,
        eligible_brothers=10,
        total_required=5,
    )
    assert result.configured_slug == MODE_NORMAL
    assert result.resolved_slug == MODE_NORMAL
