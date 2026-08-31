"""Integration tests for the opt-in repository privacy hooks."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
GUARD = ROOT / "scripts" / "privacy_guard.py"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "public-repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    return repo


def _guard(
    repo: Path, db_path: Path, *args: str
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["RISK_DB_PATH"] = str(db_path)
    return subprocess.run(
        [sys.executable, str(GUARD), *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _stage(repo: Path, relative_path: str, content: str | bytes) -> None:
    path = repo / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    _git(repo, "add", relative_path)


def test_staged_file_with_sqlite_magic_is_blocked_despite_extension(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _stage(repo, "innocent-looking.txt", b"SQLite format 3\x00" + b"\x00" * 64)

    result = _guard(repo, tmp_path / "no-local-db", "staged")

    assert result.returncode == 1
    assert "SQLite database magic bytes" in result.stderr


@pytest.mark.parametrize(
    ("content", "finding"),
    [
        ('group_id = "876543210"\n', "ID-shaped"),
        ('access_token = "fictional-secret-value"\n', "token-shaped"),
    ],
)
def test_staged_added_lines_with_sensitive_shapes_are_blocked(
    tmp_path: Path, content: str, finding: str
) -> None:
    repo = _repo(tmp_path)
    _stage(repo, "settings.txt", content)

    result = _guard(repo, tmp_path / "no-local-db", "staged")

    assert result.returncode == 1
    assert finding in result.stderr


def test_roster_names_are_loaded_from_database_at_hook_runtime(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    db_path = tmp_path / "local-only.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE members (display_name TEXT NOT NULL)")
    conn.commit()
    _stage(repo, "notes.txt", "Zinnia Quill is assigned.\n")

    before_insert = _guard(repo, db_path, "staged")
    assert before_insert.returncode == 0

    conn.execute("INSERT INTO members (display_name) VALUES (?)", ("Zinnia Quill",))
    conn.commit()
    conn.close()
    after_insert = _guard(repo, db_path, "staged")

    assert after_insert.returncode == 1
    assert "name from the local roster" in after_insert.stderr
    assert "Zinnia Quill" not in after_insert.stderr
    assert _git_tracked(repo) == ["notes.txt"]


def _git_tracked(repo: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"], cwd=repo, capture_output=True, text=True, check=True
    )
    return result.stdout.splitlines()


@pytest.mark.parametrize(
    "message",
    [
        "configure remote id 987654321",
        "configure access token: fictional-secret-value",
    ],
)
def test_commit_message_sensitive_shapes_are_blocked(tmp_path: Path, message: str) -> None:
    repo = _repo(tmp_path)
    message_path = tmp_path / "COMMIT_EDITMSG"
    message_path.write_text(message, encoding="utf-8")

    result = _guard(repo, tmp_path / "no-local-db", "commit-msg", str(message_path))

    assert result.returncode == 1
    assert "commit message" in result.stderr


def test_commit_message_roster_name_is_blocked_without_echoing_it(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    db_path = tmp_path / "local-only.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE members (display_name TEXT NOT NULL)")
    conn.execute("INSERT INTO members (display_name) VALUES (?)", ("Orson Vellum",))
    conn.commit()
    conn.close()
    message_path = tmp_path / "COMMIT_EDITMSG"
    message_path.write_text("update schedule for Orson Vellum", encoding="utf-8")

    result = _guard(repo, db_path, "commit-msg", str(message_path))

    assert result.returncode == 1
    assert "name from the local roster" in result.stderr
    assert "Orson Vellum" not in result.stderr
