"""Phase 3: events repo, host-change trigger, audit completeness, target=0."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from risk.cli.main import app
from risk.db.connection import transaction
from risk.repos import event_types as etypes_repo
from risk.repos import events as events_repo
from risk.repos import houses as houses_repo
from risk.repos import semesters as semesters_repo

pytestmark = pytest.mark.integration


def _setup_event(db: sqlite3.Connection) -> int:
    with transaction(db):
        sem_id = semesters_repo.insert(
            db, name="SP26", starts_on="2026-01-15", ends_on="2026-05-15"
        )
        houses_repo.insert(db, slug="zta", display_name="ZTA")
        houses_repo.insert(db, slug="kd", display_name="KD")
    mixer = etypes_repo.get_by_slug(db, "mixer")
    zta = houses_repo.get_by_slug(db, "zta")
    assert mixer is not None and zta is not None
    with transaction(db):
        return events_repo.insert(
            db,
            semester_id=sem_id,
            event_type_id=mixer.id,
            display_name="ZTA mixer",
            date="2026-02-14",
            host_house_id=zta.id,
        )


def test_host_change_flips_resync_pending(db: sqlite3.Connection) -> None:
    ev_id = _setup_event(db)
    kd = houses_repo.get_by_slug(db, "kd")
    assert kd is not None
    before = events_repo.get_by_id(db, ev_id)
    assert before is not None and before.resync_pending is False
    with transaction(db):
        events_repo.update_host(db, event_id=ev_id, host_house_id=kd.id)
    after = events_repo.get_by_id(db, ev_id)
    assert after is not None and after.resync_pending is True


def test_event_times_both_or_neither(db: sqlite3.Connection) -> None:
    """CHECK ((s IS NULL AND e IS NULL) OR (both NOT NULL))."""
    _setup_event(db)
    mixer = etypes_repo.get_by_slug(db, "mixer")
    sem = semesters_repo.get_by_name(db, "SP26")
    assert mixer is not None and sem is not None
    with pytest.raises(sqlite3.IntegrityError), transaction(db):
        events_repo.insert(
            db,
            semester_id=sem.id,
            event_type_id=mixer.id,
            display_name="bad-times",
            date="2026-02-15",
            start_time="22:00",
            end_time=None,  # half-set is illegal
        )


def test_unique_display_name_per_semester(db: sqlite3.Connection) -> None:
    _setup_event(db)
    mixer = etypes_repo.get_by_slug(db, "mixer")
    sem = semesters_repo.get_by_name(db, "SP26")
    assert mixer is not None and sem is not None
    with pytest.raises(sqlite3.IntegrityError), transaction(db):
        events_repo.insert(
            db,
            semester_id=sem.id,
            event_type_id=mixer.id,
            display_name="ZTA mixer",  # collision in same semester
            date="2026-03-01",
        )


# ---------- CLI ----------


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


def test_cli_event_add_snapshots_requirements(tmp_path: Path) -> None:
    runner = CliRunner()
    db = tmp_path / "p3.db"
    _run(runner, db, "semester", "add", "SP26", "--starts", "2026-01-15", "--ends", "2026-05-15")
    _run(runner, db, "semester", "set-current", "SP26")
    _run(runner, db, "config", "house", "add", "zta", "--display-name", "ZTA")
    _run(
        runner,
        db,
        "config",
        "house",
        "set-pref",
        "zta",
        "mixer",
        "door",
        "--min",
        "3",
        "--target",
        "3",
    )

    add = _run(
        runner,
        db,
        "event",
        "add",
        "--name",
        "ZTA mixer",
        "--type",
        "mixer",
        "--date",
        "2026-02-14",
        "--host",
        "zta",
    )
    reqs = add["data"]["requirements"]
    by_shift = {r["shift_type_slug"]: r for r in reqs}
    assert by_shift["door"]["source_layer"] == "house_preference"
    assert by_shift["door"]["min_count"] == 3
    assert by_shift["driver"]["source_layer"] == "event_type_default"


def test_cli_target_zero_requires_confirmation_in_json(tmp_path: Path) -> None:
    runner = CliRunner()
    db = tmp_path / "p3.db"
    _run(runner, db, "semester", "add", "SP26", "--starts", "2026-01-15", "--ends", "2026-05-15")
    _run(runner, db, "semester", "set-current", "SP26")
    _run(runner, db, "config", "house", "add", "zta", "--display-name", "ZTA")
    _run(
        runner,
        db,
        "event",
        "add",
        "--name",
        "ZTA mixer",
        "--type",
        "mixer",
        "--date",
        "2026-02-14",
        "--host",
        "zta",
    )
    out = _run(
        runner,
        db,
        "event",
        "set-shift-req",
        "ZTA mixer",
        "driver",
        "--min",
        "0",
        "--target",
        "0",
        expect_ok=False,
    )
    assert out["error"]["code"] == "confirm_required"

    # With --yes it applies and tags as manual_override (target=0 = suppress).
    confirmed = _run(
        runner,
        db,
        "event",
        "set-shift-req",
        "ZTA mixer",
        "driver",
        "--min",
        "0",
        "--target",
        "0",
        "--yes",
    )
    by_shift = {r["shift_type_slug"]: r for r in confirmed["data"]}
    assert by_shift["driver"]["target_count"] == 0
    assert by_shift["driver"]["source_layer"] == "manual_override"


def test_cli_resync_after_host_change(tmp_path: Path) -> None:
    runner = CliRunner()
    db = tmp_path / "p3.db"
    _run(runner, db, "semester", "add", "SP26", "--starts", "2026-01-15", "--ends", "2026-05-15")
    _run(runner, db, "semester", "set-current", "SP26")
    _run(runner, db, "config", "house", "add", "zta", "--display-name", "ZTA")
    _run(runner, db, "config", "house", "add", "kd", "--display-name", "KD")
    _run(
        runner,
        db,
        "config",
        "house",
        "set-pref",
        "kd",
        "mixer",
        "door",
        "--min",
        "3",
        "--target",
        "5",
    )
    _run(
        runner,
        db,
        "event",
        "add",
        "--name",
        "ZTA mixer",
        "--type",
        "mixer",
        "--date",
        "2026-02-14",
        "--host",
        "zta",
    )
    _run(runner, db, "event", "set-host", "ZTA mixer", "--house", "kd")
    show_before = _run(runner, db, "event", "show", "ZTA mixer")
    assert show_before["data"]["event"]["resync_pending"] is True

    _run(runner, db, "event", "resync-shift-reqs", "ZTA mixer")
    show_after = _run(runner, db, "event", "show", "ZTA mixer")
    assert show_after["data"]["event"]["resync_pending"] is False
    by_shift = {r["shift_type_slug"]: r for r in show_after["data"]["requirements"]}
    assert by_shift["door"]["target_count"] == 5
    assert by_shift["door"]["source_layer"] == "resync"


def test_cli_event_audit_completeness(tmp_path: Path) -> None:
    """Every set-shift-req write produces an audit row."""
    runner = CliRunner()
    db = tmp_path / "p3.db"
    _run(runner, db, "semester", "add", "SP26", "--starts", "2026-01-15", "--ends", "2026-05-15")
    _run(runner, db, "semester", "set-current", "SP26")
    _run(
        runner,
        db,
        "event",
        "add",
        "--name",
        "Off-site philanthropy",
        "--type",
        "philanthropy",
        "--date",
        "2026-03-01",
    )
    _run(
        runner,
        db,
        "event",
        "set-shift-req",
        "Off-site philanthropy",
        "setup",
        "--min",
        "5",
        "--target",
        "6",
    )
    _run(
        runner,
        db,
        "event",
        "set-shift-req",
        "Off-site philanthropy",
        "setup",
        "--min",
        "6",
        "--target",
        "7",
    )
    # 2 from seed snapshot (setup, cleanup) + 2 manual overrides = 4 audit rows.
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT source_layer, min_count, target_count FROM event_shift_requirement_writes ORDER BY id"
    ).fetchall()
    conn.close()
    layers = [r["source_layer"] for r in rows]
    assert layers == [
        "event_type_default",
        "event_type_default",
        "manual_override",
        "manual_override",
    ]
    assert rows[-1]["min_count"] == 6
    assert rows[-1]["target_count"] == 7
