"""Phase 2 CLI round-trip via Typer's CliRunner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from risk.cli.main import app

pytestmark = pytest.mark.integration


def _run(runner: CliRunner, db: Path, *args: str, expect_ok: bool = True) -> dict[str, Any]:
    res = runner.invoke(app, ["--json", "--db", str(db), *args])
    data: dict[str, Any] = json.loads(res.output)
    if expect_ok:
        assert res.exit_code == 0, res.output
        assert data["ok"] is True, data
    else:
        assert res.exit_code != 0, res.output
        assert data["ok"] is False, data
    return data


def test_full_member_workflow(tmp_path: Path) -> None:
    runner = CliRunner()
    db = tmp_path / "p2.db"

    _run(runner, db, "semester", "add", "SP26", "--starts", "2026-01-15", "--ends", "2026-05-15")
    _run(runner, db, "semester", "set-current", "SP26")
    _run(runner, db, "config", "house", "add", "main", "--display-name", "Main")
    _run(runner, db, "config", "role", "add", "brother", "--display-name", "Brother")

    _run(
        runner,
        db,
        "member",
        "add",
        "colin-guenther",
        "--display-name",
        "Colin Guenther",
        "--class-year",
        "2027",
    )
    _run(runner, db, "member", "set-role", "colin-guenther", "brother")
    _run(runner, db, "member", "add-alias", "colin-guenther", "Colin G")
    _run(runner, db, "member", "set-house", "colin-guenther", "main")

    show = _run(runner, db, "member", "show", "colin-guenther", "--semester", "SP26")
    payload = show["data"]
    assert payload["member"]["slug"] == "colin-guenther"
    assert payload["member"]["status_slug"] == "active"
    aliases = [a["alias"] for a in payload["aliases"]]
    assert aliases == ["Colin G"]
    roles = [r["role_slug"] for r in payload["roles"]]
    assert roles == ["brother"]
    assert payload["house"]["house_slug"] == "main"


def test_member_resolve_by_alias(tmp_path: Path) -> None:
    runner = CliRunner()
    db = tmp_path / "p2.db"
    _run(runner, db, "semester", "add", "SP26", "--starts", "2026-01-15", "--ends", "2026-05-15")
    _run(runner, db, "semester", "set-current", "SP26")
    _run(runner, db, "member", "add", "x", "--display-name", "X")
    _run(runner, db, "member", "add-alias", "x", "Mr X")
    show = _run(runner, db, "member", "show", "Mr X")
    assert show["data"]["member"]["slug"] == "x"


def test_set_role_without_current_semester_fails(tmp_path: Path) -> None:
    runner = CliRunner()
    db = tmp_path / "p2.db"
    _run(runner, db, "member", "add", "x", "--display-name", "X")
    _run(runner, db, "config", "role", "add", "brother", "--display-name", "Brother")
    out = _run(runner, db, "member", "set-role", "x", "brother", expect_ok=False)
    assert out["error"]["code"] == "semester.no_current"


def test_set_house_mode_round_trip(tmp_path: Path) -> None:
    runner = CliRunner()
    db = tmp_path / "p2.db"
    _run(runner, db, "semester", "add", "SP26", "--starts", "2026-01-15", "--ends", "2026-05-15")
    _run(runner, db, "config", "house", "add", "main", "--display-name", "Main")
    out = _run(
        runner,
        db,
        "semester",
        "set-house-mode",
        "SP26",
        "--house",
        "main",
        "--mode",
        "pledge_takeover_full",
    )
    assert out["data"]["pledge_mode_slug"] == "pledge_takeover_full"
