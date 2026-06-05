"""Integration tests for Phase 4 auto-assign: determinism + CLI end-to-end."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from risk.db.connection import connect, transaction
from risk.db.schema import ensure_schema
from risk.repos import auto_assign_runs as runs_repo
from risk.repos import event_types as etypes_repo
from risk.repos import events as events_repo
from risk.repos import houses as houses_repo
from risk.repos import member_statuses as statuses_repo
from risk.repos import members as members_repo
from risk.repos import semesters as semesters_repo
from risk.repos import shifts as shifts_repo
from risk.services import assignment

pytestmark = pytest.mark.integration


def _build_basic_world(
    db: sqlite3.Connection, *, n_members: int = 6
) -> tuple[int, int, int, list[int]]:
    sem_id = semesters_repo.insert(db, name="SP26", starts_on="2026-01-15", ends_on="2026-05-15")
    zta_id = houses_repo.insert(db, slug="zta", display_name="ZTA")
    et = etypes_repo.get_by_slug(db, "mixer")
    assert et is not None
    event_id = events_repo.insert(
        db,
        semester_id=sem_id,
        event_type_id=et.id,
        display_name="ZTA mixer",
        date="2026-02-14",
        host_house_id=zta_id,
    )
    active = statuses_repo.get_by_slug(db, "active")
    assert active is not None
    member_ids = [
        members_repo.insert(
            db,
            slug=f"member-{i}",
            display_name=f"Member {i}",
            status_id=active.id,
            class_year=2027 + (i % 3),
        )
        for i in range(n_members)
    ]
    from risk.services import shift_requirements as svc

    svc.snapshot_for_event(db, event_id)
    return sem_id, zta_id, event_id, member_ids


def test_auto_assign_deterministic_with_same_seed(db: sqlite3.Connection) -> None:
    sem_id, _, event_id, _ = _build_basic_world(db)
    with transaction(db):
        first = assignment.auto_assign(db, event_id=event_id, seed=12345)
    first_assigns = [(a.shift_type_slug, a.slot_index, a.member_id) for a in first.assignments]

    # Reset shifts to redo.
    with transaction(db):
        db.execute("DELETE FROM shifts WHERE event_id = ?", (event_id,))
        db.execute("DELETE FROM auto_assign_runs WHERE event_id = ?", (event_id,))
        second = assignment.auto_assign(db, event_id=event_id, seed=12345)
    second_assigns = [(a.shift_type_slug, a.slot_index, a.member_id) for a in second.assignments]

    assert first_assigns == second_assigns
    assert first.seed == 12345 == second.seed
    _ = sem_id


def test_auto_assign_records_run_audit(db: sqlite3.Connection) -> None:
    _, _, event_id, _ = _build_basic_world(db)
    with transaction(db):
        result = assignment.auto_assign(db, event_id=event_id, seed=99)
    runs = runs_repo.list_for_event(db, event_id)
    assert len(runs) == 1
    run = runs[0]
    assert run.seed == 99
    assert run.resolved_mode_slug == "normal"
    assert run.eligible_brothers_at_run == 6
    assert run.eligible_pledges_at_run == 0
    # Resolved mode + counts also surfaced on result for the CLI.
    assert result.resolved_mode.resolved_slug == "normal"


def test_auto_assign_dry_run_does_not_write(db: sqlite3.Connection) -> None:
    _, _, event_id, _ = _build_basic_world(db)
    result = assignment.auto_assign(db, event_id=event_id, seed=1, commit=False)
    # No shifts written.
    assert shifts_repo.list_for_event(db, event_id) == []
    # No audit row.
    assert runs_repo.list_for_event(db, event_id) == []
    # Result still has the assignments.
    assert any(a.member_id is not None for a in result.assignments)


def test_auto_assign_strict_raises_when_below_min(db: sqlite3.Connection) -> None:
    sem_id, zta_id, event_id, _ = _build_basic_world(db, n_members=1)
    with pytest.raises(RuntimeError, match="pool short"), transaction(db):
        assignment.auto_assign(db, event_id=event_id, seed=1, strict=True)
    _ = sem_id, zta_id


def test_partial_unique_index_blocks_double_assign(db: sqlite3.Connection) -> None:
    _, _, event_id, member_ids = _build_basic_world(db)
    from risk.repos import pledge_modes as pmodes_repo
    from risk.repos import shift_types as stypes_repo

    door = stypes_repo.get_by_slug(db, "door")
    pm = pmodes_repo.get(db, "normal")
    assert door is not None and pm is not None
    with transaction(db):
        s1 = shifts_repo.insert_open(db, event_id=event_id, shift_type_id=door.id, slot_index=0)
        s2 = shifts_repo.insert_open(db, event_id=event_id, shift_type_id=door.id, slot_index=1)
        shifts_repo.assign(
            db,
            shift_id=s1,
            member_id=member_ids[0],
            effective_pledge_mode_id=pm.id,
            assigned_at="2026-02-14",
        )
    with pytest.raises(sqlite3.IntegrityError), transaction(db):
        shifts_repo.assign(
            db,
            shift_id=s2,
            member_id=member_ids[0],
            effective_pledge_mode_id=pm.id,
            assigned_at="2026-02-14",
        )


def test_host_change_unassigns_now_ineligible_members(db: sqlite3.Connection) -> None:
    """After `event set-host` flips the host, prior assignments to members of
    the new host house are revoked + warning emitted (resync_pending=1).
    """
    from risk.repos import events as events_repo
    from risk.repos import houses as houses_repo
    from risk.repos import member_house_assignments as mha_repo

    sem_id, _, event_id, member_ids = _build_basic_world(db)
    # Pre-assign normally.
    with transaction(db):
        first = assignment.auto_assign(db, event_id=event_id, seed=1)
    assigned_member_ids = {a.member_id for a in first.assignments if a.member_id is not None}
    assert assigned_member_ids, "expected at least one assignment"

    # Move the next event's host to a new house, and put one of the assigned
    # members into that house — they're now host-house and ineligible.
    other_house = houses_repo.insert(db, slug="other", display_name="Other")
    pivot_member = next(iter(assigned_member_ids))
    with transaction(db):
        mha_repo.set_assignment(
            db, member_id=pivot_member, house_id=other_house, semester_id=sem_id
        )
        events_repo.update_host(db, event_id=event_id, host_house_id=other_house)
        # set_host trigger flips resync_pending=1; verify.
        ev = events_repo.get_by_id(db, event_id)
        assert ev is not None and ev.resync_pending

    with transaction(db):
        second = assignment.auto_assign(db, event_id=event_id, seed=2)

    # Pivot member should no longer appear in assignments.
    second_member_ids = {a.member_id for a in second.assignments if a.member_id is not None}
    assert pivot_member not in second_member_ids
    # Warnings include the resync_pending and the unassign note.
    assert any("resync_pending" in w for w in second.warnings)
    assert any("no longer eligible" in w for w in second.warnings)


# ---------- CLI ----------


def _run_cli(db_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    # Use the installed `risk` entry point in the active venv.
    risk_bin = Path(sys.executable).parent / "risk"
    return subprocess.run(
        [str(risk_bin), "--db", str(db_path), "--json", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_auto_assign_json_envelope(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    conn = connect(db_path)
    ensure_schema(conn)
    _, _, _, _ = _build_basic_world(conn)
    # Mark semester current via SQL (skip CLI plumbing for speed).
    conn.execute("UPDATE semesters SET is_current = 1 WHERE name = 'SP26'")
    conn.close()

    res = _run_cli(db_path, "event", "auto-assign", "ZTA mixer", "--seed", "7")
    assert res.returncode == 0, res.stdout + res.stderr
    envelope = json.loads(res.stdout)
    assert envelope["ok"] is True
    data = envelope["data"]
    assert data["seed"] == 7
    assert data["resolved_mode"]["resolved"] == "normal"
    assert any(a["member"] is not None for a in data["assignments"])


def test_cli_auto_assign_unknown_allow_key_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    conn = connect(db_path)
    ensure_schema(conn)
    _build_basic_world(conn)
    conn.execute("UPDATE semesters SET is_current = 1 WHERE name = 'SP26'")
    conn.close()

    res = _run_cli(db_path, "event", "auto-assign", "ZTA mixer", "--allow", "made-up-key")
    assert res.returncode != 0
    envelope = json.loads(res.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "allow.unknown_key"
