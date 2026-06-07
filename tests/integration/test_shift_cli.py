"""Integration tests for the ``risk shift`` subcommand (filter + show)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from risk.db.connection import connect, transaction
from risk.db.schema import ensure_schema
from risk.repos import event_types as etypes_repo
from risk.repos import events as events_repo
from risk.repos import houses as houses_repo
from risk.repos import member_statuses as statuses_repo
from risk.repos import members as members_repo
from risk.repos import semesters as semesters_repo
from risk.services import assignment, shift_requirements

pytestmark = pytest.mark.integration


def _run_cli(db_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    risk_bin = Path(sys.executable).parent / "risk"
    return subprocess.run(
        [str(risk_bin), "--db", str(db_path), "--json", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _seed(db_path: Path) -> int:
    """Seed a 1-event 6-member SP26 world; return the event id."""
    conn = connect(db_path)
    ensure_schema(conn)
    sem_id = semesters_repo.insert(
        conn, name="SP26", starts_on="2026-01-15", ends_on="2026-05-15"
    )
    conn.execute("UPDATE semesters SET is_current = 1 WHERE id = ?", (sem_id,))
    zta_id = houses_repo.insert(conn, slug="zta", display_name="ZTA")
    et = etypes_repo.get_by_slug(conn, "mixer")
    assert et is not None
    event_id = events_repo.insert(
        conn,
        semester_id=sem_id,
        event_type_id=et.id,
        display_name="ZTA mixer",
        date="2026-02-14",
        host_house_id=zta_id,
    )
    active = statuses_repo.get_by_slug(conn, "active")
    assert active is not None
    for i in range(6):
        members_repo.insert(
            conn,
            slug=f"member-{i}",
            display_name=f"Member {i}",
            status_id=active.id,
            class_year=2027 + (i % 3),
        )
    shift_requirements.snapshot_for_event(conn, event_id)
    with transaction(conn):
        assignment.auto_assign(conn, event_id=event_id, seed=42)
    conn.close()
    return event_id


def test_shift_list_returns_assigned_shifts(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    res = _run_cli(db_path, "shift", "list")
    assert res.returncode == 0, res.stdout + res.stderr
    envelope = json.loads(res.stdout)
    assert envelope["ok"] is True
    shifts = envelope["data"]
    assert len(shifts) >= 1
    assert all(s["assigned_member_slug"] is not None for s in shifts)
    assert all(s["status"] == "assigned" for s in shifts)


def test_shift_list_filter_by_member(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    res = _run_cli(db_path, "shift", "list", "--member", "member-0")
    assert res.returncode == 0, res.stdout + res.stderr
    shifts = json.loads(res.stdout)["data"]
    assert all(s["assigned_member_slug"] == "member-0" for s in shifts)


def test_shift_list_filter_by_event_and_status(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    event_id = _seed(db_path)
    res = _run_cli(
        db_path, "shift", "list", "--event", str(event_id), "--status", "assigned"
    )
    assert res.returncode == 0, res.stdout + res.stderr
    shifts = json.loads(res.stdout)["data"]
    assert len(shifts) >= 1
    assert all(s["event_id"] == event_id and s["status"] == "assigned" for s in shifts)


def test_shift_list_unknown_member_returns_error(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    res = _run_cli(db_path, "shift", "list", "--member", "nobody")
    assert res.returncode != 0
    envelope = json.loads(res.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "member.not_found"


def test_shift_show_returns_single_shift(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    # First find a real shift id via list.
    list_res = _run_cli(db_path, "shift", "list")
    shifts = json.loads(list_res.stdout)["data"]
    shift_id = shifts[0]["id"]

    show_res = _run_cli(db_path, "shift", "show", str(shift_id))
    assert show_res.returncode == 0, show_res.stdout + show_res.stderr
    shift = json.loads(show_res.stdout)["data"]
    assert shift["id"] == shift_id
    assert "shift_type_slug" in shift
    assert "assigned_member_slug" in shift


def test_shift_show_not_found_returns_error(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    res = _run_cli(db_path, "shift", "show", "9999")
    assert res.returncode != 0
    envelope = json.loads(res.stdout)
    assert envelope["error"]["code"] == "shift.not_found"
