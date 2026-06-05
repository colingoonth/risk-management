"""Integration tests for Phase 8: revert-prefs + resync-all."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from risk.db.connection import connect, transaction
from risk.db.schema import ensure_schema
from risk.repos import event_shift_requirements as req_repo
from risk.repos import event_types as etypes_repo
from risk.repos import events as events_repo
from risk.repos import house_shift_preferences as prefs_repo
from risk.repos import houses as houses_repo
from risk.repos import semesters as semesters_repo
from risk.repos import shift_types as stypes_repo
from risk.services import shift_requirements as sr_svc

pytestmark = pytest.mark.integration


def _world(tmp_path: Path):
    conn = connect(tmp_path / "p8.db")
    ensure_schema(conn)
    sem = semesters_repo.insert(
        conn, name="SP26", starts_on="2026-01-15", ends_on="2026-05-15"
    )
    with transaction(conn):
        semesters_repo.set_current(conn, "SP26")
    zta = houses_repo.insert(conn, slug="zta", display_name="ZTA")
    return conn, sem, zta


def _last_history_time(conn, house_id: int) -> str:
    row = conn.execute(
        """
        SELECT MAX(changed_at) AS t FROM house_shift_preferences_history
        WHERE house_id = ?
        """,
        (house_id,),
    ).fetchone()
    return str(row["t"])


def test_state_at_returns_snapshot_just_after_insert(tmp_path: Path) -> None:
    conn, _sem, zta = _world(tmp_path)
    et = etypes_repo.get_by_slug(conn, "mixer")
    st = stypes_repo.get_by_slug(conn, "door")
    assert et is not None and st is not None
    with transaction(conn):
        prefs_repo.upsert(
            conn,
            house_id=zta,
            event_type_id=et.id,
            shift_type_id=st.id,
            min_count=2,
            target_count=4,
        )
    t0 = _last_history_time(conn, zta)
    snap = prefs_repo.state_at(conn, house_id=zta, at_timestamp=t0)
    assert snap == [(et.id, st.id, 2, 4)]


def test_revert_restores_prior_values(tmp_path: Path) -> None:
    conn, _sem, zta = _world(tmp_path)
    et = etypes_repo.get_by_slug(conn, "mixer")
    st = stypes_repo.get_by_slug(conn, "door")
    assert et is not None and st is not None
    with transaction(conn):
        prefs_repo.upsert(
            conn,
            house_id=zta,
            event_type_id=et.id,
            shift_type_id=st.id,
            min_count=2,
            target_count=4,
        )
    snapshot_time = _last_history_time(conn, zta)
    time.sleep(1.05)  # datetime('now') has 1-second resolution
    with transaction(conn):
        prefs_repo.upsert(
            conn,
            house_id=zta,
            event_type_id=et.id,
            shift_type_id=st.id,
            min_count=8,
            target_count=10,
        )
    snap = prefs_repo.state_at(conn, house_id=zta, at_timestamp=snapshot_time)
    assert snap == [(et.id, st.id, 2, 4)]


def test_revert_treats_delete_as_absent(tmp_path: Path) -> None:
    conn, _sem, zta = _world(tmp_path)
    et = etypes_repo.get_by_slug(conn, "mixer")
    st = stypes_repo.get_by_slug(conn, "door")
    assert et is not None and st is not None
    with transaction(conn):
        prefs_repo.upsert(
            conn,
            house_id=zta,
            event_type_id=et.id,
            shift_type_id=st.id,
            min_count=2,
            target_count=4,
        )
    time.sleep(1.05)
    with transaction(conn):
        prefs_repo.delete(
            conn, house_id=zta, event_type_id=et.id, shift_type_id=st.id
        )
    post_delete = _last_history_time(conn, zta)
    snap = prefs_repo.state_at(conn, house_id=zta, at_timestamp=post_delete)
    assert snap == [(et.id, st.id, None, None)]


def test_resync_semester_runs_for_each_non_terminal_event(tmp_path: Path) -> None:
    conn, sem, zta = _world(tmp_path)
    et = etypes_repo.get_by_slug(conn, "mixer")
    assert et is not None
    e1 = events_repo.insert(
        conn,
        semester_id=sem,
        event_type_id=et.id,
        display_name="e1",
        date="2026-02-01",
        host_house_id=zta,
    )
    e2 = events_repo.insert(
        conn,
        semester_id=sem,
        event_type_id=et.id,
        display_name="e2",
        date="2026-02-08",
        host_house_id=zta,
    )
    e_done = events_repo.insert(
        conn,
        semester_id=sem,
        event_type_id=et.id,
        display_name="done",
        date="2026-01-20",
        host_house_id=zta,
    )
    sr_svc.snapshot_for_event(conn, e1)
    sr_svc.snapshot_for_event(conn, e2)
    sr_svc.snapshot_for_event(conn, e_done)
    events_repo.update_status(conn, event_id=e_done, status="completed")
    with transaction(conn):
        result = sr_svc.resync_semester(conn, semester_id=sem)
    # Only the two non-terminal events were touched.
    assert set(result.keys()) == {e1, e2}
    # Each got at least one requirement re-written.
    for eid in (e1, e2):
        reqs = req_repo.list_for_event(conn, eid)
        assert len(reqs) > 0
