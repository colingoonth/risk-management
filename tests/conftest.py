"""Shared pytest fixtures."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from risk.db.connection import connect
from risk.db.schema import ensure_schema

# Subprocess coverage. pytest-cov ships an `a1_coverage.pth` site hook that
# calls `coverage.process_startup()` whenever COVERAGE_PROCESS_START is set
# in the env. Setting it here means every `risk` CLI subprocess launched
# from an integration test contributes to the coverage report. Paired with
# `parallel = true` + `[tool.coverage.paths]` in pyproject.toml.
os.environ.setdefault(
    "COVERAGE_PROCESS_START", str(Path(__file__).parent.parent / "pyproject.toml")
)


@pytest.fixture()
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """File-backed SQLite DB with full schema applied. WAL mode is real."""
    conn = connect(tmp_path / "risk.db")
    ensure_schema(conn)
    try:
        yield conn
    finally:
        conn.close()
