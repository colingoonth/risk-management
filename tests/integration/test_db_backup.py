"""Integration tests for ``risk db backup``."""

from __future__ import annotations

import json
import sqlite3
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
    conn = connect(db_path)
    ensure_schema(conn)
    semesters_repo.insert(conn, name="SP26", starts_on="2026-01-15", ends_on="2026-05-15")
    conn.close()


def test_backup_creates_valid_copy(tmp_path: Path) -> None:
    src = tmp_path / "src.db"
    dst = tmp_path / "backup.db"
    _seed(src)

    res = _run_cli(src, "db", "backup", str(dst))
    assert res.returncode == 0, res.stdout + res.stderr
    payload = json.loads(res.stdout)
    assert payload["ok"] is True
    assert payload["data"]["dest"] == str(dst)
    assert payload["data"]["bytes"] > 0

    # Backup opens cleanly and has the seeded row.
    dst_conn = sqlite3.connect(dst)
    rows = dst_conn.execute("SELECT name FROM semesters").fetchall()
    dst_conn.close()
    assert [r[0] for r in rows] == ["SP26"]


def test_backup_creates_parent_dirs(tmp_path: Path) -> None:
    src = tmp_path / "src.db"
    dst = tmp_path / "nested" / "subdir" / "backup.db"
    _seed(src)
    res = _run_cli(src, "db", "backup", str(dst))
    assert res.returncode == 0, res.stdout + res.stderr
    assert dst.exists()


def test_backup_overwrites_existing_file(tmp_path: Path) -> None:
    src = tmp_path / "src.db"
    dst = tmp_path / "backup.db"
    dst.write_bytes(b"stale junk")
    _seed(src)

    res = _run_cli(src, "db", "backup", str(dst))
    assert res.returncode == 0, res.stdout + res.stderr
    # Backup is now a real SQLite file, not the stale bytes.
    assert dst.read_bytes()[:16] == b"SQLite format 3\x00"


def test_backup_rejects_directory_dest(tmp_path: Path) -> None:
    src = tmp_path / "src.db"
    dst_dir = tmp_path / "somedir"
    dst_dir.mkdir()
    _seed(src)

    res = _run_cli(src, "db", "backup", str(dst_dir))
    assert res.returncode != 0
    envelope = json.loads(res.stdout)
    assert envelope["error"]["code"] == "backup.dest_is_dir"


def test_backup_rejects_uncreated_destination_inside_git_worktree(tmp_path: Path) -> None:
    src = tmp_path / "src.db"
    worktree = tmp_path / "public-repo"
    worktree.mkdir()
    subprocess.run(["git", "init", "-q", str(worktree)], check=True)
    dst = worktree / "private" / "fictional-backup.db"
    _seed(src)

    res = _run_cli(src, "db", "backup", str(dst))

    assert res.returncode != 0
    envelope = json.loads(res.stdout)
    assert envelope["error"]["code"] == "backup.dest_in_git_worktree"
    assert not dst.parent.exists()
