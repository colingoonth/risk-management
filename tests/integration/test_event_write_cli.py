"""CLI-layer tests for ``risk event`` write paths — set-shift-req / clear-shift-req /
resync-shift-reqs / set-host / cancel / auto-assign --dry-run + error paths."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from risk.cli.main import app
from risk.db.connection import connect
from risk.db.schema import ensure_schema
from risk.repos import member_statuses as statuses_repo
from risk.repos import members as members_repo
from risk.repos import semesters as semesters_repo

pytestmark = pytest.mark.integration

runner = CliRunner()


def _run(db_path: Path, *args: str) -> tuple[dict, int]:
    res = runner.invoke(app, ["--json", "--db", str(db_path), *args])
    return json.loads(res.output), res.exit_code


def _seed(db_path: Path) -> int:
    """Schema + SP26 current + zta + a few members + one mixer event."""
    conn = connect(db_path)
    ensure_schema(conn)
    sem_id = semesters_repo.insert(conn, name="SP26", starts_on="2026-01-15", ends_on="2026-05-15")
    conn.execute("UPDATE semesters SET is_current = 1 WHERE id = ?", (sem_id,))
    active = statuses_repo.get_by_slug(conn, "active")
    assert active is not None
    for i in range(8):
        members_repo.insert(
            conn,
            slug=f"member-{i}",
            display_name=f"Member {i}",
            status_id=active.id,
            class_year=2027,
        )
    conn.close()
    _run(db_path, "config", "house", "add", "zta", "--display-name", "ZTA")
    _run(db_path, "config", "house", "add", "kd", "--display-name", "Kappa Delta")
    add_data, add_code = _run(
        db_path,
        "event",
        "add",
        "--name",
        "ZTA mixer",
        "--type",
        "mixer",
        "--host",
        "zta",
        "--date",
        "2026-02-14",
    )
    assert add_code == 0, add_data
    return 1


def test_event_set_shift_req_happy(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    data, code = _run(
        db_path,
        "event",
        "set-shift-req",
        "1",
        "door",
        "--min",
        "3",
        "--target",
        "4",
    )
    assert code == 0, data


def test_event_set_shift_req_unknown_event_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    data, code = _run(
        db_path,
        "event",
        "set-shift-req",
        "9999",
        "door",
        "--min",
        "1",
        "--target",
        "1",
    )
    assert code != 0
    assert data["error"]["code"] == "event.not_found"


def test_event_set_shift_req_unknown_shift_type_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    data, code = _run(
        db_path,
        "event",
        "set-shift-req",
        "1",
        "bogus",
        "--min",
        "1",
        "--target",
        "1",
    )
    assert code != 0
    assert data["error"]["code"] == "shift_type.not_found"


def test_event_set_shift_req_target_zero_requires_yes_in_json_mode(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    data, code = _run(
        db_path,
        "event",
        "set-shift-req",
        "1",
        "door",
        "--min",
        "0",
        "--target",
        "0",
    )
    assert code != 0
    assert data["error"]["code"] == "confirm_required"

    yes_data, yes_code = _run(
        db_path,
        "event",
        "set-shift-req",
        "1",
        "door",
        "--min",
        "0",
        "--target",
        "0",
        "--yes",
    )
    assert yes_code == 0, yes_data


def test_event_clear_shift_req_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    _run(db_path, "event", "set-shift-req", "1", "door", "--min", "3", "--target", "4")
    data, code = _run(db_path, "event", "clear-shift-req", "1", "door")
    assert code == 0, data


def test_event_clear_shift_req_unknown_event_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    data, code = _run(db_path, "event", "clear-shift-req", "9999", "door")
    assert code != 0
    assert data["error"]["code"] == "event.not_found"


def test_event_clear_shift_req_unknown_shift_type_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    data, code = _run(db_path, "event", "clear-shift-req", "1", "bogus")
    assert code != 0
    assert data["error"]["code"] == "shift_type.not_found"


def test_event_resync_shift_reqs_happy(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    data, code = _run(db_path, "event", "resync-shift-reqs", "1")
    assert code == 0, data


def test_event_resync_shift_reqs_unknown_event_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    data, code = _run(db_path, "event", "resync-shift-reqs", "9999")
    assert code != 0
    assert data["error"]["code"] == "event.not_found"


def test_event_set_host_happy(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    data, code = _run(db_path, "event", "set-host", "1", "--house", "kd")
    assert code == 0, data


def test_event_set_host_unknown_event_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    data, code = _run(db_path, "event", "set-host", "9999", "--house", "kd")
    assert code != 0
    assert data["error"]["code"] == "event.not_found"


def test_event_set_host_unknown_house_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    data, code = _run(db_path, "event", "set-host", "1", "--house", "ghost")
    assert code != 0
    assert data["error"]["code"] == "house.not_found"


def test_event_cancel_happy(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    data, code = _run(db_path, "event", "cancel", "1", "--yes")
    assert code == 0, data


def test_event_cancel_unknown_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    data, code = _run(db_path, "event", "cancel", "9999", "--yes")
    assert code != 0
    assert data["error"]["code"] == "event.not_found"


def test_event_auto_assign_dry_run(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    data, code = _run(db_path, "event", "auto-assign", "1", "--dry-run", "--seed", "7")
    assert code == 0, data
    assert data["data"]["dry_run"] is True
