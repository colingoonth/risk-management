"""Verify the audit triggers on ``house_shift_preferences`` write history rows
on insert, update, and delete (canonical plan §2.7)."""

from __future__ import annotations

import sqlite3

import pytest

from risk.db.connection import transaction
from risk.repos import event_types as etypes_repo
from risk.repos import house_shift_preferences as prefs_repo
from risk.repos import houses as houses_repo
from risk.repos import shift_types as stypes_repo

pytestmark = pytest.mark.integration


def _seed(db: sqlite3.Connection) -> tuple[int, int, int]:
    with transaction(db):
        houses_repo.insert(db, slug="main", display_name="Main")
    h = houses_repo.get_by_slug(db, "main")
    et = etypes_repo.get_by_slug(db, "mixer")
    st = stypes_repo.get_by_slug(db, "door")
    assert h is not None and et is not None and st is not None
    return h.id, et.id, st.id


def test_insert_logs_history(db: sqlite3.Connection) -> None:
    h_id, et_id, st_id = _seed(db)
    with transaction(db):
        prefs_repo.upsert(
            db, house_id=h_id, event_type_id=et_id, shift_type_id=st_id, min_count=3, target_count=3
        )
    history = prefs_repo.history_for_house(db, h_id)
    assert len(history) == 1
    assert history[0].change_kind == "insert"
    assert history[0].min_count == 3
    assert history[0].target_count == 3


def test_update_logs_history(db: sqlite3.Connection) -> None:
    h_id, et_id, st_id = _seed(db)
    with transaction(db):
        prefs_repo.upsert(
            db, house_id=h_id, event_type_id=et_id, shift_type_id=st_id, min_count=2, target_count=2
        )
    with transaction(db):
        prefs_repo.upsert(
            db, house_id=h_id, event_type_id=et_id, shift_type_id=st_id, min_count=3, target_count=4
        )
    history = prefs_repo.history_for_house(db, h_id)
    kinds = [h.change_kind for h in history]
    assert kinds == ["update", "insert"]  # most-recent first
    assert history[0].min_count == 3
    assert history[0].target_count == 4


def test_delete_logs_history_with_nulls(db: sqlite3.Connection) -> None:
    h_id, et_id, st_id = _seed(db)
    with transaction(db):
        prefs_repo.upsert(
            db, house_id=h_id, event_type_id=et_id, shift_type_id=st_id, min_count=3, target_count=4
        )
    with transaction(db):
        prefs_repo.delete(db, house_id=h_id, event_type_id=et_id, shift_type_id=st_id)
    history = prefs_repo.history_for_house(db, h_id)
    assert history[0].change_kind == "delete"
    assert history[0].min_count is None
    assert history[0].target_count is None


def test_check_target_ge_min(db: sqlite3.Connection) -> None:
    h_id, et_id, st_id = _seed(db)
    with pytest.raises(sqlite3.IntegrityError), transaction(db):
        prefs_repo.upsert(
            db, house_id=h_id, event_type_id=et_id, shift_type_id=st_id, min_count=5, target_count=2
        )
