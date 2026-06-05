"""Integration tests for Phase 5 ``risk strike ...`` CLI end-to-end."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from risk.db.connection import connect, transaction
from risk.db.schema import ensure_schema
from risk.repos import member_statuses as statuses_repo
from risk.repos import members as members_repo
from risk.repos import semesters as semesters_repo

pytestmark = pytest.mark.integration


def _world(tmp_path: Path) -> tuple[Path, dict[str, int]]:
    db_path = tmp_path / "p5.db"
    conn = connect(db_path)
    ensure_schema(conn)
    sem_id = semesters_repo.insert(
        conn, name="SP26", starts_on="2026-01-15", ends_on="2026-05-15"
    )
    with transaction(conn):
        semesters_repo.set_current(conn, "SP26")
    active = statuses_repo.get_by_slug(conn, "active")
    assert active is not None
    mid_alice = members_repo.insert(
        conn, slug="alice", display_name="Alice", status_id=active.id
    )
    mid_bob = members_repo.insert(
        conn, slug="bob", display_name="Bob", status_id=active.id
    )
    conn.close()
    return db_path, {"sem": sem_id, "alice": mid_alice, "bob": mid_bob}


def _run(db_path: Path, *args: str) -> dict[str, object]:
    risk_bin = Path(sys.executable).parent / "risk"
    proc = subprocess.run(
        [str(risk_bin), "--db", str(db_path), "--json", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, (
        f"CLI failed: args={args!r}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    return json.loads(proc.stdout.strip())


def test_issue_then_standing(tmp_path: Path) -> None:
    db_path, _ids = _world(tmp_path)
    result = _run(
        db_path,
        "strike", "issue", "alice",
        "--reason", "no-show",
        "--on", "2026-02-10",
    )
    assert result["ok"] is True
    data = result["data"]
    assert isinstance(data, dict)
    assert data["strike_number"] == 1
    assert data["new_consequences"] == []

    standing = _run(db_path, "strike", "standing", "alice")
    assert standing["ok"] is True
    sd = standing["data"]
    assert isinstance(sd, dict)
    assert sd["active_strike_count"] == 1
    assert sd["in_bad_standing"] is False


def test_full_threshold_ladder_via_cli(tmp_path: Path) -> None:
    db_path, _ids = _world(tmp_path)
    new_kinds_seq: list[list[str]] = []
    for i in range(5):
        r = _run(
            db_path,
            "strike", "issue", "alice",
            "--reason", f"r{i + 1}",
            "--on", f"2026-02-{1 + i:02d}",
        )
        new_kinds_seq.append(r["data"]["new_consequences"])
    assert new_kinds_seq == [
        [],
        ["extra_shift"],
        [],
        ["probation"],
        ["expulsion_review"],
    ]
    standing = _run(db_path, "strike", "standing", "alice")["data"]
    assert standing["active_strike_count"] == 5
    assert standing["in_bad_standing"] is True
    assert {p["kind"] for p in standing["pending_consequences"]} == {
        "extra_shift",
        "probation",
        "expulsion_review",
    }


def test_remove_closes_and_does_not_delete_consequences(tmp_path: Path) -> None:
    db_path, _ids = _world(tmp_path)
    sids: list[int] = []
    for i in range(4):
        r = _run(
            db_path,
            "strike", "issue", "alice",
            "--reason", f"r{i + 1}",
            "--on", f"2026-02-{1 + i:02d}",
        )
        sids.append(r["data"]["strike_id"])

    rm = _run(
        db_path,
        "strike", "remove", "alice",
        "--method", "voluntary_social_risk",
        "--on", "2026-03-01",
        "--strikes", ",".join(str(s) for s in sids),
    )
    assert rm["data"]["active_count_after"] == 0

    standing = _run(db_path, "strike", "standing", "alice")["data"]
    assert standing["active_strike_count"] == 0
    # Threshold consequences SURVIVE removal (chair resolves manually).
    kinds = {p["kind"] for p in standing["pending_consequences"]}
    assert kinds == {"extra_shift", "probation"}


def test_consequences_resolve_transitions_state(tmp_path: Path) -> None:
    db_path, _ids = _world(tmp_path)
    for i in range(2):
        _run(
            db_path,
            "strike", "issue", "alice",
            "--reason", f"r{i + 1}",
            "--on", f"2026-02-{1 + i:02d}",
        )
    pending = _run(db_path, "strike", "consequences", "pending-for", "alice")["data"]
    assert len(pending["pending"]) == 1
    pc_id = pending["pending"][0]["id"]
    resolved = _run(
        db_path,
        "strike", "consequences", "resolve", str(pc_id),
        "--as", "served",
    )
    assert resolved["data"]["consequence"]["state"] == "served"
    pending_after = _run(db_path, "strike", "consequences", "pending-for", "alice")["data"]
    assert pending_after["pending"] == []


def test_back_dated_strike_is_rejected(tmp_path: Path) -> None:
    db_path, _ids = _world(tmp_path)
    _run(
        db_path,
        "strike", "issue", "alice",
        "--reason", "first",
        "--on", "2026-03-10",
    )
    risk_bin = Path(sys.executable).parent / "risk"
    proc = subprocess.run(
        [
            str(risk_bin), "--db", str(db_path), "--json",
            "strike", "issue", "alice",
            "--reason", "back-dated",
            "--on", "2026-02-01",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0
    payload = json.loads(proc.stdout.strip())
    assert payload["ok"] is False
    assert "strike" in payload["error"]["code"]


def test_strike_in_other_semester_isolated(tmp_path: Path) -> None:
    db_path, _ids = _world(tmp_path)
    # Add a fall semester directly via repo since CLI add varies.
    conn = connect(db_path)
    ensure_schema(conn)
    semesters_repo.insert(
        conn, name="FA26", starts_on="2026-08-15", ends_on="2026-12-15"
    )
    conn.close()

    for i in range(2):
        _run(
            db_path,
            "strike", "issue", "alice",
            "--reason", f"sp{i}",
            "--on", f"2026-02-{1 + i:02d}",
        )
    _run(
        db_path,
        "strike", "issue", "alice",
        "--reason", "fall",
        "--on", "2026-09-01",
        "--semester", "FA26",
    )
    sp_standing = _run(db_path, "strike", "standing", "alice")["data"]
    fa_standing = _run(
        db_path, "strike", "standing", "alice", "--semester", "FA26"
    )["data"]
    assert sp_standing["active_strike_count"] == 2
    assert fa_standing["active_strike_count"] == 1
