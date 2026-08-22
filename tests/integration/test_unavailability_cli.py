"""CLI-layer tests for ``risk unavailability`` — add / list / remove + error paths."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from risk.db.connection import connect
from risk.db.schema import ensure_schema
from risk.repos import member_statuses as statuses_repo
from risk.repos import members as members_repo
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
    conn = connect(db_path)
    ensure_schema(conn)
    sem_id = semesters_repo.insert(conn, name="SP26", starts_on="2026-01-15", ends_on="2026-05-15")
    conn.execute("UPDATE semesters SET is_current = 1 WHERE id = ?", (sem_id,))
    active = statuses_repo.get_by_slug(conn, "active")
    assert active is not None
    members_repo.insert(
        conn, slug="alice", display_name="Alice", status_id=active.id, class_year=2027
    )
    members_repo.insert(conn, slug="bob", display_name="Bob", status_id=active.id, class_year=2027)
    conn.close()


def test_unavailability_add_happy(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    res = _run_cli(
        db_path,
        "unavailability",
        "add",
        "alice",
        "--starts",
        "2026-03-01",
        "--ends",
        "2026-03-08",
        "--reason",
        "spring break",
    )
    assert res.returncode == 0, res.stdout + res.stderr
    data = json.loads(res.stdout)["data"]["unavailability"]
    assert data["member_slug"] == "alice"
    assert data["starts_on"] == "2026-03-01"
    assert data["ends_on"] == "2026-03-08"


def test_unavailability_add_inverted_dates_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    res = _run_cli(
        db_path,
        "unavailability",
        "add",
        "alice",
        "--starts",
        "2026-03-08",
        "--ends",
        "2026-03-01",
    )
    assert res.returncode != 0
    err = json.loads(res.stdout)["error"]
    assert err["code"].startswith("unavailability.")


def test_unavailability_add_unknown_member_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    res = _run_cli(
        db_path,
        "unavailability",
        "add",
        "ghost",
        "--starts",
        "2026-03-01",
        "--ends",
        "2026-03-08",
    )
    assert res.returncode != 0
    assert json.loads(res.stdout)["error"]["code"] == "member.not_found"


def test_unavailability_list_returns_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    _run_cli(
        db_path,
        "unavailability",
        "add",
        "alice",
        "--starts",
        "2026-03-01",
        "--ends",
        "2026-03-08",
    )
    _run_cli(
        db_path,
        "unavailability",
        "add",
        "bob",
        "--starts",
        "2026-04-01",
        "--ends",
        "2026-04-05",
    )
    res = _run_cli(db_path, "unavailability", "list")
    assert res.returncode == 0, res.stdout + res.stderr
    rows = json.loads(res.stdout)["data"]["unavailability"]
    assert len(rows) == 2


def test_unavailability_list_filter_by_member(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    _run_cli(
        db_path,
        "unavailability",
        "add",
        "alice",
        "--starts",
        "2026-03-01",
        "--ends",
        "2026-03-08",
    )
    _run_cli(
        db_path,
        "unavailability",
        "add",
        "bob",
        "--starts",
        "2026-04-01",
        "--ends",
        "2026-04-05",
    )
    res = _run_cli(db_path, "unavailability", "list", "--member", "alice")
    rows = json.loads(res.stdout)["data"]["unavailability"]
    assert len(rows) == 1
    assert rows[0]["member_slug"] == "alice"


def test_unavailability_remove(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    add_res = _run_cli(
        db_path,
        "unavailability",
        "add",
        "alice",
        "--starts",
        "2026-03-01",
        "--ends",
        "2026-03-08",
    )
    unavail_id = json.loads(add_res.stdout)["data"]["unavailability"]["id"]

    rm_res = _run_cli(db_path, "unavailability", "remove", str(unavail_id))
    assert rm_res.returncode == 0, rm_res.stdout + rm_res.stderr

    list_res = _run_cli(db_path, "unavailability", "list")
    assert json.loads(list_res.stdout)["data"]["unavailability"] == []
