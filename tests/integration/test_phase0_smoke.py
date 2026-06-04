"""Phase 0 walking-skeleton smoke test.

Exercises the full vertical slice: connection layer → schema → repo → CLI.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from risk.cli.main import app
from risk.db.connection import connect, transaction
from risk.db.schema import ensure_schema
from risk.repos import semesters as semesters_repo

pytestmark = pytest.mark.integration


def test_pragmas_applied(db: sqlite3.Connection) -> None:
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert db.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_one_current_semester_enforced(db: sqlite3.Connection) -> None:
    """Partial unique index allows exactly one current semester."""
    with transaction(db):
        semesters_repo.insert(db, name="SP26", starts_on="2026-01-15", ends_on="2026-05-15")
        semesters_repo.insert(db, name="FA26", starts_on="2026-08-20", ends_on="2026-12-15")
    db.execute("UPDATE semesters SET is_current = 1 WHERE name = 'SP26'")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE semesters SET is_current = 1 WHERE name = 'FA26'")


def test_starts_ends_check(db: sqlite3.Connection) -> None:
    """ends_on >= starts_on enforced."""
    with pytest.raises(sqlite3.IntegrityError), transaction(db):
        semesters_repo.insert(db, name="BAD", starts_on="2026-05-15", ends_on="2026-01-15")


def test_pledge_takeover_within_semester(db: sqlite3.Connection) -> None:
    """pledge_takeover_starts_on must fall inside the semester window."""
    with pytest.raises(sqlite3.IntegrityError), transaction(db):
        semesters_repo.insert(
            db,
            name="BAD",
            starts_on="2026-01-15",
            ends_on="2026-05-15",
            pledge_takeover_starts_on="2026-06-01",
        )


def test_cli_semester_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "cli.db"
    runner = CliRunner()

    res = runner.invoke(
        app,
        [
            "--json",
            "--db",
            str(db_path),
            "semester",
            "add",
            "SP26",
            "--starts",
            "2026-01-15",
            "--ends",
            "2026-05-15",
            "--pledge-takeover",
            "2026-03-01",
        ],
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["ok"] is True
    assert payload["data"]["name"] == "SP26"

    res = runner.invoke(app, ["--json", "--db", str(db_path), "semester", "list"])
    assert res.exit_code == 0
    listed = json.loads(res.output)
    assert listed["ok"] is True
    assert len(listed["data"]) == 1
    assert listed["data"][0]["name"] == "SP26"


def test_cli_duplicate_name_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "dup.db"
    runner = CliRunner()
    base = [
        "--json",
        "--db",
        str(db_path),
        "semester",
        "add",
        "SP26",
        "--starts",
        "2026-01-15",
        "--ends",
        "2026-05-15",
    ]
    assert runner.invoke(app, base).exit_code == 0
    res = runner.invoke(app, base)
    assert res.exit_code != 0
    payload = json.loads(res.output)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "semester.integrity"


def test_transaction_rollback(tmp_path: Path) -> None:
    """Failed transactions roll back cleanly."""
    conn = connect(tmp_path / "tx.db")
    ensure_schema(conn)
    try:
        with pytest.raises(RuntimeError), transaction(conn):
            semesters_repo.insert(conn, name="X", starts_on="2026-01-15", ends_on="2026-05-15")
            raise RuntimeError("simulated failure")
    finally:
        rows = conn.execute("SELECT COUNT(*) FROM semesters").fetchone()[0]
        assert rows == 0
        conn.close()
