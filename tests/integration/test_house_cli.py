"""CLI-layer tests for ``risk config house`` — set-pref / clear-pref / list-prefs /
revert-prefs + error paths."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from risk.cli.main import app
from risk.db.connection import connect
from risk.db.schema import ensure_schema

pytestmark = pytest.mark.integration

runner = CliRunner()


def _run(db_path: Path, *args: str) -> tuple[dict, int]:
    res = runner.invoke(app, ["--json", "--db", str(db_path), *args])
    return json.loads(res.output), res.exit_code


def _bare(db_path: Path) -> None:
    conn = connect(db_path)
    ensure_schema(conn)
    conn.close()


def test_house_set_pref_happy(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare(db_path)
    _run(db_path, "config", "house", "add", "zta", "--display-name", "ZTA")
    data, code = _run(
        db_path,
        "config",
        "house",
        "set-pref",
        "zta",
        "mixer",
        "door",
        "--min",
        "2",
        "--target",
        "3",
    )
    assert code == 0, data

    list_data, _ = _run(db_path, "config", "house", "list-prefs", "zta")
    rows = list_data["data"]
    assert any(r["event_type_slug"] == "mixer" and r["shift_type_slug"] == "door" for r in rows)


def test_house_set_pref_unknown_house_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare(db_path)
    data, code = _run(
        db_path,
        "config",
        "house",
        "set-pref",
        "ghost",
        "mixer",
        "door",
        "--min",
        "2",
        "--target",
        "3",
    )
    assert code != 0
    assert data["error"]["code"] == "house.not_found"


def test_house_set_pref_unknown_event_type_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare(db_path)
    _run(db_path, "config", "house", "add", "zta", "--display-name", "ZTA")
    data, code = _run(
        db_path,
        "config",
        "house",
        "set-pref",
        "zta",
        "bogus",
        "door",
        "--min",
        "1",
        "--target",
        "1",
    )
    assert code != 0
    assert data["error"]["code"] == "event_type.not_found"


def test_house_set_pref_unknown_shift_type_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare(db_path)
    _run(db_path, "config", "house", "add", "zta", "--display-name", "ZTA")
    data, code = _run(
        db_path,
        "config",
        "house",
        "set-pref",
        "zta",
        "mixer",
        "bogus",
        "--min",
        "1",
        "--target",
        "1",
    )
    assert code != 0
    assert data["error"]["code"] == "shift_type.not_found"


def test_house_clear_pref_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare(db_path)
    _run(db_path, "config", "house", "add", "zta", "--display-name", "ZTA")
    _run(
        db_path,
        "config",
        "house",
        "set-pref",
        "zta",
        "mixer",
        "door",
        "--min",
        "2",
        "--target",
        "3",
    )
    clear_data, clear_code = _run(db_path, "config", "house", "clear-pref", "zta", "mixer", "door")
    assert clear_code == 0, clear_data
    list_data, _ = _run(db_path, "config", "house", "list-prefs", "zta")
    rows = list_data["data"]
    assert not any(r["event_type_slug"] == "mixer" and r["shift_type_slug"] == "door" for r in rows)


def test_house_clear_pref_unknown_house_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare(db_path)
    data, code = _run(db_path, "config", "house", "clear-pref", "ghost", "mixer", "door")
    assert code != 0
    assert data["error"]["code"] == "house.not_found"


def test_house_list_prefs_unknown_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare(db_path)
    data, code = _run(db_path, "config", "house", "list-prefs", "ghost")
    assert code != 0
    assert data["error"]["code"] == "house.not_found"


def test_house_revert_prefs_dry_run_then_apply(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare(db_path)
    _run(db_path, "config", "house", "add", "zta", "--display-name", "ZTA")
    _run(
        db_path,
        "config",
        "house",
        "set-pref",
        "zta",
        "mixer",
        "door",
        "--min",
        "2",
        "--target",
        "3",
    )
    time.sleep(1.1)  # Ensure timestamp difference for history snapshot.
    checkpoint_data, _ = _run(db_path, "config", "house", "pref-history", "zta")
    history = checkpoint_data["data"]
    assert len(history) >= 1
    # Capture a timestamp from the history to revert to.
    revert_to = history[0]["changed_at"]
    # Mutate the pref after the checkpoint.
    _run(
        db_path,
        "config",
        "house",
        "set-pref",
        "zta",
        "mixer",
        "door",
        "--min",
        "1",
        "--target",
        "1",
    )
    # Dry-run revert.
    dry_data, dry_code = _run(
        db_path, "config", "house", "revert-prefs", "zta", "--to", revert_to, "--dry-run"
    )
    assert dry_code == 0, dry_data
    assert dry_data["data"]["dry_run"] is True

    # Apply revert.
    apply_data, apply_code = _run(
        db_path, "config", "house", "revert-prefs", "zta", "--to", revert_to
    )
    assert apply_code == 0, apply_data


def test_house_revert_prefs_unknown_house_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare(db_path)
    data, code = _run(
        db_path, "config", "house", "revert-prefs", "ghost", "--to", "2026-01-01 00:00:00"
    )
    assert code != 0
    assert data["error"]["code"] == "house.not_found"
