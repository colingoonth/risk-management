"""CLI-layer tests for ``risk ingest strike-sheet`` — file/JSON/preview/apply + errors."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from risk.cli.main import app
from risk.db.connection import connect
from risk.db.schema import ensure_schema
from risk.repos import member_statuses as statuses_repo
from risk.repos import members as members_repo
from risk.repos import semesters as semesters_repo

pytestmark = pytest.mark.integration

runner = CliRunner()


def _run(db_path: Path, *args: str) -> tuple[dict, int]:
    res = runner.invoke(app, ["--json", "--db", str(db_path), *args])
    return json.loads(res.output), res.exit_code


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
    conn.close()


def _write_payload(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload))


def test_ingest_file_not_found(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    data, code = _run(db_path, "ingest", "strike-sheet", str(tmp_path / "nope.json"))
    assert code != 0
    assert data["error"]["code"] == "ingest.file_not_found"


def test_ingest_invalid_json(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    bad = tmp_path / "bad.json"
    bad.write_text("[not an object]")
    data, code = _run(db_path, "ingest", "strike-sheet", str(bad))
    assert code != 0
    assert data["error"]["code"] == "ingest.invalid_json"


def test_ingest_wrong_ingest_type(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    bad = tmp_path / "bad.json"
    _write_payload(bad, {"ingest_type": "rosters", "entries": []})
    data, code = _run(db_path, "ingest", "strike-sheet", str(bad))
    assert code != 0
    assert data["error"]["code"] == "ingest.invalid_json"


def test_ingest_dry_run_preview(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    payload_path = tmp_path / "p.json"
    _write_payload(
        payload_path,
        {
            "ingest_type": "strikes",
            "semester": "SP26",
            "entries": [
                {"member_slug": "alice", "issued_on": "2026-02-14", "reason": "no-show"},
            ],
        },
    )
    data, code = _run(db_path, "ingest", "strike-sheet", str(payload_path), "--dry-run")
    assert code == 0, data
    assert data["data"]["dry_run"] is True
    assert data["data"]["preview"]["new_strikes"] == 1


def test_ingest_dry_run_missing_semester_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    payload_path = tmp_path / "p.json"
    _write_payload(
        payload_path,
        {
            "ingest_type": "strikes",
            "entries": [
                {"member_slug": "alice", "issued_on": "2026-02-14", "reason": "no-show"},
            ],
        },
    )
    data, code = _run(db_path, "ingest", "strike-sheet", str(payload_path), "--dry-run")
    assert code != 0
    assert data["error"]["code"] == "ingest.preview_failed"


def test_ingest_apply_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    payload_path = tmp_path / "p.json"
    _write_payload(
        payload_path,
        {
            "ingest_type": "strikes",
            "semester": "SP26",
            "entries": [
                {"member_slug": "alice", "issued_on": "2026-02-14", "reason": "no-show"},
            ],
        },
    )
    first_data, first_code = _run(db_path, "ingest", "strike-sheet", str(payload_path))
    assert first_code == 0, first_data
    assert len(first_data["data"]["applied_strike_ids"]) == 1

    # Second apply should be a no-op (idempotent upsert).
    second_data, second_code = _run(db_path, "ingest", "strike-sheet", str(payload_path))
    assert second_code == 0, second_data
    assert len(second_data["data"]["applied_strike_ids"]) == 0


def test_ingest_apply_unknown_semester_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    payload_path = tmp_path / "p.json"
    _write_payload(
        payload_path,
        {
            "ingest_type": "strikes",
            "semester": "NOPE",
            "entries": [
                {"member_slug": "alice", "issued_on": "2026-02-14", "reason": "no-show"},
            ],
        },
    )
    data, code = _run(db_path, "ingest", "strike-sheet", str(payload_path))
    assert code != 0
    assert data["error"]["code"] == "ingest.apply_failed"


def test_ingest_semester_override_wins(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    payload_path = tmp_path / "p.json"
    _write_payload(
        payload_path,
        {
            "ingest_type": "strikes",
            "semester": "WRONG",
            "entries": [
                {"member_slug": "alice", "issued_on": "2026-02-14", "reason": "no-show"},
            ],
        },
    )
    data, code = _run(
        db_path,
        "ingest",
        "strike-sheet",
        str(payload_path),
        "--semester",
        "SP26",
        "--dry-run",
    )
    assert code == 0, data


# --- gform-roster ---

_ROSTER_CSV = (
    "Full Name,Rising Class,PC,EC\n"
    "Alice Anderson,Rising Senior,Zeta,Yes\n"
    "Bob Brown,Rising Junior,Eta,No\n"
)


def test_gform_roster_dry_run(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    csv_path = tmp_path / "roster.csv"
    csv_path.write_text(_ROSTER_CSV)
    data, code = _run(
        db_path, "ingest", "gform-roster", str(csv_path), "--semester", "SP26", "--dry-run"
    )
    assert code == 0, data
    assert data["data"]["dry_run"] is True
    assert data["data"]["preview"]["new_members"] == 2
    assert data["data"]["preview"]["base_year"] == 2026


def test_gform_roster_apply(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    csv_path = tmp_path / "roster.csv"
    csv_path.write_text(_ROSTER_CSV)
    data, code = _run(db_path, "ingest", "gform-roster", str(csv_path), "--semester", "SP26")
    assert code == 0, data
    assert data["data"]["inserted_members"] == 2
    assert data["data"]["exec_roles_set"] == 1  # only Alice is EC


def test_gform_roster_file_not_found(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    data, code = _run(
        db_path, "ingest", "gform-roster", str(tmp_path / "nope.csv"), "--semester", "SP26"
    )
    assert code != 0
    assert data["error"]["code"] == "ingest.file_not_found"


def test_gform_roster_unknown_semester_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    csv_path = tmp_path / "roster.csv"
    csv_path.write_text(_ROSTER_CSV)
    data, code = _run(db_path, "ingest", "gform-roster", str(csv_path), "--semester", "NOPE")
    assert code != 0
    assert data["error"]["code"] == "ingest.apply_failed"
