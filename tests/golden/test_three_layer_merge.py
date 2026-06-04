"""Golden tests for the 3-layer shift-requirement merge.

8 combinations: event_type_default present? × house_preference present? ×
manual_override applied? Each fixture asserts the resulting (min, target,
source_layer) tuple.
"""

from __future__ import annotations

import sqlite3

import pytest

from risk.db.connection import transaction
from risk.repos import event_shift_requirements as req_repo
from risk.repos import event_type_shift_defaults as defaults_repo
from risk.repos import event_types as etypes_repo
from risk.repos import events as events_repo
from risk.repos import house_shift_preferences as prefs_repo
from risk.repos import houses as houses_repo
from risk.repos import semesters as semesters_repo
from risk.repos import shift_types as stypes_repo
from risk.services import shift_requirements as svc

pytestmark = [pytest.mark.golden, pytest.mark.integration]


def _setup_world(
    db: sqlite3.Connection,
    *,
    with_default: bool,
    with_pref: bool,
) -> tuple[int, int, int]:
    """Return (event_id, shift_type_id, house_id). Builds a single mixer event."""
    # Wipe the canonical mixer/driver default the seed migration installed so
    # we can control the "default present? yes/no" axis cleanly.
    with transaction(db):
        mixer = etypes_repo.get_by_slug(db, "mixer")
        driver = stypes_repo.get_by_slug(db, "driver")
        assert mixer is not None and driver is not None
        defaults_repo.delete(db, event_type_id=mixer.id, shift_type_id=driver.id)
        if with_default:
            defaults_repo.upsert(
                db,
                event_type_id=mixer.id,
                shift_type_id=driver.id,
                min_count=2,
                target_count=3,
            )

        houses_repo.insert(db, slug="zta", display_name="ZTA")
        zta = houses_repo.get_by_slug(db, "zta")
        assert zta is not None
        if with_pref:
            prefs_repo.upsert(
                db,
                house_id=zta.id,
                event_type_id=mixer.id,
                shift_type_id=driver.id,
                min_count=4,
                target_count=4,
            )

        sem_id = semesters_repo.insert(
            db, name="SP26", starts_on="2026-01-15", ends_on="2026-05-15"
        )
        event_id = events_repo.insert(
            db,
            semester_id=sem_id,
            event_type_id=mixer.id,
            display_name="ZTA mixer",
            date="2026-02-14",
            host_house_id=zta.id,
        )
        svc.snapshot_for_event(db, event_id)

    return event_id, driver.id, zta.id


def _driver_row(db: sqlite3.Connection, event_id: int) -> tuple[int, int, str] | None:
    rows = req_repo.list_for_event_with_source(db, event_id)
    for r in rows:
        if r.shift_type_slug == "driver":
            return (r.min_count, r.target_count, r.source_layer)
    return None


# ---------- Layer 1: defaults only (no house) ----------


def test_default_only_with_house_but_no_pref(db: sqlite3.Connection) -> None:
    event_id, _st_id, _h_id = _setup_world(db, with_default=True, with_pref=False)
    assert _driver_row(db, event_id) == (2, 3, "event_type_default")


def test_no_default_no_pref(db: sqlite3.Connection) -> None:
    event_id, _, _ = _setup_world(db, with_default=False, with_pref=False)
    assert _driver_row(db, event_id) is None


# ---------- Layer 2: house pref overrides default ----------


def test_pref_overrides_default(db: sqlite3.Connection) -> None:
    event_id, _, _ = _setup_world(db, with_default=True, with_pref=True)
    assert _driver_row(db, event_id) == (4, 4, "house_preference")


def test_pref_present_no_default(db: sqlite3.Connection) -> None:
    event_id, _, _ = _setup_world(db, with_default=False, with_pref=True)
    assert _driver_row(db, event_id) == (4, 4, "house_preference")


# ---------- Layer 3: manual override beats both ----------


def test_manual_override_beats_pref(db: sqlite3.Connection) -> None:
    event_id, st_id, _ = _setup_world(db, with_default=True, with_pref=True)
    with transaction(db):
        svc.apply_manual_override(
            db, event_id=event_id, shift_type_id=st_id, min_count=5, target_count=6
        )
    assert _driver_row(db, event_id) == (5, 6, "manual_override")


def test_manual_override_beats_default_only(db: sqlite3.Connection) -> None:
    event_id, st_id, _ = _setup_world(db, with_default=True, with_pref=False)
    with transaction(db):
        svc.apply_manual_override(
            db, event_id=event_id, shift_type_id=st_id, min_count=5, target_count=6
        )
    assert _driver_row(db, event_id) == (5, 6, "manual_override")


# ---------- Resync semantics ----------


def test_resync_preserves_manual_override(db: sqlite3.Connection) -> None:
    event_id, st_id, _ = _setup_world(db, with_default=True, with_pref=True)
    with transaction(db):
        svc.apply_manual_override(
            db, event_id=event_id, shift_type_id=st_id, min_count=9, target_count=9
        )
    with transaction(db):
        svc.resync_event(db, event_id)
    assert _driver_row(db, event_id) == (9, 9, "manual_override")


def test_resync_refreshes_non_overrides(db: sqlite3.Connection) -> None:
    event_id, st_id, _h_id = _setup_world(db, with_default=True, with_pref=True)
    # Chair edits house pref AFTER event creation; resync should pull new values.
    with transaction(db):
        prefs_repo.upsert(
            db,
            house_id=_h_id,
            event_type_id=events_repo.get_by_id(db, event_id).event_type_id,  # type: ignore[union-attr]
            shift_type_id=st_id,
            min_count=7,
            target_count=8,
        )
    with transaction(db):
        svc.resync_event(db, event_id)
    assert _driver_row(db, event_id) == (7, 8, "resync")
