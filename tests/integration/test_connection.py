"""Tests for risk.db.connection — env var resolution and transaction retry."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from risk.db.connection import DEFAULT_DB_PATH, connect, resolve_db_path, transaction
from risk.db.schema import ensure_schema

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fix 5: RISK_DB_PATH env var
# ---------------------------------------------------------------------------


def test_risk_db_path_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """RISK_DB_PATH env var should override the default DB path."""
    custom_db = tmp_path / "custom.db"
    monkeypatch.setenv("RISK_DB_PATH", str(custom_db))
    result = resolve_db_path()
    assert result == custom_db


def test_resolve_db_path_explicit_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit path argument overrides both env var and defaults."""
    custom_db = tmp_path / "custom.db"
    explicit = tmp_path / "explicit.db"
    monkeypatch.setenv("RISK_DB_PATH", str(custom_db))
    result = resolve_db_path(explicit=explicit)
    assert result == explicit


def test_resolve_db_path_default_when_no_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without RISK_DB_PATH and without a legacy path, returns the XDG default."""
    monkeypatch.delenv("RISK_DB_PATH", raising=False)
    # Patch the legacy check so it does not trigger the fallback.
    with patch("risk.db.connection._LEGACY_DB_PATH", tmp_path / "nonexistent.db"):
        result = resolve_db_path()
    assert result == DEFAULT_DB_PATH


# ---------------------------------------------------------------------------
# Fix 6: transaction() retry on SQLITE_BUSY
#
# sqlite3.Connection.execute is a C-extension slot that cannot be patched with
# patch.object.  Instead we use a thin Python subclass that delegates to the
# real connection but can intercept BEGIN statements.
# ---------------------------------------------------------------------------


class _FlakyConnection:
    """Wraps a real sqlite3.Connection and raises OperationalError("database is
    locked") on the first `n` BEGIN IMMEDIATE calls, then lets them through."""

    def __init__(self, real_conn: sqlite3.Connection, fail_count: int) -> None:
        self._real = real_conn
        self._fail_count = fail_count
        self.call_count = 0
        self.row_factory = real_conn.row_factory

    def execute(self, sql: str, *args, **kwargs):  # type: ignore[no-untyped-def]
        if sql.strip().upper().startswith("BEGIN") and self.call_count < self._fail_count:
            self.call_count += 1
            raise sqlite3.OperationalError("database is locked")
        return self._real.execute(sql, *args, **kwargs)

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        return getattr(self._real, name)


def test_transaction_retry_on_busy(tmp_path: Path) -> None:
    """transaction() should retry on SQLITE_BUSY before succeeding."""
    db_path = tmp_path / "retry.db"
    real_conn = connect(db_path)
    ensure_schema(real_conn)

    flaky: _FlakyConnection = _FlakyConnection(real_conn, fail_count=2)

    with transaction(flaky):  # type: ignore[arg-type]
        pass  # Should succeed after 2 retries

    assert flaky.call_count == 2, f"Expected 2 retries, got {flaky.call_count}"
    real_conn.close()


def test_transaction_raises_after_max_retries(tmp_path: Path) -> None:
    """transaction() should re-raise after RETRY_MAX_ATTEMPTS exhausted."""
    from risk.db.connection import RETRY_MAX_ATTEMPTS

    db_path = tmp_path / "exhaust.db"
    real_conn = connect(db_path)
    ensure_schema(real_conn)

    # fail_count > RETRY_MAX_ATTEMPTS so it always fails
    flaky = _FlakyConnection(real_conn, fail_count=RETRY_MAX_ATTEMPTS + 10)

    with pytest.raises(sqlite3.OperationalError, match="locked"), transaction(flaky):  # type: ignore[arg-type]
        pass

    assert flaky.call_count == RETRY_MAX_ATTEMPTS
    real_conn.close()


def test_transaction_rollback_on_exception(tmp_path: Path) -> None:
    """transaction() rolls back cleanly when the body raises."""
    db_path = tmp_path / "rollback.db"
    conn = connect(db_path)
    ensure_schema(conn)

    with pytest.raises(RuntimeError, match="simulated"), transaction(conn):
        conn.execute(
            "INSERT INTO semesters (name, starts_on, ends_on) VALUES (?, ?, ?)",
            ("SP26", "2026-01-15", "2026-05-15"),
        )
        raise RuntimeError("simulated failure")

    count = conn.execute("SELECT COUNT(*) FROM semesters").fetchone()[0]
    assert count == 0
    conn.close()


def test_the_cli_and_the_app_resolve_to_the_same_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One definition of where the chapter lives, not two.

    These were separate constants until they drifted: the desktop launcher
    hardcoded ``~/Library/Application Support/risk-management/risk.db`` while
    ``connection`` resolved to ``~/.local/share/risk/risk.db``. The app and the
    shell therefore opened DIFFERENT databases on the same machine — and since
    ``ensure_schema`` creates whatever it is handed, the first bare ``risk``
    command did not fail, it silently created an empty second chapter. Running
    ``risk member list`` against a fully loaded app then printed nothing, which
    is indistinguishable from having lost the roster.

    Caught by running the real ingest against the real machine, not by the suite,
    which is why it is now in the suite.
    """
    from risk.db.connection import resolve_db_path
    from risk.desktop.__main__ import _default_db_path

    monkeypatch.delenv("RISK_DB_PATH", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert resolve_db_path() == _default_db_path()


def test_xdg_data_home_still_wins_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """A user who sets XDG_DATA_HOME means it, on any platform."""
    from risk.db.connection import _default_db_path

    monkeypatch.setenv("XDG_DATA_HOME", "/tmp/xdg-test")
    assert _default_db_path() == Path("/tmp/xdg-test") / "risk" / "risk.db"
