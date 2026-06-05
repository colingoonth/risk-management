"""Integration tests for Phase 7 semester archive: validator + bulk disposition."""

from __future__ import annotations

from pathlib import Path

import pytest

from risk.db.connection import connect, transaction
from risk.db.schema import ensure_schema
from risk.repos import event_types as etypes_repo
from risk.repos import events as events_repo
from risk.repos import houses as houses_repo
from risk.repos import member_statuses as statuses_repo
from risk.repos import members as members_repo
from risk.repos import pending_consequences as pc_repo
from risk.repos import semesters as semesters_repo
from risk.repos import strikes as strikes_repo
from risk.repos import swap_requests as sr_repo
from risk.services import semester_archive, strike_state

pytestmark = pytest.mark.integration


def _world(tmp_path: Path):
    conn = connect(tmp_path / "p7.db")
    ensure_schema(conn)
    spring = semesters_repo.insert(
        conn, name="SP26", starts_on="2026-01-15", ends_on="2026-05-15"
    )
    fall = semesters_repo.insert(
        conn, name="FA26", starts_on="2026-08-15", ends_on="2026-12-15"
    )
    with transaction(conn):
        semesters_repo.set_current(conn, "SP26")
    active = statuses_repo.get_by_slug(conn, "active")
    assert active is not None
    alice = members_repo.insert(
        conn, slug="alice", display_name="Alice", status_id=active.id
    )
    return conn, spring, fall, alice


def test_validate_clean_semester(tmp_path: Path) -> None:
    conn, spring, _fall, _alice = _world(tmp_path)
    report = semester_archive.validate(conn, semester_id=spring)
    assert report.is_blocked is False
    assert report.open_strike_count == 0
    assert report.pending_consequence_count == 0


def test_validate_surfaces_open_strikes(tmp_path: Path) -> None:
    conn, spring, _fall, alice = _world(tmp_path)
    with transaction(conn):
        strike_state.issue_strike(
            conn,
            member_id=alice,
            semester_id=spring,
            issued_on="2026-02-10",
            reason="r",
        )
    report = semester_archive.validate(conn, semester_id=spring)
    assert report.open_strike_count == 1
    assert report.is_blocked is True
    assert "open strike" in report.blockers[0]


def test_archive_without_force_refuses_blockers(tmp_path: Path) -> None:
    conn, spring, _fall, alice = _world(tmp_path)
    with transaction(conn):
        strike_state.issue_strike(
            conn,
            member_id=alice,
            semester_id=spring,
            issued_on="2026-02-10",
            reason="r",
        )
    with pytest.raises(ValueError, match="blockers"), transaction(conn):
        semester_archive.archive(
            conn, semester_id=spring, archived_at="2026-05-31"
        )


def test_archive_with_force_closes_strikes(tmp_path: Path) -> None:
    conn, spring, _fall, alice = _world(tmp_path)
    with transaction(conn):
        for i in range(2):
            strike_state.issue_strike(
                conn,
                member_id=alice,
                semester_id=spring,
                issued_on=f"2026-02-{1 + i:02d}",
                reason=f"r{i}",
            )
    with transaction(conn):
        result = semester_archive.archive(
            conn,
            semester_id=spring,
            archived_at="2026-05-31",
            force=True,
        )
    assert result.strikes_closed == 2
    assert result.strikes_carried_forward == 0
    sem_after = semesters_repo.get_by_name(conn, "SP26")
    assert sem_after is not None and sem_after.archived_at == "2026-05-31"
    # Original strikes are closed.
    assert strikes_repo.count_active(conn, member_id=alice, semester_id=spring) == 0


def test_archive_carries_forward_strikes(tmp_path: Path) -> None:
    conn, spring, fall, alice = _world(tmp_path)
    with transaction(conn):
        for i in range(2):
            strike_state.issue_strike(
                conn,
                member_id=alice,
                semester_id=spring,
                issued_on=f"2026-02-{1 + i:02d}",
                reason=f"r{i}",
            )
    with transaction(conn):
        result = semester_archive.archive(
            conn,
            semester_id=spring,
            archived_at="2026-05-31",
            force=True,
            carry_to_semester_id=fall,
        )
    assert result.strikes_carried_forward == 2
    # Strikes in fall semester carry the link.
    fall_strikes = strikes_repo.list_for_member_semester(
        conn, member_id=alice, semester_id=fall
    )
    assert len(fall_strikes) == 2
    for s in fall_strikes:
        assert s.carried_from_strike_id is not None
    # Spring strikes are closed.
    assert strikes_repo.count_active(conn, member_id=alice, semester_id=spring) == 0


def test_archive_carries_pending_consequences(tmp_path: Path) -> None:
    conn, spring, _fall, alice = _world(tmp_path)
    with transaction(conn):
        for i in range(2):
            strike_state.issue_strike(
                conn,
                member_id=alice,
                semester_id=spring,
                issued_on=f"2026-02-{1 + i:02d}",
                reason=f"r{i}",
            )
    # Confirm one pending consequence exists (extra_shift at strike 2).
    pcs = pc_repo.list_for_member_semester(
        conn, member_id=alice, semester_id=spring, state="pending"
    )
    assert len(pcs) == 1
    with transaction(conn):
        semester_archive.archive(
            conn, semester_id=spring, archived_at="2026-05-31", force=True
        )
    pcs_after = pc_repo.list_for_member_semester(
        conn, member_id=alice, semester_id=spring
    )
    assert all(pc.state == "carried_forward" for pc in pcs_after)


def test_archive_refuses_when_future_events_exist(tmp_path: Path) -> None:
    conn, spring, _fall, _alice = _world(tmp_path)
    et = etypes_repo.get_by_slug(conn, "mixer")
    assert et is not None
    zta = houses_repo.insert(conn, slug="zta", display_name="ZTA")
    events_repo.insert(
        conn,
        semester_id=spring,
        event_type_id=et.id,
        display_name="dangling",
        date="2026-04-10",
        host_house_id=zta,
    )
    with pytest.raises(ValueError, match="non-terminal event"), transaction(conn):
        semester_archive.archive(
            conn,
            semester_id=spring,
            archived_at="2026-05-31",
            force=True,
        )


def test_archive_cancels_open_swaps(tmp_path: Path) -> None:
    conn, spring, _fall, alice = _world(tmp_path)
    # Insert a swap request directly so we don't need full shift fixtures.
    et = etypes_repo.get_by_slug(conn, "mixer")
    assert et is not None
    zta = houses_repo.insert(conn, slug="zta", display_name="ZTA")
    eid = events_repo.insert(
        conn,
        semester_id=spring,
        event_type_id=et.id,
        display_name="m",
        date="2026-02-14",
        host_house_id=zta,
    )
    events_repo.update_status(conn, event_id=eid, status="completed")
    # Insert a dummy shift + swap_requests row directly.
    door_id = conn.execute(
        "SELECT id FROM shift_types WHERE slug='door'"
    ).fetchone()["id"]
    pm_id = conn.execute(
        "SELECT id FROM pledge_modes WHERE slug='normal'"
    ).fetchone()["id"]
    conn.execute(
        """INSERT INTO shifts (event_id, shift_type_id, slot_index,
           assigned_member_id, effective_pledge_mode_id, status, assigned_at)
           VALUES (?, ?, 0, ?, ?, 'assigned', '2026-02-14')""",
        (eid, door_id, alice, pm_id),
    )
    sid = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    sr_repo.insert(
        conn,
        semester_id=spring,
        from_shift_id=sid,
        initiator_member_id=alice,
        counterparty_member_id=alice,
    )
    with transaction(conn):
        result = semester_archive.archive(
            conn, semester_id=spring, archived_at="2026-05-31", force=True
        )
    assert result.swaps_cancelled == 1


def test_unarchive_restores(tmp_path: Path) -> None:
    conn, spring, _fall, _alice = _world(tmp_path)
    with transaction(conn):
        semester_archive.archive(
            conn, semester_id=spring, archived_at="2026-05-31"
        )
    with transaction(conn):
        semester_archive.unarchive(conn, semester_id=spring)
    sem = semesters_repo.get_by_name(conn, "SP26")
    assert sem is not None and sem.archived_at is None


def test_archive_blocks_writes_via_trigger(tmp_path: Path) -> None:
    """After archive, the archive-guard triggers must reject new strikes."""
    import sqlite3

    conn, spring, _fall, alice = _world(tmp_path)
    with transaction(conn):
        semester_archive.archive(
            conn, semester_id=spring, archived_at="2026-05-31"
        )
    with pytest.raises(sqlite3.IntegrityError, match="archived"), transaction(conn):
        strike_state.issue_strike(
            conn,
            member_id=alice,
            semester_id=spring,
            issued_on="2026-06-01",
            reason="late",
        )
