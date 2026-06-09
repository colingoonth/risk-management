"""CLI-layer tests for ``risk config house`` — set-pref / clear-pref / list-prefs /
revert-prefs + error paths."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from risk.db.connection import connect
from risk.db.schema import ensure_schema

pytestmark = pytest.mark.integration


def _run_cli(db_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    risk_bin = Path(sys.executable).parent / "risk"
    return subprocess.run(
        [str(risk_bin), "--db", str(db_path), "--json", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _bare(db_path: Path) -> None:
    conn = connect(db_path)
    ensure_schema(conn)
    conn.close()


def test_house_set_pref_happy(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare(db_path)
    _run_cli(db_path, "config", "house", "add", "zta", "--display-name", "ZTA")
    res = _run_cli(
        db_path, "config", "house", "set-pref", "zta", "mixer", "door",
        "--min", "2", "--target", "3",
    )
    assert res.returncode == 0, res.stdout + res.stderr

    list_res = _run_cli(db_path, "config", "house", "list-prefs", "zta")
    rows = json.loads(list_res.stdout)["data"]
    assert any(
        r["event_type_slug"] == "mixer" and r["shift_type_slug"] == "door" for r in rows
    )


def test_house_set_pref_unknown_house_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare(db_path)
    res = _run_cli(
        db_path, "config", "house", "set-pref", "ghost", "mixer", "door",
        "--min", "2", "--target", "3",
    )
    assert res.returncode != 0
    assert json.loads(res.stdout)["error"]["code"] == "house.not_found"


def test_house_set_pref_unknown_event_type_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare(db_path)
    _run_cli(db_path, "config", "house", "add", "zta", "--display-name", "ZTA")
    res = _run_cli(
        db_path, "config", "house", "set-pref", "zta", "bogus", "door",
        "--min", "1", "--target", "1",
    )
    assert res.returncode != 0
    assert json.loads(res.stdout)["error"]["code"] == "event_type.not_found"


def test_house_set_pref_unknown_shift_type_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare(db_path)
    _run_cli(db_path, "config", "house", "add", "zta", "--display-name", "ZTA")
    res = _run_cli(
        db_path, "config", "house", "set-pref", "zta", "mixer", "bogus",
        "--min", "1", "--target", "1",
    )
    assert res.returncode != 0
    assert json.loads(res.stdout)["error"]["code"] == "shift_type.not_found"


def test_house_clear_pref_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare(db_path)
    _run_cli(db_path, "config", "house", "add", "zta", "--display-name", "ZTA")
    _run_cli(
        db_path, "config", "house", "set-pref", "zta", "mixer", "door",
        "--min", "2", "--target", "3",
    )
    clear_res = _run_cli(db_path, "config", "house", "clear-pref", "zta", "mixer", "door")
    assert clear_res.returncode == 0, clear_res.stdout + clear_res.stderr
    list_res = _run_cli(db_path, "config", "house", "list-prefs", "zta")
    rows = json.loads(list_res.stdout)["data"]
    assert not any(
        r["event_type_slug"] == "mixer" and r["shift_type_slug"] == "door" for r in rows
    )


def test_house_clear_pref_unknown_house_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare(db_path)
    res = _run_cli(db_path, "config", "house", "clear-pref", "ghost", "mixer", "door")
    assert res.returncode != 0
    assert json.loads(res.stdout)["error"]["code"] == "house.not_found"


def test_house_list_prefs_unknown_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare(db_path)
    res = _run_cli(db_path, "config", "house", "list-prefs", "ghost")
    assert res.returncode != 0
    assert json.loads(res.stdout)["error"]["code"] == "house.not_found"


def test_house_revert_prefs_dry_run_then_apply(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare(db_path)
    _run_cli(db_path, "config", "house", "add", "zta", "--display-name", "ZTA")
    _run_cli(
        db_path, "config", "house", "set-pref", "zta", "mixer", "door",
        "--min", "2", "--target", "3",
    )
    time.sleep(1.1)  # Ensure timestamp difference for history snapshot.
    checkpoint = _run_cli(db_path, "config", "house", "pref-history", "zta")
    history = json.loads(checkpoint.stdout)["data"]
    assert len(history) >= 1
    # Capture a timestamp from the history to revert to.
    revert_to = history[0]["changed_at"]
    # Mutate the pref after the checkpoint.
    _run_cli(
        db_path, "config", "house", "set-pref", "zta", "mixer", "door",
        "--min", "1", "--target", "1",
    )
    # Dry-run revert.
    dry = _run_cli(
        db_path, "config", "house", "revert-prefs", "zta", "--to", revert_to, "--dry-run"
    )
    assert dry.returncode == 0, dry.stdout + dry.stderr
    assert json.loads(dry.stdout)["data"]["dry_run"] is True

    # Apply revert.
    apply_res = _run_cli(db_path, "config", "house", "revert-prefs", "zta", "--to", revert_to)
    assert apply_res.returncode == 0, apply_res.stdout + apply_res.stderr


def test_house_revert_prefs_unknown_house_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare(db_path)
    res = _run_cli(db_path, "config", "house", "revert-prefs", "ghost", "--to", "2026-01-01 00:00:00")
    assert res.returncode != 0
    assert json.loads(res.stdout)["error"]["code"] == "house.not_found"
