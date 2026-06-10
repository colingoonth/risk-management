"""CLI-layer tests for ``risk semester`` — add / set-current / set-house-mode /
resync-all / archive / unarchive + their error paths."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from risk.cli.main import app
from risk.db.connection import connect
from risk.db.schema import ensure_schema

pytestmark = pytest.mark.integration

runner = CliRunner()


def _run(db_path: Path, *args: str) -> dict:
    res = runner.invoke(app, ["--json", "--db", str(db_path), *args])
    return json.loads(res.output), res.exit_code


def _bare_db(db_path: Path) -> None:
    conn = connect(db_path)
    ensure_schema(conn)
    conn.close()


def test_semester_add_happy(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare_db(db_path)
    data, code = _run(
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
    assert code == 0, data
    assert data["data"]["name"] == "SP26"
    assert data["data"]["pledge_takeover_starts_on"] == "2026-03-15"


def test_semester_add_duplicate_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare_db(db_path)
    _run(db_path, "semester", "add", "SP26", "--starts", "2026-01-15", "--ends", "2026-05-15")
    data, code = _run(
        db_path, "semester", "add", "SP26", "--starts", "2026-01-15", "--ends", "2026-05-15"
    )
    assert code != 0
    assert data["error"]["code"] == "semester.integrity"


def test_semester_set_current_flips_flag(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare_db(db_path)
    _run(db_path, "semester", "add", "SP26", "--starts", "2026-01-15", "--ends", "2026-05-15")
    _run(db_path, "semester", "add", "FA26", "--starts", "2026-08-15", "--ends", "2026-12-15")
    _run(db_path, "semester", "set-current", "FA26")
    list_data, list_code = _run(db_path, "semester", "list")
    assert list_code == 0
    rows = list_data["data"]
    current = [r for r in rows if r["is_current"]]
    assert len(current) == 1
    assert current[0]["name"] == "FA26"


def test_semester_set_current_unknown_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare_db(db_path)
    data, code = _run(db_path, "semester", "set-current", "NOPE")
    assert code != 0
    assert data["error"]["code"] == "semester.not_found"


def test_semester_set_house_mode_happy_and_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare_db(db_path)
    _run(db_path, "semester", "add", "SP26", "--starts", "2026-01-15", "--ends", "2026-05-15")
    _run(db_path, "config", "house", "add", "zta", "--display-name", "ZTA")

    happy_data, happy_code = _run(
        db_path,
        "semester",
        "set-house-mode",
        "SP26",
        "--house",
        "zta",
        "--mode",
        "pledge_takeover_full",
    )
    assert happy_code == 0, happy_data

    # Unknown semester
    e1_data, _ = _run(
        db_path,
        "semester",
        "set-house-mode",
        "BAD",
        "--house",
        "zta",
        "--mode",
        "normal",
    )
    assert e1_data["error"]["code"] == "semester.not_found"

    # Unknown house
    e2_data, _ = _run(
        db_path,
        "semester",
        "set-house-mode",
        "SP26",
        "--house",
        "ghost",
        "--mode",
        "normal",
    )
    assert e2_data["error"]["code"] == "house.not_found"

    # Unknown pledge_mode
    e3_data, _ = _run(
        db_path,
        "semester",
        "set-house-mode",
        "SP26",
        "--house",
        "zta",
        "--mode",
        "bogus",
    )
    assert e3_data["error"]["code"] == "pledge_mode.not_found"


def test_semester_resync_all_dry_run_and_apply(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare_db(db_path)
    _run(db_path, "semester", "add", "SP26", "--starts", "2026-01-15", "--ends", "2026-05-15")
    _run(db_path, "semester", "set-current", "SP26")
    _run(db_path, "config", "house", "add", "zta", "--display-name", "ZTA")
    _run(
        db_path, "event", "add", "--name", "ZTA mixer", "--type", "mixer", "--host", "zta",
        "--date", "2026-02-14",
    )

    dry_data, dry_code = _run(db_path, "semester", "resync-all", "SP26", "--dry-run")
    assert dry_code == 0, dry_data
    assert dry_data["data"]["dry_run"] is True
    assert dry_data["data"]["count"] >= 1

    apply_data, apply_code = _run(db_path, "semester", "resync-all", "SP26")
    assert apply_code == 0, apply_data


def test_semester_resync_all_unknown_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare_db(db_path)
    data, code = _run(db_path, "semester", "resync-all", "NOPE")
    assert code != 0
    assert data["error"]["code"] == "semester.not_found"


def test_semester_archive_dry_run_then_force(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare_db(db_path)
    _run(db_path, "semester", "add", "SP26", "--starts", "2026-01-15", "--ends", "2026-05-15")

    dry_data, dry_code = _run(db_path, "semester", "archive", "SP26", "--dry-run")
    assert dry_code == 0, dry_data
    assert "report" in dry_data["data"]

    archive_data, archive_code = _run(db_path, "semester", "archive", "SP26", "--force")
    assert archive_code == 0, archive_data


def test_semester_archive_unknown_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare_db(db_path)
    data, code = _run(db_path, "semester", "archive", "NOPE")
    assert code != 0
    assert data["error"]["code"] == "semester.not_found"


def test_semester_archive_already_archived_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare_db(db_path)
    _run(db_path, "semester", "add", "SP26", "--starts", "2026-01-15", "--ends", "2026-05-15")
    _run(db_path, "semester", "archive", "SP26", "--force")
    data, code = _run(db_path, "semester", "archive", "SP26")
    assert code != 0
    assert data["error"]["code"] == "semester.already_archived"


def test_semester_archive_unknown_carry_to_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare_db(db_path)
    _run(db_path, "semester", "add", "SP26", "--starts", "2026-01-15", "--ends", "2026-05-15")
    data, code = _run(
        db_path, "semester", "archive", "SP26", "--force", "--carry-to", "GHOST"
    )
    assert code != 0
    assert data["error"]["code"] == "semester.not_found"


def test_semester_unarchive_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare_db(db_path)
    _run(db_path, "semester", "add", "SP26", "--starts", "2026-01-15", "--ends", "2026-05-15")
    _run(db_path, "semester", "archive", "SP26", "--force")
    data, code = _run(db_path, "semester", "unarchive", "SP26")
    assert code == 0, data
    list_data, _ = _run(db_path, "semester", "list")
    sp26 = next(r for r in list_data["data"] if r["name"] == "SP26")
    assert sp26["archived_at"] is None


def test_semester_unarchive_unknown_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare_db(db_path)
    data, code = _run(db_path, "semester", "unarchive", "NOPE")
    assert code != 0
    assert data["error"]["code"] == "semester.not_found"
