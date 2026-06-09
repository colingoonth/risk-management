"""CLI-layer tests for ``risk ingest strike-sheet`` — file/JSON/preview/apply + errors."""

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
    sem_id = semesters_repo.insert(
        conn, name="SP26", starts_on="2026-01-15", ends_on="2026-05-15"
    )
    conn.execute("UPDATE semesters SET is_current = 1 WHERE id = ?", (sem_id,))
    active = statuses_repo.get_by_slug(conn, "active")
    assert active is not None
    members_repo.insert(
        conn, slug="alice", display_name="Alice", status_id=active.id, class_year=2027
    )
    conn.close()


def _write_payload(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload))


def test_ingest_file_not_found(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    res = _run_cli(db_path, "ingest", "strike-sheet", str(tmp_path / "nope.json"))
    assert res.returncode != 0
    assert json.loads(res.stdout)["error"]["code"] == "ingest.file_not_found"


def test_ingest_invalid_json(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    bad = tmp_path / "bad.json"
    bad.write_text("[not an object]")
    res = _run_cli(db_path, "ingest", "strike-sheet", str(bad))
    assert res.returncode != 0
    assert json.loads(res.stdout)["error"]["code"] == "ingest.invalid_json"


def test_ingest_wrong_ingest_type(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    bad = tmp_path / "bad.json"
    _write_payload(bad, {"ingest_type": "rosters", "entries": []})
    res = _run_cli(db_path, "ingest", "strike-sheet", str(bad))
    assert res.returncode != 0
    assert json.loads(res.stdout)["error"]["code"] == "ingest.invalid_json"


def test_ingest_dry_run_preview(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    payload_path = tmp_path / "p.json"
    _write_payload(payload_path, {
        "ingest_type": "strikes",
        "semester": "SP26",
        "entries": [
            {"member_slug": "alice", "issued_on": "2026-02-14", "reason": "no-show"},
        ],
    })
    res = _run_cli(db_path, "ingest", "strike-sheet", str(payload_path), "--dry-run")
    assert res.returncode == 0, res.stdout + res.stderr
    data = json.loads(res.stdout)["data"]
    assert data["dry_run"] is True
    assert data["preview"]["new_strikes"] == 1


def test_ingest_dry_run_missing_semester_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    payload_path = tmp_path / "p.json"
    _write_payload(payload_path, {
        "ingest_type": "strikes",
        "entries": [
            {"member_slug": "alice", "issued_on": "2026-02-14", "reason": "no-show"},
        ],
    })
    res = _run_cli(db_path, "ingest", "strike-sheet", str(payload_path), "--dry-run")
    assert res.returncode != 0
    assert json.loads(res.stdout)["error"]["code"] == "ingest.preview_failed"


def test_ingest_apply_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    payload_path = tmp_path / "p.json"
    _write_payload(payload_path, {
        "ingest_type": "strikes",
        "semester": "SP26",
        "entries": [
            {"member_slug": "alice", "issued_on": "2026-02-14", "reason": "no-show"},
        ],
    })
    first = _run_cli(db_path, "ingest", "strike-sheet", str(payload_path))
    assert first.returncode == 0, first.stdout + first.stderr
    assert len(json.loads(first.stdout)["data"]["applied_strike_ids"]) == 1

    # Second apply should be a no-op (idempotent upsert).
    second = _run_cli(db_path, "ingest", "strike-sheet", str(payload_path))
    assert second.returncode == 0, second.stdout + second.stderr
    assert len(json.loads(second.stdout)["data"]["applied_strike_ids"]) == 0


def test_ingest_apply_unknown_semester_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    payload_path = tmp_path / "p.json"
    _write_payload(payload_path, {
        "ingest_type": "strikes",
        "semester": "NOPE",
        "entries": [
            {"member_slug": "alice", "issued_on": "2026-02-14", "reason": "no-show"},
        ],
    })
    res = _run_cli(db_path, "ingest", "strike-sheet", str(payload_path))
    assert res.returncode != 0
    assert json.loads(res.stdout)["error"]["code"] == "ingest.apply_failed"


def test_ingest_semester_override_wins(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    payload_path = tmp_path / "p.json"
    _write_payload(payload_path, {
        "ingest_type": "strikes",
        "semester": "WRONG",
        "entries": [
            {"member_slug": "alice", "issued_on": "2026-02-14", "reason": "no-show"},
        ],
    })
    res = _run_cli(
        db_path, "ingest", "strike-sheet", str(payload_path),
        "--semester", "SP26", "--dry-run",
    )
    assert res.returncode == 0, res.stdout + res.stderr
