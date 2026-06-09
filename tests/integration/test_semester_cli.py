"""CLI-layer tests for ``risk semester`` — add / set-current / set-house-mode /
resync-all / archive / unarchive + their error paths."""

from __future__ import annotations

import json
import subprocess
import sys
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


def _bare_db(db_path: Path) -> None:
    conn = connect(db_path)
    ensure_schema(conn)
    conn.close()


def test_semester_add_happy(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare_db(db_path)
    res = _run_cli(
        db_path,
        "semester",
        "add",
        "SP26",
        "--starts",
        "2026-01-15",
        "--ends",
        "2026-05-15",
        "--pledge-takeover",
        "2026-03-15",
    )
    assert res.returncode == 0, res.stdout + res.stderr
    data = json.loads(res.stdout)["data"]
    assert data["name"] == "SP26"
    assert data["pledge_takeover_starts_on"] == "2026-03-15"


def test_semester_add_duplicate_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare_db(db_path)
    _run_cli(db_path, "semester", "add", "SP26", "--starts", "2026-01-15", "--ends", "2026-05-15")
    res = _run_cli(
        db_path, "semester", "add", "SP26", "--starts", "2026-01-15", "--ends", "2026-05-15"
    )
    assert res.returncode != 0
    assert json.loads(res.stdout)["error"]["code"] == "semester.integrity"


def test_semester_set_current_flips_flag(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare_db(db_path)
    _run_cli(db_path, "semester", "add", "SP26", "--starts", "2026-01-15", "--ends", "2026-05-15")
    _run_cli(db_path, "semester", "add", "FA26", "--starts", "2026-08-15", "--ends", "2026-12-15")
    _run_cli(db_path, "semester", "set-current", "FA26")
    list_res = _run_cli(db_path, "semester", "list")
    rows = json.loads(list_res.stdout)["data"]
    current = [r for r in rows if r["is_current"]]
    assert len(current) == 1
    assert current[0]["name"] == "FA26"


def test_semester_set_current_unknown_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare_db(db_path)
    res = _run_cli(db_path, "semester", "set-current", "NOPE")
    assert res.returncode != 0
    assert json.loads(res.stdout)["error"]["code"] == "semester.not_found"


def test_semester_set_house_mode_happy_and_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare_db(db_path)
    _run_cli(db_path, "semester", "add", "SP26", "--starts", "2026-01-15", "--ends", "2026-05-15")
    _run_cli(db_path, "config", "house", "add", "zta", "--display-name", "ZTA")

    happy = _run_cli(
        db_path,
        "semester",
        "set-house-mode",
        "SP26",
        "--house",
        "zta",
        "--mode",
        "pledge_takeover_full",
    )
    assert happy.returncode == 0, happy.stdout + happy.stderr

    # Unknown semester
    e1 = _run_cli(
        db_path,
        "semester",
        "set-house-mode",
        "BAD",
        "--house",
        "zta",
        "--mode",
        "normal",
    )
    assert json.loads(e1.stdout)["error"]["code"] == "semester.not_found"

    # Unknown house
    e2 = _run_cli(
        db_path,
        "semester",
        "set-house-mode",
        "SP26",
        "--house",
        "ghost",
        "--mode",
        "normal",
    )
    assert json.loads(e2.stdout)["error"]["code"] == "house.not_found"

    # Unknown pledge_mode
    e3 = _run_cli(
        db_path,
        "semester",
        "set-house-mode",
        "SP26",
        "--house",
        "zta",
        "--mode",
        "bogus",
    )
    assert json.loads(e3.stdout)["error"]["code"] == "pledge_mode.not_found"


def test_semester_resync_all_dry_run_and_apply(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare_db(db_path)
    _run_cli(db_path, "semester", "add", "SP26", "--starts", "2026-01-15", "--ends", "2026-05-15")
    _run_cli(db_path, "semester", "set-current", "SP26")
    _run_cli(db_path, "config", "house", "add", "zta", "--display-name", "ZTA")
    _run_cli(
        db_path, "event", "add", "--name", "ZTA mixer", "--type", "mixer", "--host", "zta",
        "--date", "2026-02-14",
    )

    dry = _run_cli(db_path, "semester", "resync-all", "SP26", "--dry-run")
    assert dry.returncode == 0, dry.stdout + dry.stderr
    data = json.loads(dry.stdout)["data"]
    assert data["dry_run"] is True
    assert data["count"] >= 1

    apply_res = _run_cli(db_path, "semester", "resync-all", "SP26")
    assert apply_res.returncode == 0, apply_res.stdout + apply_res.stderr


def test_semester_resync_all_unknown_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare_db(db_path)
    res = _run_cli(db_path, "semester", "resync-all", "NOPE")
    assert res.returncode != 0
    assert json.loads(res.stdout)["error"]["code"] == "semester.not_found"


def test_semester_archive_dry_run_then_force(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare_db(db_path)
    _run_cli(db_path, "semester", "add", "SP26", "--starts", "2026-01-15", "--ends", "2026-05-15")

    dry = _run_cli(db_path, "semester", "archive", "SP26", "--dry-run")
    assert dry.returncode == 0, dry.stdout + dry.stderr
    assert "report" in json.loads(dry.stdout)["data"]

    archive = _run_cli(db_path, "semester", "archive", "SP26", "--force")
    assert archive.returncode == 0, archive.stdout + archive.stderr


def test_semester_archive_unknown_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare_db(db_path)
    res = _run_cli(db_path, "semester", "archive", "NOPE")
    assert res.returncode != 0
    assert json.loads(res.stdout)["error"]["code"] == "semester.not_found"


def test_semester_archive_already_archived_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare_db(db_path)
    _run_cli(db_path, "semester", "add", "SP26", "--starts", "2026-01-15", "--ends", "2026-05-15")
    _run_cli(db_path, "semester", "archive", "SP26", "--force")
    res = _run_cli(db_path, "semester", "archive", "SP26")
    assert res.returncode != 0
    assert json.loads(res.stdout)["error"]["code"] == "semester.already_archived"


def test_semester_archive_unknown_carry_to_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare_db(db_path)
    _run_cli(db_path, "semester", "add", "SP26", "--starts", "2026-01-15", "--ends", "2026-05-15")
    res = _run_cli(
        db_path, "semester", "archive", "SP26", "--force", "--carry-to", "GHOST"
    )
    assert res.returncode != 0
    assert json.loads(res.stdout)["error"]["code"] == "semester.not_found"


def test_semester_unarchive_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare_db(db_path)
    _run_cli(db_path, "semester", "add", "SP26", "--starts", "2026-01-15", "--ends", "2026-05-15")
    _run_cli(db_path, "semester", "archive", "SP26", "--force")
    res = _run_cli(db_path, "semester", "unarchive", "SP26")
    assert res.returncode == 0, res.stdout + res.stderr
    list_res = _run_cli(db_path, "semester", "list")
    sp26 = next(r for r in json.loads(list_res.stdout)["data"] if r["name"] == "SP26")
    assert sp26["archived_at"] is None


def test_semester_unarchive_unknown_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare_db(db_path)
    res = _run_cli(db_path, "semester", "unarchive", "NOPE")
    assert res.returncode != 0
    assert json.loads(res.stdout)["error"]["code"] == "semester.not_found"
