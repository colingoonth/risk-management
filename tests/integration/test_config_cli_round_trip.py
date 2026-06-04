"""Phase 1 CLI round-trip tests through Typer's CliRunner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from risk.cli.main import app

pytestmark = pytest.mark.integration


def _run(runner: CliRunner, db: Path, *args: str) -> dict[str, Any]:
    res = runner.invoke(app, ["--json", "--db", str(db), *args])
    assert res.exit_code == 0, res.output
    data: dict[str, Any] = json.loads(res.output)
    assert data["ok"] is True, data
    return data


def test_house_add_and_set_pref(tmp_path: Path) -> None:
    runner = CliRunner()
    db = tmp_path / "cfg.db"
    _run(runner, db, "config", "house", "add", "main", "--display-name", "Main")
    _run(
        runner,
        db,
        "config",
        "house",
        "set-pref",
        "main",
        "mixer",
        "door",
        "--min",
        "3",
        "--target",
        "3",
    )
    history = _run(runner, db, "config", "house", "pref-history", "main")
    assert isinstance(history["data"], list)
    assert len(history["data"]) == 1
    assert history["data"][0]["change_kind"] == "insert"


def test_role_with_reserved_word_fails(tmp_path: Path) -> None:
    runner = CliRunner()
    db = tmp_path / "cfg.db"
    res = runner.invoke(
        app,
        [
            "--json",
            "--db",
            str(db),
            "config",
            "role",
            "add",
            "evil-role",
            "--display-name",
            "Evil",
            "--automation-key",
            "help",
            "--default-excluded",
            "--exclude-soft",
        ],
    )
    assert res.exit_code != 0
    payload = json.loads(res.output)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "role.integrity"


def test_event_type_show_includes_seed_data(tmp_path: Path) -> None:
    runner = CliRunner()
    db = tmp_path / "cfg.db"
    data = _run(runner, db, "config", "event-type", "show", "krush")
    payload = data["data"]
    assert isinstance(payload, dict)
    assert "bar" in payload["allowed_shift_types"]
    defaults_by_shift = {
        d["shift_type_slug"]: (d["min_count"], d["target_count"]) for d in payload["defaults"]
    }
    assert defaults_by_shift["bar"] in ([2, 2], (2, 2))


def test_removal_method_soft_delete(tmp_path: Path) -> None:
    runner = CliRunner()
    db = tmp_path / "cfg.db"
    _run(runner, db, "config", "removal-method", "retire", "donation")
    active = _run(runner, db, "config", "removal-method", "list")
    slugs = [m["slug"] for m in active["data"]]
    assert "donation" not in slugs
    # Re-adding under the same slug should succeed (soft-delete makes slug available).
    _run(runner, db, "config", "removal-method", "add", "donation", "--display-name", "Donation v2")
    active2 = _run(runner, db, "config", "removal-method", "list")
    slugs2 = [m["slug"] for m in active2["data"]]
    assert "donation" in slugs2
