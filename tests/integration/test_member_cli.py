"""CLI-layer tests for ``risk member`` — show / unset-role / add-alias / set-house + errors."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from risk.db.connection import connect
from risk.db.schema import ensure_schema
from risk.repos import semesters as semesters_repo

pytestmark = pytest.mark.integration


def _run_cli(db_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    risk_bin = Path(sys.executable).parent / "risk"
    return subprocess.run(
        [str(risk_bin), "--db", str(db_path), "--json", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _seed(db_path: Path) -> None:
    """Schema + current SP26 + 1 member + 1 house."""
    conn = connect(db_path)
    ensure_schema(conn)
    sem_id = semesters_repo.insert(
        conn, name="SP26", starts_on="2026-01-15", ends_on="2026-05-15"
    )
    conn.execute("UPDATE semesters SET is_current = 1 WHERE id = ?", (sem_id,))
    conn.close()
    _run_cli(db_path, "config", "house", "add", "zta", "--display-name", "ZTA")
    _run_cli(db_path, "member", "add", "alice", "--display-name", "Alice", "--class-year", "2027")


def test_member_show_happy(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    res = _run_cli(db_path, "member", "show", "alice")
    assert res.returncode == 0, res.stdout + res.stderr
    data = json.loads(res.stdout)["data"]
    assert data["member"]["slug"] == "alice"


def test_member_show_unknown_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    res = _run_cli(db_path, "member", "show", "ghost")
    assert res.returncode != 0
    assert json.loads(res.stdout)["error"]["code"] == "member.not_found"


def test_member_unset_role_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    set_res = _run_cli(db_path, "member", "set-role", "alice", "exec", "--semester", "SP26")
    assert set_res.returncode == 0, set_res.stdout + set_res.stderr

    unset_res = _run_cli(db_path, "member", "unset-role", "alice", "exec", "--semester", "SP26")
    assert unset_res.returncode == 0, unset_res.stdout + unset_res.stderr


def test_member_unset_role_unknown_member_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    res = _run_cli(db_path, "member", "unset-role", "ghost", "exec", "--semester", "SP26")
    assert res.returncode != 0
    assert json.loads(res.stdout)["error"]["code"] == "member.not_found"


def test_member_add_alias_happy_and_resolves(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    res = _run_cli(db_path, "member", "add-alias", "alice", "al")
    assert res.returncode == 0, res.stdout + res.stderr
    # Alias resolves on show.
    show = _run_cli(db_path, "member", "show", "al")
    assert show.returncode == 0
    assert json.loads(show.stdout)["data"]["member"]["slug"] == "alice"


def test_member_add_alias_unknown_member_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    res = _run_cli(db_path, "member", "add-alias", "ghost", "ghosty")
    assert res.returncode != 0
    assert json.loads(res.stdout)["error"]["code"] == "member.not_found"


def test_member_add_alias_duplicate_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    _run_cli(db_path, "member", "add-alias", "alice", "al")
    res = _run_cli(db_path, "member", "add-alias", "alice", "al")
    assert res.returncode != 0
    assert json.loads(res.stdout)["error"]["code"] == "alias.integrity"


def test_member_set_house_happy(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    res = _run_cli(db_path, "member", "set-house", "alice", "zta")
    assert res.returncode == 0, res.stdout + res.stderr


def test_member_set_house_unknown_member_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    res = _run_cli(db_path, "member", "set-house", "ghost", "zta")
    assert res.returncode != 0
    assert json.loads(res.stdout)["error"]["code"] == "member.not_found"


def test_member_set_house_unknown_house_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    res = _run_cli(db_path, "member", "set-house", "alice", "ghost-house")
    assert res.returncode != 0
    assert json.loads(res.stdout)["error"]["code"] == "house.not_found"
