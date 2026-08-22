"""CLI-layer tests for ``risk swap`` — request shapes A/B/C + accept/reject/cancel/list."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from risk.cli.main import app
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

runner = CliRunner()


def _run(db_path: Path, *args: str) -> tuple[dict, int]:
    res = runner.invoke(app, ["--json", "--db", str(db_path), *args])
    return json.loads(res.output), res.exit_code


def _seed(db_path: Path) -> int:
    """Seed SP26 + 1 mixer + 15 members + auto-assign. Return event id."""
    conn = connect(db_path)
    ensure_schema(conn)
    sem_id = semesters_repo.insert(conn, name="SP26", starts_on="2026-01-15", ends_on="2026-05-15")
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
    for i in range(15):
        members_repo.insert(
            conn,
            slug=f"member-{i}",
            display_name=f"Member {i}",
            status_id=active.id,
            class_year=2027,
        )
    shift_requirements.snapshot_for_event(conn, event_id)
    with transaction(conn):
        assignment.auto_assign(conn, event_id=event_id, seed=42)
    conn.close()
    return event_id


def _assigned_shifts(db_path: Path) -> list[dict]:
    data, _ = _run(db_path, "shift", "list", "--status", "assigned")
    return list(data["data"])


def test_swap_request_shape_a_trade(tmp_path: Path) -> None:
    """Shape A: two assigned shifts, each with a counterparty member."""
    db_path = tmp_path / "r.db"
    _seed(db_path)
    shifts = _assigned_shifts(db_path)
    # Pick two different shifts assigned to different members.
    s1, s2 = shifts[0], shifts[1]
    assert s1["assigned_member_slug"] != s2["assigned_member_slug"]

    data, code = _run(
        db_path, "swap", "request", "--from-shift", str(s1["id"]), "--to-shift", str(s2["id"])
    )
    assert code == 0, data
    assert data["ok"] is True
    assert data["data"]["swap_request"]["state"] == "open"


def test_swap_request_invalid_from_shift_open_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    # Create an unassigned shift via SQL.
    conn = connect(db_path)
    conn.execute(
        "INSERT INTO shifts (event_id, shift_type_id, slot_index, status) VALUES (?, ?, ?, 'open')",
        (1, 1, 49),
    )
    open_id = conn.execute("SELECT id FROM shifts WHERE slot_index = 49").fetchone()[0]
    conn.close()

    data, code = _run(db_path, "swap", "request", "--from-shift", str(open_id))
    assert code != 0
    assert data["error"]["code"] == "swap.from_shift_open"


def test_swap_request_from_shift_not_found(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    data, code = _run(db_path, "swap", "request", "--from-shift", "9999")
    assert code != 0
    assert data["error"]["code"] == "swap.from_shift_not_found"


def test_swap_request_unknown_counterparty_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    shifts = _assigned_shifts(db_path)
    data, code = _run(
        db_path,
        "swap",
        "request",
        "--from-shift",
        str(shifts[0]["id"]),
        "--counterparty",
        "ghost",
    )
    assert code != 0
    assert data["error"]["code"] == "member.not_found"


def test_swap_accept_resolves_swap(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    shifts = _assigned_shifts(db_path)
    s1, s2 = shifts[0], shifts[1]
    req_data, req_code = _run(
        db_path, "swap", "request", "--from-shift", str(s1["id"]), "--to-shift", str(s2["id"])
    )
    assert req_code == 0, req_data
    req_id = req_data["data"]["swap_request"]["id"]

    accept_data, accept_code = _run(db_path, "swap", "accept", str(req_id))
    assert accept_code == 0, accept_data
    assert accept_data["ok"] is True


def test_swap_accept_unknown_id_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    data, code = _run(db_path, "swap", "accept", "9999")
    assert code != 0
    assert data["error"]["code"] == "swap.not_found"


def test_swap_reject_marks_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    shifts = _assigned_shifts(db_path)
    req_data, req_code = _run(
        db_path,
        "swap",
        "request",
        "--from-shift",
        str(shifts[0]["id"]),
        "--to-shift",
        str(shifts[1]["id"]),
    )
    assert req_code == 0, req_data
    req_id = req_data["data"]["swap_request"]["id"]
    data, code = _run(db_path, "swap", "reject", str(req_id))
    assert code == 0, data
    assert data["data"]["state"] == "rejected"


def test_swap_reject_unknown_id_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    data, code = _run(db_path, "swap", "reject", "9999")
    assert code != 0
    assert data["error"]["code"] == "swap.reject_invalid"


def test_swap_cancel_marks_cancelled(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    shifts = _assigned_shifts(db_path)
    req_data, req_code = _run(
        db_path,
        "swap",
        "request",
        "--from-shift",
        str(shifts[0]["id"]),
        "--to-shift",
        str(shifts[1]["id"]),
    )
    assert req_code == 0, req_data
    req_id = req_data["data"]["swap_request"]["id"]
    data, code = _run(db_path, "swap", "cancel", str(req_id), "--yes")
    assert code == 0, data
    assert data["data"]["state"] == "cancelled"


def test_swap_cancel_unknown_id_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    data, code = _run(db_path, "swap", "cancel", "9999", "--yes")
    assert code != 0
    assert data["error"]["code"] == "swap.cancel_invalid"


def test_swap_list_filters_by_state(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    shifts = _assigned_shifts(db_path)
    _run(
        db_path,
        "swap",
        "request",
        "--from-shift",
        str(shifts[0]["id"]),
        "--to-shift",
        str(shifts[1]["id"]),
    )
    data, code = _run(db_path, "swap", "list", "--state", "open")
    assert code == 0, data
    rows = data["data"]["swap_requests"]
    assert len(rows) >= 1
    assert all(r["state"] == "open" for r in rows)
