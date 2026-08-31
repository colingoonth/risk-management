"""Static checks for ignored private artifacts and opt-in hook installation."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "path",
    [
        "fictional-roster.sqlite",
        "fictional-roster.sqlite-wal",
        "fictional-roster.sqlite-shm",
        "fictional-roster.sqlite3",
        "fictional-roster.sqlite3-wal",
        "fictional-roster.sqlite3-shm",
        "fictional-roster.bak",
        "fictional-roster.csv",
        "fictional-roster.json",
        "fictional-roster.jsonl",
        "fictional-roster.xlsx",
        "fictional-export.pdf",
        "fictional-run.log",
        "Screenshot 2026-01-02 at 03.04.05.png",
        "screenshots/fictional-roster.png",
    ],
)
def test_private_artifact_is_gitignored(path: str) -> None:
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--quiet", path],
        cwd=ROOT,
        check=False,
    )
    assert result.returncode == 0, path


def test_hooks_are_executable_and_documented_as_an_explicit_one_line_install() -> None:
    pre_commit = ROOT / "scripts" / "pre-commit"
    commit_msg = ROOT / "scripts" / "commit-msg"
    assert os.access(pre_commit, os.X_OK)
    assert os.access(commit_msg, os.X_OK)

    documentation = (ROOT / "scripts" / "README.md").read_text(encoding="utf-8")
    assert "git config core.hooksPath scripts" in documentation
