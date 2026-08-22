"""CLI-layer tests for ``risk strike`` — issue / remove / consequences resolve + errors.

Standing / list / consequences list / pending-for already exercised by the
JSON envelope sweep; this focuses on the write paths uncovered there.
"""

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
    # Seed a removal method via the CLI.
    _run_cli(db_path, "config", "removal-method", "add", "car-wash", "--display-name", "Car wash")


def _issue_strike(db_path: Path, member: str, on: str, reason: str = "no-show") -> int:
    res = _run_cli(db_path, "strike", "issue", member, "--reason", reason, "--on", on)
    assert res.returncode == 0, res.stdout + res.stderr
    return int(json.loads(res.stdout)["data"]["strike_id"])


def test_strike_issue_unknown_member_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    res = _run_cli(db_path, "strike", "issue", "ghost", "--reason", "no-show", "--on", "2026-02-14")
    assert res.returncode != 0
    assert json.loads(res.stdout)["error"]["code"] == "member.not_found"


def test_strike_remove_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    sid = _issue_strike(db_path, "alice", "2026-02-14")
    res = _run_cli(
        db_path,
        "strike",
        "remove",
        "alice",
        "--method",
        "car-wash",
        "--on",
        "2026-03-01",
        "--strikes",
        str(sid),
    )
    assert res.returncode == 0, res.stdout + res.stderr
    payload = json.loads(res.stdout)["data"]
    assert sid in payload["closed_strike_ids"]


def test_strike_remove_unknown_member_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    res = _run_cli(
        db_path,
        "strike",
        "remove",
        "ghost",
        "--method",
        "car-wash",
        "--on",
        "2026-03-01",
        "--strikes",
        "1",
    )
    assert res.returncode != 0
    assert json.loads(res.stdout)["error"]["code"] == "member.not_found"


def test_strike_remove_unknown_method_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    sid = _issue_strike(db_path, "alice", "2026-02-14")
    res = _run_cli(
        db_path,
        "strike",
        "remove",
        "alice",
        "--method",
        "no-such-method",
        "--on",
        "2026-03-01",
        "--strikes",
        str(sid),
    )
    assert res.returncode != 0
    assert json.loads(res.stdout)["error"]["code"] == "removal_method.not_found"


def test_strike_remove_unknown_by_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    sid = _issue_strike(db_path, "alice", "2026-02-14")
    res = _run_cli(
        db_path,
        "strike",
        "remove",
        "alice",
        "--method",
        "car-wash",
        "--on",
        "2026-03-01",
        "--strikes",
        str(sid),
        "--by",
        "ghost",
    )
    assert res.returncode != 0
    assert json.loads(res.stdout)["error"]["code"] == "member.not_found"


def test_strike_remove_empty_strikes_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    res = _run_cli(
        db_path,
        "strike",
        "remove",
        "alice",
        "--method",
        "car-wash",
        "--on",
        "2026-03-01",
        "--strikes",
        "",
    )
    assert res.returncode != 0
    assert json.loads(res.stdout)["error"]["code"] == "strike.remove.empty"


def test_strike_remove_bad_link_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    res = _run_cli(
        db_path,
        "strike",
        "remove",
        "alice",
        "--method",
        "car-wash",
        "--on",
        "2026-03-01",
        "--strikes",
        "9999",
    )
    assert res.returncode != 0
    assert json.loads(res.stdout)["error"]["code"] == "strike.remove.bad_link"


def test_strike_consequences_resolve_served(tmp_path: Path) -> None:
    """Issue 2 strikes to trigger extra_shift, then resolve it served."""
    db_path = tmp_path / "r.db"
    _seed(db_path)
    _issue_strike(db_path, "alice", "2026-02-14")
    _issue_strike(db_path, "alice", "2026-02-15", reason="late")

    pending_res = _run_cli(db_path, "strike", "consequences", "pending-for", "alice")
    pending = json.loads(pending_res.stdout)["data"]["pending"]
    assert len(pending) == 1
    pc_id = pending[0]["id"]

    resolve_res = _run_cli(
        db_path, "strike", "consequences", "resolve", str(pc_id), "--as", "served"
    )
    assert resolve_res.returncode == 0, resolve_res.stdout + resolve_res.stderr
    assert json.loads(resolve_res.stdout)["data"]["consequence"]["state"] == "served"


def test_strike_consequences_resolve_bad_state_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    res = _run_cli(db_path, "strike", "consequences", "resolve", "1", "--as", "completed")
    assert res.returncode != 0
    assert json.loads(res.stdout)["error"]["code"] == "consequence.resolve.bad_state"


def test_strike_consequences_resolve_unknown_id_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    res = _run_cli(db_path, "strike", "consequences", "resolve", "9999", "--as", "served")
    assert res.returncode != 0
    assert json.loads(res.stdout)["error"]["code"] == "consequence.resolve.not_pending"
