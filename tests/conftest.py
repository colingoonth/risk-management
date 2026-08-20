"""Shared pytest fixtures."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from risk.db.connection import connect
from risk.db.schema import ensure_schema


def pytest_configure(config: pytest.Config) -> None:
    """Arm subprocess coverage, but only when this run is measuring coverage.

    pytest-cov ships an ``a1_coverage.pth`` site hook that calls
    ``coverage.process_startup()`` whenever COVERAGE_PROCESS_START is set in the
    env. Setting it means every ``risk`` CLI subprocess launched from an
    integration test contributes to the coverage report. Paired with
    ``parallel = true`` + ``[tool.coverage.paths]`` in pyproject.toml.

    It used to be set at import time, for every run. Without ``--cov`` there is
    no parent session to combine the results, so each subprocess dropped an
    orphan ``.coverage.<host>.<pid>.<rand>`` file in the invocation directory
    that nothing ever collected — and the suite paid ~4x its runtime (122s vs
    32s) to produce data nobody had asked for. The files are gitignored, so this
    never reached a commit; it just made the normal test loop slow and messy.

    ``hasplugin("_cov")`` is the gate because pytest-cov only registers that
    plugin when coverage is actually active, so it is False for a plain run and
    True for ``--cov`` in either form. Set in ``pytest_configure`` rather than at
    import so it lands before collection, and therefore well before any test
    spawns a subprocess.
    """
    if config.pluginmanager.hasplugin("_cov"):
        os.environ.setdefault(
            "COVERAGE_PROCESS_START",
            str(Path(__file__).parent.parent / "pyproject.toml"),
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
