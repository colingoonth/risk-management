"""Sweep test: every read command emits a parseable JSON envelope.

Plan §6 Phase 8 exit said --json and --json-raw must work for all read
commands. This test enumerates every read surface and asserts the envelope
shape under both modes, so regressions surface immediately.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from risk.db.connection import connect, transaction
from risk.db.schema import ensure_schema
from risk.repos import event_types as etypes_repo
from risk.repos import events as events_repo
from risk.repos import houses as houses_repo
from risk.repos import member_statuses as statuses_repo
from risk.repos import members as members_repo
from risk.repos import semesters as semesters_repo
from risk.repos import shift_types as stypes_repo
from risk.services import assignment, shift_requirements

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def seeded_db(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, str]]:
    """Build a populated DB with one of every read-surface artifact."""
    db_path = tmp_path_factory.mktemp("envelope_sweep") / "r.db"
    conn = connect(db_path)
    ensure_schema(conn)

    sem_id = semesters_repo.insert(
        conn, name="SP26", starts_on="2026-01-15", ends_on="2026-05-15"
    )
    conn.execute("UPDATE semesters SET is_current = 1 WHERE id = ?", (sem_id,))
    zta_id = houses_repo.insert(conn, slug="zta", display_name="ZTA")

    # Pref-history surface needs at least one preference write.
    door = stypes_repo.get_by_slug(conn, "door")
    mixer = etypes_repo.get_by_slug(conn, "mixer")
    assert door is not None and mixer is not None
    from risk.repos import house_shift_preferences as prefs_repo

    with transaction(conn):
        prefs_repo.upsert(
            conn,
            house_id=zta_id,
            event_type_id=mixer.id,
            shift_type_id=door.id,
            min_count=2,
            target_count=3,
        )

    et = etypes_repo.get_by_slug(conn, "mixer")
    assert et is not None
    event_id = events_repo.insert(
        conn,
        semester_id=sem_id,
        event_type_id=et.id,
        display_name="ZTA mixer",
        date="2026-02-14",
        host_house_id=zta_id,
    )
    active = statuses_repo.get_by_slug(conn, "active")
    assert active is not None
    for i in range(8):
        members_repo.insert(
            conn,
            slug=f"member-{i}",
            display_name=f"Member {i}",
            status_id=active.id,
            class_year=2027,
        )
    shift_requirements.snapshot_for_event(conn, event_id)
    with transaction(conn):
        assignment.auto_assign(conn, event_id=event_id, seed=42)
    conn.close()

    return db_path, {
        "semester": "SP26",
        "house": "zta",
        "event_type": "mixer",
        "shift_type": "door",
        "member": "member-0",
        "event_id": str(event_id),
        "shift_id": "1",
    }


def _run_cli(db_path: Path, mode_flag: str, *args: str) -> subprocess.CompletedProcess[str]:
    risk_bin = Path(sys.executable).parent / "risk"
    return subprocess.run(
        [str(risk_bin), "--db", str(db_path), mode_flag, *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _read_commands(args: dict[str, str]) -> list[tuple[str, list[str]]]:
    """Every read command in the CLI, as (label, argv-tail) pairs."""
    return [
        ("semester list", ["semester", "list"]),
        ("member list", ["member", "list"]),
        ("member show", ["member", "show", args["member"]]),
        ("event list", ["event", "list"]),
        ("event show", ["event", "show", args["event_id"]]),
        ("shift list", ["shift", "list"]),
        ("shift list --member", ["shift", "list", "--member", args["member"]]),
        ("shift list --event", ["shift", "list", "--event", args["event_id"]]),
        ("shift show", ["shift", "show", args["shift_id"]]),
        ("strike list", ["strike", "list", "--member", args["member"]]),
        ("strike standing", ["strike", "standing", args["member"]]),
        ("strike consequences list", ["strike", "consequences", "list"]),
        ("strike consequences pending-for", ["strike", "consequences", "pending-for", args["member"]]),
        ("swap list", ["swap", "list"]),
        ("unavailability list", ["unavailability", "list"]),
        ("config event-type list", ["config", "event-type", "list"]),
        ("config event-type show", ["config", "event-type", "show", args["event_type"]]),
        ("config house list", ["config", "house", "list"]),
        ("config house list-prefs", ["config", "house", "list-prefs", args["house"]]),
        ("config house pref-history", ["config", "house", "pref-history", args["house"]]),
        ("config role list", ["config", "role", "list"]),
        ("config shift-type list", ["config", "shift-type", "list"]),
        ("config removal-method list", ["config", "removal-method", "list"]),
        ("config pledge-mode list", ["config", "pledge-mode", "list"]),
    ]


@pytest.mark.parametrize("mode_flag", ["--json", "--json-raw"])
def test_every_read_command_emits_parseable_json(
    seeded_db: tuple[Path, dict[str, str]], mode_flag: str
) -> None:
    db_path, args = seeded_db
    failures: list[str] = []
    for label, argv in _read_commands(args):
        res = _run_cli(db_path, mode_flag, *argv)
        if res.returncode != 0:
            failures.append(f"{label}: exit {res.returncode} stdout={res.stdout!r}")
            continue
        try:
            payload = json.loads(res.stdout)
        except json.JSONDecodeError as exc:
            failures.append(f"{label}: invalid JSON ({exc}): {res.stdout!r}")
            continue
        if mode_flag == "--json":
            if not isinstance(payload, dict) or payload.get("ok") is not True:
                failures.append(f"{label}: envelope missing or ok!=True: {payload!r}")
            for key in ("ok", "data", "error", "warnings"):
                if key not in payload:
                    failures.append(f"{label}: envelope missing key {key!r}")
    assert not failures, "JSON envelope failures:\n" + "\n".join(failures)
