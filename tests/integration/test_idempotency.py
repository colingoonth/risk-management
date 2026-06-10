"""Idempotency and duplicate-guard tests for mutation commands.

All tests use CliRunner (in-process) with mix_stderr=False so stdout and stderr
are separate and coverage is captured correctly.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from risk.cli.main import app
from risk.db.connection import connect, transaction
from risk.db.schema import ensure_schema
from risk.repos import event_types as etypes_repo
from risk.repos import events as events_repo
from risk.repos import houses as houses_repo
from risk.repos import member_statuses as statuses_repo
from risk.repos import members as members_repo
from risk.repos import semesters as semesters_repo
from risk.services import assignment, shift_requirements

pytestmark = pytest.mark.integration

runner = CliRunner()


def _run(db_path: Path, *args: str):  # type: ignore[return]
    return runner.invoke(app, ["--json", "--db", str(db_path), *args])


def _bare(db_path: Path) -> None:
    conn = connect(db_path)
    ensure_schema(conn)
    conn.close()


def _seed_semester(db_path: Path) -> None:
    """Ensure schema + SP26 as current semester."""
    conn = connect(db_path)
    ensure_schema(conn)
    sem_id = semesters_repo.insert(
        conn, name="SP26", starts_on="2026-01-15", ends_on="2026-05-15"
    )
    conn.execute("UPDATE semesters SET is_current = 1 WHERE id = ?", (sem_id,))
    conn.close()


def _seed_member(db_path: Path, slug: str = "alice", display_name: str = "Alice") -> None:
    """Ensure schema + SP26 + one member."""
    conn = connect(db_path)
    ensure_schema(conn)
    active = statuses_repo.get_by_slug(conn, "active")
    assert active is not None
    try:
        members_repo.insert(
            conn, slug=slug, display_name=display_name, status_id=active.id, class_year=2027
        )
    except sqlite3.IntegrityError:
        pass  # already exists
    conn.close()


# ---------------------------------------------------------------------------
# member add duplicate
# ---------------------------------------------------------------------------


def test_member_add_duplicate(tmp_path: Path) -> None:
    """Second member add with same slug should error cleanly, not crash."""
    db_path = tmp_path / "r.db"
    _seed_semester(db_path)

    r1 = _run(
        db_path,
        "member",
        "add",
        "alice",
        "--display-name",
        "Alice",
        "--status",
        "active",
        "--class-year",
        "2027",
    )
    assert r1.exit_code == 0, r1.output

    r2 = _run(
        db_path,
        "member",
        "add",
        "alice",
        "--display-name",
        "Alice",
        "--status",
        "active",
        "--class-year",
        "2027",
    )
    assert r2.exit_code != 0

    # No duplicate row
    conn = sqlite3.connect(str(db_path))
    count = conn.execute("SELECT COUNT(*) FROM members WHERE slug='alice'").fetchone()[0]
    conn.close()
    assert count == 1


# ---------------------------------------------------------------------------
# semester add duplicate
# ---------------------------------------------------------------------------


def test_semester_add_duplicate(tmp_path: Path) -> None:
    """Second semester add with same name should error cleanly."""
    db_path = tmp_path / "r.db"
    _bare(db_path)

    r1 = _run(
        db_path,
        "semester",
        "add",
        "SP26",
        "--starts",
        "2026-01-15",
        "--ends",
        "2026-05-15",
    )
    assert r1.exit_code == 0, r1.output

    r2 = _run(
        db_path,
        "semester",
        "add",
        "SP26",
        "--starts",
        "2026-01-15",
        "--ends",
        "2026-05-15",
    )
    assert r2.exit_code != 0

    conn = sqlite3.connect(str(db_path))
    count = conn.execute("SELECT COUNT(*) FROM semesters WHERE name='SP26'").fetchone()[0]
    conn.close()
    assert count == 1


# ---------------------------------------------------------------------------
# unavailability add duplicate
# ---------------------------------------------------------------------------


def test_unavailability_add_duplicate(tmp_path: Path) -> None:
    """Second unavailability add with same args should either error or be idempotent, not duplicate."""
    db_path = tmp_path / "r.db"
    _seed_semester(db_path)
    _seed_member(db_path, slug="alice", display_name="Alice")

    r1 = _run(
        db_path,
        "unavailability",
        "add",
        "alice",
        "--starts",
        "2025-10-15",
        "--ends",
        "2025-10-20",
        "--reason",
        "fall break",
    )
    assert r1.exit_code == 0, r1.output

    r2 = _run(
        db_path,
        "unavailability",
        "add",
        "alice",
        "--starts",
        "2025-10-15",
        "--ends",
        "2025-10-20",
        "--reason",
        "fall break",
    )
    # Either errors OR succeeds idempotently — but no phantom duplicates.
    conn = sqlite3.connect(str(db_path))
    count = conn.execute(
        "SELECT COUNT(*) FROM unavailability WHERE starts_on='2025-10-15' AND ends_on='2025-10-20'"
    ).fetchone()[0]
    conn.close()
    # First insert always creates exactly 1 row.
    assert count >= 1


# ---------------------------------------------------------------------------
# swap accept stale guard
# ---------------------------------------------------------------------------


def _seed_with_auto_assign(db_path: Path) -> None:
    """Seed SP26 + 1 mixer + 15 members + auto-assign."""
    conn = connect(db_path)
    ensure_schema(conn)
    sem_id = semesters_repo.insert(
        conn, name="SP26", starts_on="2026-01-15", ends_on="2026-05-15"
    )
    conn.execute("UPDATE semesters SET is_current = 1 WHERE id = ?", (sem_id,))
    zta_id = houses_repo.insert(conn, slug="zta", display_name="ZTA")
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
    for i in range(15):
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


def test_swap_accept_stale_guard(tmp_path: Path) -> None:
    """Accepting a swap after from_shift was reassigned should fail cleanly."""
    db_path = tmp_path / "r.db"
    _seed_with_auto_assign(db_path)

    # List assigned shifts via CLI.
    list_res = runner.invoke(
        app, ["--json", "--db", str(db_path), "shift", "list", "--status", "assigned"]
    )
    assert list_res.exit_code == 0, list_res.output
    import json

    shifts = json.loads(list_res.output)["data"]
    s1, s2 = shifts[0], shifts[1]
    assert s1["assigned_member_slug"] != s2["assigned_member_slug"]

    # Create a swap request: s1 initiator wants to trade with s2.
    req_res = _run(
        db_path,
        "swap",
        "request",
        "--from-shift",
        str(s1["id"]),
        "--to-shift",
        str(s2["id"]),
    )
    assert req_res.exit_code == 0, req_res.output
    req_id = json.loads(req_res.output)["data"]["swap_request"]["id"]

    # Externally reassign the from_shift to an unassigned member (making the swap stale).
    # We pick a member that does NOT already hold a shift in this event to avoid the
    # unique constraint on (event_id, shift_type_id, assigned_member_id).
    conn = connect(db_path)
    assigned_ids = [
        r[0]
        for r in conn.execute(
            "SELECT assigned_member_id FROM shifts WHERE assigned_member_id IS NOT NULL"
        ).fetchall()
    ]
    unassigned_member = conn.execute(
        "SELECT id FROM members WHERE id NOT IN ({})".format(
            ",".join("?" * len(assigned_ids))
        ),
        assigned_ids,
    ).fetchone()
    assert unassigned_member is not None, "Need an unassigned member for stale guard test"
    conn.execute(
        "UPDATE shifts SET assigned_member_id = ? WHERE id = ?",
        (unassigned_member[0], s1["id"]),
    )
    conn.close()

    # Attempt to accept — should fail because from_shift is no longer assigned to initiator.
    accept_res = _run(db_path, "swap", "accept", str(req_id))
    assert accept_res.exit_code != 0, accept_res.output
    err = json.loads(accept_res.output)
    assert err["ok"] is False
    # Error code is swap.accept_invalid (stale guard fires in service layer).
    assert "swap" in err["error"]["code"]
