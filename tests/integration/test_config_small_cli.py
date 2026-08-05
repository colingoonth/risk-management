"""CLI-layer tests for the small ``risk config role`` and ``risk config shift-type``
write paths (add/rename + integrity error paths)."""

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


def _bare(db_path: Path) -> None:
    conn = connect(db_path)
    ensure_schema(conn)
    conn.close()


def test_role_add_happy(tmp_path: Path) -> None:
    # The slug must be one the migrations do NOT seed, or this tests nothing but
    # the duplicate path (which is test_role_add_duplicate_errors' job, and it
    # uses a seeded slug on purpose). 'social_chair' used to be safe here and
    # stopped being so the moment 0013 seeded it for real.
    db_path = tmp_path / "r.db"
    _bare(db_path)
    res = _run_cli(
        db_path, "config", "role", "add", "test_only_role",
        "--display-name", "Test Only Role",
        "--automation-key", "test-only-role",
        "--exclude-soft",
        "--default-excluded",
    )
    assert res.returncode == 0, res.stdout + res.stderr


def test_role_add_duplicate_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare(db_path)
    res = _run_cli(
        db_path, "config", "role", "add", "exec", "--display-name", "Second Exec"
    )
    assert res.returncode != 0
    assert json.loads(res.stdout)["error"]["code"] == "role.integrity"


def test_role_rename_happy(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare(db_path)
    res = _run_cli(
        db_path, "config", "role", "rename", "exec", "--display-name", "Executive"
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert json.loads(res.stdout)["data"]["display_name"] == "Executive"


def test_role_rename_unknown_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare(db_path)
    res = _run_cli(
        db_path, "config", "role", "rename", "no_such_role", "--display-name", "X"
    )
    assert res.returncode != 0
    assert json.loads(res.stdout)["error"]["code"] == "role.not_found"


def test_shift_type_add_happy(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare(db_path)
    res = _run_cli(
        db_path, "config", "shift-type", "add", "patio", "--display-name", "Patio watch"
    )
    assert res.returncode == 0, res.stdout + res.stderr


def test_shift_type_add_duplicate_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _bare(db_path)
    res = _run_cli(
        db_path, "config", "shift-type", "add", "door", "--display-name", "Door"
    )
    assert res.returncode != 0
    assert json.loads(res.stdout)["error"]["code"] == "shift_type.integrity"
