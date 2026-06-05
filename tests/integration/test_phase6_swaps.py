"""Integration tests for Phase 6 swap workflow: all three swap shapes."""

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
from risk.repos import pledge_modes as pm_repo
from risk.repos import semesters as semesters_repo
from risk.repos import shift_types as stypes_repo
from risk.repos import shifts as shifts_repo
from risk.repos import swap_requests as sr_repo
from risk.services import swaps

pytestmark = pytest.mark.integration


def _make_event_with_two_assigned_shifts(
    tmp_path: Path,
) -> tuple[Path, int, int, int, int, int]:
    """Returns (db_path, alice_id, bob_id, shift_a_id, shift_b_id, sem_id)."""
    db_path = tmp_path / "swap.db"
    conn = connect(db_path)
    ensure_schema(conn)
    sem_id = semesters_repo.insert(
        conn, name="SP26", starts_on="2026-01-15", ends_on="2026-05-15"
    )
    with transaction(conn):
        semesters_repo.set_current(conn, "SP26")
    active = statuses_repo.get_by_slug(conn, "active")
    assert active is not None
    alice = members_repo.insert(
        conn, slug="alice", display_name="Alice", status_id=active.id
    )
    bob = members_repo.insert(conn, slug="bob", display_name="Bob", status_id=active.id)
    zta = houses_repo.insert(conn, slug="zta", display_name="ZTA")
    et = etypes_repo.get_by_slug(conn, "mixer")
    assert et is not None
    event_id = events_repo.insert(
        conn,
        semester_id=sem_id,
        event_type_id=et.id,
        display_name="Mixer",
        date="2026-02-14",
        host_house_id=zta,
    )
    door = stypes_repo.get_by_slug(conn, "door")
    assert door is not None
    pm = pm_repo.get(conn, "normal")
    assert pm is not None
    # Two slots, both assigned.
    sid_a = shifts_repo.insert_open(
        conn, event_id=event_id, shift_type_id=door.id, slot_index=0
    )
    sid_b = shifts_repo.insert_open(
        conn, event_id=event_id, shift_type_id=door.id, slot_index=1
    )
    with transaction(conn):
        shifts_repo.assign(
            conn,
            shift_id=sid_a,
            member_id=alice,
            effective_pledge_mode_id=pm.id,
            assigned_at="2026-02-14",
        )
        shifts_repo.assign(
            conn,
            shift_id=sid_b,
            member_id=bob,
            effective_pledge_mode_id=pm.id,
            assigned_at="2026-02-14",
        )
    return db_path, alice, bob, sid_a, sid_b, sem_id


def test_trade_swap(tmp_path: Path) -> None:
    db_path, alice, bob, sid_a, sid_b, sem_id = _make_event_with_two_assigned_shifts(
        tmp_path
    )
    conn = connect(db_path)
    ensure_schema(conn)
    with transaction(conn):
        req_id = swaps.request_swap(
            conn,
            semester_id=sem_id,
            from_shift_id=sid_a,
            initiator_member_id=alice,
            to_shift_id=sid_b,
        )
    with transaction(conn):
        result = swaps.accept_swap(conn, request_id=req_id, assigned_at="2026-02-14")
    assert result.shape == "trade"
    a_after = shifts_repo.get_by_id(conn, sid_a)
    b_after = shifts_repo.get_by_id(conn, sid_b)
    assert a_after is not None and b_after is not None
    assert a_after.assigned_member_id == bob
    assert b_after.assigned_member_id == alice
    req_row = sr_repo.get_by_id(conn, req_id)
    assert req_row is not None
    assert req_row.state == "accepted"


def test_reassign_to_open_swap(tmp_path: Path) -> None:
    db_path, alice, _bob, sid_a, _sid_b, sem_id = _make_event_with_two_assigned_shifts(
        tmp_path
    )
    conn = connect(db_path)
    ensure_schema(conn)
    door = stypes_repo.get_by_slug(conn, "door")
    assert door is not None
    fs = shifts_repo.get_by_id(conn, sid_a)
    assert fs is not None
    # Add a third, OPEN slot.
    sid_open = shifts_repo.insert_open(
        conn, event_id=fs.event_id, shift_type_id=door.id, slot_index=2
    )
    with transaction(conn):
        req_id = swaps.request_swap(
            conn,
            semester_id=sem_id,
            from_shift_id=sid_a,
            initiator_member_id=alice,
            to_shift_id=sid_open,
        )
    with transaction(conn):
        result = swaps.accept_swap(conn, request_id=req_id, assigned_at="2026-02-14")
    assert result.shape == "reassign_to_open"
    fs_after = shifts_repo.get_by_id(conn, sid_a)
    ts_after = shifts_repo.get_by_id(conn, sid_open)
    assert fs_after is not None and ts_after is not None
    # from-shift becomes plain open; audit lives in swap_requests.
    assert fs_after.status == "open"
    assert fs_after.assigned_member_id is None
    # to-shift is the new live assignment.
    assert ts_after.status == "assigned"
    assert ts_after.assigned_member_id == alice


def test_counterparty_takeover_swap(tmp_path: Path) -> None:
    db_path, alice, bob, sid_a, _sid_b, sem_id = _make_event_with_two_assigned_shifts(
        tmp_path
    )
    conn = connect(db_path)
    ensure_schema(conn)
    active = statuses_repo.get_by_slug(conn, "active")
    assert active is not None
    carol = members_repo.insert(
        conn, slug="carol", display_name="Carol", status_id=active.id
    )
    with transaction(conn):
        req_id = swaps.request_swap(
            conn,
            semester_id=sem_id,
            from_shift_id=sid_a,
            initiator_member_id=alice,
            counterparty_member_id=carol,
        )
    with transaction(conn):
        result = swaps.accept_swap(conn, request_id=req_id, assigned_at="2026-02-14")
    assert result.shape == "counterparty_takeover"
    a_after = shifts_repo.get_by_id(conn, sid_a)
    assert a_after is not None
    assert a_after.assigned_member_id == carol
    assert a_after.status == "assigned"


def test_reject_and_cancel_block_acceptance(tmp_path: Path) -> None:
    db_path, alice, _bob, sid_a, sid_b, sem_id = _make_event_with_two_assigned_shifts(
        tmp_path
    )
    conn = connect(db_path)
    ensure_schema(conn)
    with transaction(conn):
        req_id = swaps.request_swap(
            conn,
            semester_id=sem_id,
            from_shift_id=sid_a,
            initiator_member_id=alice,
            to_shift_id=sid_b,
        )
    with transaction(conn):
        swaps.reject_swap(conn, request_id=req_id)
    with pytest.raises(ValueError, match="not open"), transaction(conn):
        swaps.accept_swap(conn, request_id=req_id, assigned_at="2026-02-14")


def test_swap_request_validates_initiator_holds_from_shift(tmp_path: Path) -> None:
    db_path, alice, bob, sid_a, sid_b, sem_id = _make_event_with_two_assigned_shifts(
        tmp_path
    )
    conn = connect(db_path)
    ensure_schema(conn)
    # Bob tries to initiate a swap on alice's shift — rejected.
    with pytest.raises(ValueError, match="not assigned"), transaction(conn):
        swaps.request_swap(
            conn,
            semester_id=sem_id,
            from_shift_id=sid_a,
            initiator_member_id=bob,
            to_shift_id=sid_b,
        )
