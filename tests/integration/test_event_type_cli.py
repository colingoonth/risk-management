"""CLI-layer tests for ``risk config event-type`` — add / allow / set-default / clear / show."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from risk.db.connection import connect
from risk.db.schema import ensure_schema

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
    """Just ensure schema — seeded event types live in migration 0002."""
    conn = connect(db_path)
    ensure_schema(conn)
    conn.close()


def test_event_type_add_creates_new(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    res = _run_cli(
        db_path, "config", "event-type", "add", "fundraiser", "--display-name", "Fundraiser"
    )
    assert res.returncode == 0, res.stdout + res.stderr

    list_res = _run_cli(db_path, "config", "event-type", "list")
    slugs = [e["slug"] for e in json.loads(list_res.stdout)["data"]]
    assert "fundraiser" in slugs


def test_event_type_add_duplicate_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    res = _run_cli(db_path, "config", "event-type", "add", "mixer", "--display-name", "Mixer")
    assert res.returncode != 0
    err = json.loads(res.stdout)["error"]
    assert err["code"] == "event_type.integrity"


def test_event_type_show_returns_defaults(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    res = _run_cli(db_path, "config", "event-type", "show", "mixer")
    assert res.returncode == 0, res.stdout + res.stderr
    data = json.loads(res.stdout)["data"]
    assert data["event_type"]["slug"] == "mixer"
    assert "cleanup" in data["allowed_shift_types"]
    assert len(data["defaults"]) >= 1


def test_event_type_show_unknown_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    res = _run_cli(db_path, "config", "event-type", "show", "no-such-type")
    assert res.returncode != 0
    assert json.loads(res.stdout)["error"]["code"] == "event_type.not_found"


def test_event_type_allow_disallow_shift_type(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    # Create a new event type so we can mutate allowed list without touching seeds.
    _run_cli(db_path, "config", "event-type", "add", "tailgate", "--display-name", "Tailgate")

    allow_res = _run_cli(
        db_path, "config", "event-type", "allow-shift-type", "tailgate", "driver"
    )
    assert allow_res.returncode == 0, allow_res.stdout + allow_res.stderr

    show_res = _run_cli(db_path, "config", "event-type", "show", "tailgate")
    assert "driver" in json.loads(show_res.stdout)["data"]["allowed_shift_types"]

    disallow_res = _run_cli(
        db_path, "config", "event-type", "disallow-shift-type", "tailgate", "driver"
    )
    assert disallow_res.returncode == 0
    show2 = _run_cli(db_path, "config", "event-type", "show", "tailgate")
    assert "driver" not in json.loads(show2.stdout)["data"]["allowed_shift_types"]


def test_event_type_allow_unknown_slug_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    res1 = _run_cli(
        db_path, "config", "event-type", "allow-shift-type", "no-such-event", "door"
    )
    assert res1.returncode != 0
    assert json.loads(res1.stdout)["error"]["code"] == "event_type.not_found"

    res2 = _run_cli(
        db_path, "config", "event-type", "allow-shift-type", "mixer", "no-such-shift"
    )
    assert res2.returncode != 0
    assert json.loads(res2.stdout)["error"]["code"] == "shift_type.not_found"


def test_event_type_set_default_and_clear(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    _seed(db_path)
    _run_cli(db_path, "config", "event-type", "add", "tailgate", "--display-name", "Tailgate")
    _run_cli(db_path, "config", "event-type", "allow-shift-type", "tailgate", "setup")

    set_res = _run_cli(
        db_path,
        "config",
        "event-type",
        "set-default",
        "tailgate",
        "setup",
        "--min",
        "2",
        "--target",
        "3",
    )
    assert set_res.returncode == 0, set_res.stdout + set_res.stderr

    show_res = _run_cli(db_path, "config", "event-type", "show", "tailgate")
    defaults = json.loads(show_res.stdout)["data"]["defaults"]
    assert any(
        d["shift_type_slug"] == "setup" and d["min_count"] == 2 and d["target_count"] == 3
        for d in defaults
    )

    clear_res = _run_cli(
        db_path, "config", "event-type", "clear-default", "tailgate", "setup"
    )
    assert clear_res.returncode == 0, clear_res.stdout + clear_res.stderr

    show2 = _run_cli(db_path, "config", "event-type", "show", "tailgate")
    defaults_after = json.loads(show2.stdout)["data"]["defaults"]
    assert not any(d["shift_type_slug"] == "setup" for d in defaults_after)
