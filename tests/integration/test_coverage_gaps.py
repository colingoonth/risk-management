"""Targeted tests to close coverage gaps in CLI files.

Each test exercises a realistic error branch or rarely-hit path that wasn't
covered by the broader Phase 1-8 test suites. Grouped by source file.
"""

from __future__ import annotations

import json
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


def _run(db_path: Path, *args: str) -> tuple[dict, int]:
    res = runner.invoke(app, ["--json", "--db", str(db_path), *args])
    return json.loads(res.output), res.exit_code


def _run_human(db_path: Path, *args: str, input_text: str | None = None) -> tuple[str, int]:
    res = runner.invoke(app, ["--db", str(db_path), *args], input=input_text)
    return res.output, res.exit_code


def _bare(db_path: Path) -> None:
    conn = connect(db_path)
    ensure_schema(conn)
    conn.close()


def _seed_sp26(db_path: Path) -> None:
    """Bare schema + SP26 marked current."""
    conn = connect(db_path)
    ensure_schema(conn)
    sem_id = semesters_repo.insert(conn, name="SP26", starts_on="2026-01-15", ends_on="2026-05-15")
    conn.execute("UPDATE semesters SET is_current = 1 WHERE id = ?", (sem_id,))
    conn.close()


def _seed_with_event(db_path: Path) -> int:
    """SP26 current + zta house + a mixer event + 8 active members. Returns event id."""
    conn = connect(db_path)
    ensure_schema(conn)
    sem_id = semesters_repo.insert(conn, name="SP26", starts_on="2026-01-15", ends_on="2026-05-15")
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
    for i in range(8):
        members_repo.insert(
            conn,
            slug=f"member-{i}",
            display_name=f"Member {i}",
            status_id=active.id,
            class_year=2027,
        )
    shift_requirements.snapshot_for_event(conn, event_id)
    conn.close()
    return event_id


# =====================================================================
# event.py
# =====================================================================


def test_event_add_unknown_event_type_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _seed_sp26(db)
    data, code = _run(
        db,
        "event",
        "add",
        "--name",
        "Bad",
        "--type",
        "no-such-type",
        "--date",
        "2026-02-14",
    )
    assert code != 0
    assert data["error"]["code"] == "event_type.not_found"


def test_event_add_unknown_host_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _seed_sp26(db)
    data, code = _run(
        db,
        "event",
        "add",
        "--name",
        "Bad",
        "--type",
        "mixer",
        "--date",
        "2026-02-14",
        "--host",
        "no-house",
    )
    assert code != 0
    assert data["error"]["code"] == "house.not_found"


def test_event_add_invalid_date_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _seed_sp26(db)
    data, code = _run(
        db,
        "event",
        "add",
        "--name",
        "Bad",
        "--type",
        "mixer",
        "--date",
        "not-a-date",
    )
    assert code != 0
    assert data["error"]["code"] == "event.invalid_date"


def test_event_add_duplicate_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _seed_sp26(db)
    _run(
        db,
        "event",
        "add",
        "--name",
        "Mixer",
        "--type",
        "mixer",
        "--date",
        "2026-02-14",
    )
    data, code = _run(
        db,
        "event",
        "add",
        "--name",
        "Mixer",
        "--type",
        "mixer",
        "--date",
        "2026-03-14",
    )
    assert code != 0
    assert data["error"]["code"] == "event.duplicate"


def test_event_add_with_no_current_semester_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _bare(db)
    data, code = _run(
        db,
        "event",
        "add",
        "--name",
        "X",
        "--type",
        "mixer",
        "--date",
        "2026-02-14",
    )
    assert code != 0
    assert data["error"]["code"] == "semester.no_current"


def test_event_add_with_unknown_semester_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _bare(db)
    data, code = _run(
        db,
        "event",
        "add",
        "--name",
        "X",
        "--type",
        "mixer",
        "--date",
        "2026-02-14",
        "--semester",
        "NOPE",
    )
    assert code != 0
    assert data["error"]["code"] == "semester.not_found"


def test_event_show_unknown_event_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _seed_sp26(db)
    data, code = _run(db, "event", "show", "9999")
    assert code != 0
    assert data["error"]["code"] == "event.not_found"


def test_event_set_host_clear_with_empty(tmp_path: Path) -> None:
    """Passing --house '' clears the host (flips resync_pending)."""
    db = tmp_path / "r.db"
    _seed_with_event(db)
    data, code = _run(db, "event", "set-host", "1", "--house", "")
    assert code == 0, data
    assert data["data"]["host_house_id"] is None


def test_event_cancel_decline_confirmation(tmp_path: Path) -> None:
    """Human mode, no --yes, user types 'n': cancel aborted (exit 0 by design)."""
    db = tmp_path / "r.db"
    _seed_with_event(db)
    runner.invoke(app, ["--db", str(db), "event", "cancel", "1"], input="n\n")
    # exit_code=0 is intentional for declined-by-user; verify shift wasn't cancelled
    data, _ = _run(db, "event", "show", "1")
    assert data["data"]["event"]["status"] != "cancelled"


def test_event_set_shift_req_unknown_event_human_decline(tmp_path: Path) -> None:
    """target=0 in HUMAN mode, user declines confirmation -> aborted."""
    db = tmp_path / "r.db"
    _seed_with_event(db)
    out, code = _run_human(
        db,
        "event",
        "set-shift-req",
        "1",
        "door",
        "--min",
        "0",
        "--target",
        "0",
        input_text="n\n",
    )
    assert code != 0


def test_event_set_shift_req_target_zero_human_yes_confirm(tmp_path: Path) -> None:
    """target=0 in HUMAN mode, user confirms -> proceeds."""
    db = tmp_path / "r.db"
    _seed_with_event(db)
    out, code = _run_human(
        db,
        "event",
        "set-shift-req",
        "1",
        "door",
        "--min",
        "0",
        "--target",
        "0",
        input_text="y\n",
    )
    assert code == 0, out


def test_event_auto_assign_unknown_event_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _seed_sp26(db)
    data, code = _run(db, "event", "auto-assign", "9999")
    assert code != 0
    assert data["error"]["code"] == "event.not_found"


def test_event_auto_assign_unknown_allow_key_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _seed_with_event(db)
    data, code = _run(
        db,
        "event",
        "auto-assign",
        "1",
        "--allow",
        "not_a_real_automation_key",
    )
    assert code != 0
    assert data["error"]["code"] == "allow.unknown_key"


def test_event_auto_assign_strict_below_min_errors(tmp_path: Path) -> None:
    """Strict mode with too few eligible members raises RuntimeError->below_min."""
    db = tmp_path / "r.db"
    _bare(db)
    # SP26 + zta, but ONLY 1 member - too few for a 3-door mixer
    conn = connect(db)
    sem_id = semesters_repo.insert(conn, name="SP26", starts_on="2026-01-15", ends_on="2026-05-15")
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
    # Only 1 member - mixer needs more
    members_repo.insert(
        conn, slug="lone", display_name="Lone", status_id=active.id, class_year=2027
    )
    shift_requirements.snapshot_for_event(conn, event_id)
    conn.close()
    data, code = _run(db, "event", "auto-assign", "1", "--strict")
    assert code != 0
    assert data["error"]["code"] == "assignment.below_min"


# =====================================================================
# semester.py
# =====================================================================


def test_semester_set_house_mode_unknown_semester(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _bare(db)
    data, code = _run(
        db,
        "semester",
        "set-house-mode",
        "NOPE",
        "--house",
        "zta",
        "--mode",
        "normal",
    )
    assert code != 0
    assert data["error"]["code"] == "semester.not_found"


def test_semester_set_house_mode_unknown_house(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _seed_sp26(db)
    data, code = _run(
        db,
        "semester",
        "set-house-mode",
        "SP26",
        "--house",
        "ghost",
        "--mode",
        "normal",
    )
    assert code != 0
    assert data["error"]["code"] == "house.not_found"


def test_semester_set_house_mode_unknown_mode(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _seed_sp26(db)
    _run(db, "config", "house", "add", "zta", "--display-name", "ZTA")
    data, code = _run(
        db,
        "semester",
        "set-house-mode",
        "SP26",
        "--house",
        "zta",
        "--mode",
        "no-such-mode",
    )
    assert code != 0
    assert data["error"]["code"] == "pledge_mode.not_found"


def test_semester_archive_with_carry_to(tmp_path: Path) -> None:
    """Archive --force --carry-to a real next semester."""
    db = tmp_path / "r.db"
    _bare(db)
    _run(db, "semester", "add", "SP26", "--starts", "2026-01-15", "--ends", "2026-05-15")
    _run(db, "semester", "add", "FA26", "--starts", "2026-08-15", "--ends", "2026-12-15")
    data, code = _run(
        db,
        "semester",
        "archive",
        "SP26",
        "--force",
        "--carry-to",
        "FA26",
    )
    assert code == 0, data


def test_semester_archive_human_mode_table(tmp_path: Path) -> None:
    """Archive in human mode hits the Rich-table emit branch."""
    db = tmp_path / "r.db"
    _bare(db)
    runner.invoke(
        app,
        [
            "--db",
            str(db),
            "semester",
            "add",
            "SP26",
            "--starts",
            "2026-01-15",
            "--ends",
            "2026-05-15",
        ],
    )
    res = runner.invoke(app, ["--db", str(db), "semester", "archive", "SP26", "--force"])
    assert res.exit_code == 0, res.output


def test_semester_unarchive_decline_confirmation(tmp_path: Path) -> None:
    """Unarchive in human mode, user declines -> aborted (exit 0 by design)."""
    db = tmp_path / "r.db"
    _bare(db)
    _run(db, "semester", "add", "SP26", "--starts", "2026-01-15", "--ends", "2026-05-15")
    _run(db, "semester", "archive", "SP26", "--force")
    runner.invoke(
        app,
        ["--db", str(db), "semester", "unarchive", "SP26"],
        input="n\n",
    )
    # exit_code=0 is intentional; verify semester is still archived
    list_data, _ = _run(db, "semester", "list")
    sp26 = next(r for r in list_data["data"] if r["name"] == "SP26")
    assert sp26["archived_at"] is not None


def test_semester_unarchive_not_archived_errors(tmp_path: Path) -> None:
    """Unarchive a semester that isn't archived -> service raises ValueError."""
    db = tmp_path / "r.db"
    _bare(db)
    _run(db, "semester", "add", "SP26", "--starts", "2026-01-15", "--ends", "2026-05-15")
    data, code = _run(db, "semester", "unarchive", "SP26", "--yes")
    assert code != 0
    assert data["error"]["code"] == "semester.unarchive_failed"


# =====================================================================
# strike.py
# =====================================================================


def _seed_strike_world(db_path: Path) -> None:
    conn = connect(db_path)
    ensure_schema(conn)
    sem_id = semesters_repo.insert(conn, name="SP26", starts_on="2026-01-15", ends_on="2026-05-15")
    conn.execute("UPDATE semesters SET is_current = 1 WHERE id = ?", (sem_id,))
    active = statuses_repo.get_by_slug(conn, "active")
    assert active is not None
    members_repo.insert(
        conn, slug="alice", display_name="Alice", status_id=active.id, class_year=2027
    )
    conn.close()


def test_strike_standing_unknown_member_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _seed_strike_world(db)
    data, code = _run(db, "strike", "standing", "ghost")
    assert code != 0
    assert data["error"]["code"] == "member.not_found"


def test_strike_list_member_required_errors(tmp_path: Path) -> None:
    """`strike list` requires --member."""
    db = tmp_path / "r.db"
    _seed_strike_world(db)
    data, code = _run(db, "strike", "list")
    assert code != 0
    assert data["error"]["code"] == "strike.list.member_required"


def test_strike_list_unknown_member_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _seed_strike_world(db)
    data, code = _run(db, "strike", "list", "--member", "ghost")
    assert code != 0
    assert data["error"]["code"] == "member.not_found"


def test_strike_list_include_closed(tmp_path: Path) -> None:
    """include_closed branch returns all strikes incl. removed."""
    db = tmp_path / "r.db"
    _seed_strike_world(db)
    _run(db, "strike", "issue", "alice", "--reason", "no-show", "--on", "2026-02-14")
    data, code = _run(db, "strike", "list", "--member", "alice", "--include-closed")
    assert code == 0, data
    assert len(data["data"]["strikes"]) >= 1


def test_strike_remove_bad_id_format_errors(tmp_path: Path) -> None:
    """_parse_strike_ids raises typer.BadParameter on non-integer pieces."""
    db = tmp_path / "r.db"
    _seed_strike_world(db)
    _run(db, "config", "removal-method", "add", "car-wash", "--display-name", "Car Wash")
    res = runner.invoke(
        app,
        [
            "--json",
            "--db",
            str(db),
            "strike",
            "remove",
            "alice",
            "--method",
            "car-wash",
            "--on",
            "2026-03-01",
            "--strikes",
            "abc,123",
        ],
    )
    assert res.exit_code != 0


def test_strike_consequences_pending_for_unknown_member_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _seed_strike_world(db)
    data, code = _run(db, "strike", "consequences", "pending-for", "ghost")
    assert code != 0
    assert data["error"]["code"] == "member.not_found"


def test_strike_consequences_pending_for_returns_empty(tmp_path: Path) -> None:
    """Pending-for a member with no consequences -> empty list."""
    db = tmp_path / "r.db"
    _seed_strike_world(db)
    data, code = _run(db, "strike", "consequences", "pending-for", "alice")
    assert code == 0, data
    assert data["data"]["pending"] == []


def test_strike_issue_no_current_semester_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _bare(db)
    conn = connect(db)
    active = statuses_repo.get_by_slug(conn, "active")
    assert active is not None
    members_repo.insert(
        conn, slug="alice", display_name="Alice", status_id=active.id, class_year=2027
    )
    conn.close()
    data, code = _run(db, "strike", "issue", "alice", "--reason", "x", "--on", "2026-02-14")
    assert code != 0
    assert data["error"]["code"] == "semester.no_current"


def test_strike_issue_unknown_semester_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _seed_strike_world(db)
    data, code = _run(
        db,
        "strike",
        "issue",
        "alice",
        "--reason",
        "x",
        "--on",
        "2026-02-14",
        "--semester",
        "NOPE",
    )
    assert code != 0
    assert data["error"]["code"] == "semester.not_found"


# =====================================================================
# member.py
# =====================================================================


def test_member_add_unknown_status_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _seed_sp26(db)
    data, code = _run(
        db,
        "member",
        "add",
        "alice",
        "--display-name",
        "Alice",
        "--status",
        "no-such-status",
    )
    assert code != 0
    assert data["error"]["code"] == "status.not_found"


def test_member_add_duplicate_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _seed_sp26(db)
    _run(db, "member", "add", "alice", "--display-name", "Alice")
    data, code = _run(db, "member", "add", "alice", "--display-name", "Alice2")
    assert code != 0
    assert data["error"]["code"] == "member.integrity"


def test_member_list_with_role_filter(tmp_path: Path) -> None:
    """list --role exec --semester SP26 hits the role-filter block."""
    db = tmp_path / "r.db"
    _seed_sp26(db)
    _run(db, "member", "add", "alice", "--display-name", "Alice")
    _run(db, "member", "set-role", "alice", "exec", "--semester", "SP26")
    data, code = _run(db, "member", "list", "--role", "exec", "--semester", "SP26")
    assert code == 0, data
    assert any(m.get("member_slug") == "alice" for m in data["data"])


def test_member_list_unknown_role_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _seed_sp26(db)
    data, code = _run(db, "member", "list", "--role", "no-such-role", "--semester", "SP26")
    assert code != 0
    assert data["error"]["code"] == "role.not_found"


def test_member_list_with_status_filter(tmp_path: Path) -> None:
    """list --status active hits the status-filter block."""
    db = tmp_path / "r.db"
    _seed_sp26(db)
    _run(db, "member", "add", "alice", "--display-name", "Alice")
    data, code = _run(db, "member", "list", "--status", "active")
    assert code == 0, data


def test_member_list_unknown_status_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _seed_sp26(db)
    data, code = _run(db, "member", "list", "--status", "no-such-status")
    assert code != 0
    assert data["error"]["code"] == "status.not_found"


def test_member_set_role_unknown_member_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _seed_sp26(db)
    data, code = _run(db, "member", "set-role", "ghost", "exec", "--semester", "SP26")
    assert code != 0
    assert data["error"]["code"] == "member.not_found"


def test_member_set_role_unknown_role_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _seed_sp26(db)
    _run(db, "member", "add", "alice", "--display-name", "Alice")
    data, code = _run(db, "member", "set-role", "alice", "no-such-role", "--semester", "SP26")
    assert code != 0
    assert data["error"]["code"] == "role.not_found"


def test_member_unset_role_unknown_role_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _seed_sp26(db)
    _run(db, "member", "add", "alice", "--display-name", "Alice")
    data, code = _run(db, "member", "unset-role", "alice", "no-such-role", "--semester", "SP26")
    assert code != 0
    assert data["error"]["code"] == "role.not_found"


def test_member_show_with_semester(tmp_path: Path) -> None:
    """show member --semester SP26 hits the sem-restricted branch."""
    db = tmp_path / "r.db"
    _seed_sp26(db)
    _run(db, "config", "house", "add", "zta", "--display-name", "ZTA")
    _run(db, "member", "add", "alice", "--display-name", "Alice")
    _run(db, "member", "set-house", "alice", "zta", "--semester", "SP26")
    data, code = _run(db, "member", "show", "alice", "--semester", "SP26")
    assert code == 0, data


# =====================================================================
# swap.py
# =====================================================================


def _seed_swap_world(db_path: Path) -> int:
    """Seed a swap-ready world; return event id."""
    conn = connect(db_path)
    ensure_schema(conn)
    sem_id = semesters_repo.insert(conn, name="SP26", starts_on="2026-01-15", ends_on="2026-05-15")
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
    return event_id


def test_swap_request_invalid_neither_to_shift_nor_counterparty(tmp_path: Path) -> None:
    """Shape C: --from-shift only (no --to-shift, no --counterparty) -> invalid."""
    db = tmp_path / "r.db"
    _seed_swap_world(db)
    data, _ = _run(db, "shift", "list", "--status", "assigned")
    s1_id = data["data"][0]["id"]
    res_data, code = _run(db, "swap", "request", "--from-shift", str(s1_id))
    assert code != 0
    assert res_data["error"]["code"] in ("swap.request_invalid", "swap.from_shift_open")


def test_swap_request_duplicate_open_errors(tmp_path: Path) -> None:
    """A second open swap on the same from_shift -> swap.duplicate_open."""
    db = tmp_path / "r.db"
    _seed_swap_world(db)
    data, _ = _run(db, "shift", "list", "--status", "assigned")
    s1, s2 = data["data"][0], data["data"][1]
    req_data, req_code = _run(
        db,
        "swap",
        "request",
        "--from-shift",
        str(s1["id"]),
        "--to-shift",
        str(s2["id"]),
    )
    assert req_code == 0, req_data
    # Second request, same from_shift
    dup_data, dup_code = _run(
        db,
        "swap",
        "request",
        "--from-shift",
        str(s1["id"]),
        "--to-shift",
        str(s2["id"]),
    )
    assert dup_code != 0
    assert dup_data["error"]["code"] == "swap.duplicate_open"


def test_swap_accept_after_from_shift_deleted_errors(tmp_path: Path) -> None:
    """If from_shift is missing from the row, surface a clear error.

    Requires temporarily disabling FK enforcement; the FK normally prevents
    this state, so the guard is defense-in-depth (per ADR-008).
    """
    db = tmp_path / "r.db"
    _seed_swap_world(db)
    data, _ = _run(db, "shift", "list", "--status", "assigned")
    s1, s2 = data["data"][0], data["data"][1]
    req_data, req_code = _run(
        db,
        "swap",
        "request",
        "--from-shift",
        str(s1["id"]),
        "--to-shift",
        str(s2["id"]),
    )
    assert req_code == 0
    req_id = req_data["data"]["swap_request"]["id"]
    conn = connect(db)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "UPDATE swap_requests SET from_shift_id = ? WHERE id = ?",
        (99999, req_id),
    )
    conn.commit()
    conn.close()
    accept_data, accept_code = _run(db, "swap", "accept", str(req_id))
    assert accept_code != 0
    assert accept_data["error"]["code"] == "swap.from_shift_missing"


def test_swap_cancel_decline_confirmation(tmp_path: Path) -> None:
    """Human mode, no --yes, user declines -> aborted (exit 0 by design)."""
    db = tmp_path / "r.db"
    _seed_swap_world(db)
    data, _ = _run(db, "shift", "list", "--status", "assigned")
    s1, s2 = data["data"][0], data["data"][1]
    req_data, _ = _run(
        db,
        "swap",
        "request",
        "--from-shift",
        str(s1["id"]),
        "--to-shift",
        str(s2["id"]),
    )
    req_id = req_data["data"]["swap_request"]["id"]
    runner.invoke(
        app,
        ["--db", str(db), "swap", "cancel", str(req_id)],
        input="n\n",
    )
    # exit_code=0 is intentional; the swap should still be open
    list_data, _ = _run(db, "swap", "list")
    states = [r["state"] for r in list_data["data"]["swap_requests"]]
    assert "open" in states


# =====================================================================
# unavailability.py
# =====================================================================


def test_unavailability_add_no_current_semester_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _bare(db)
    conn = connect(db)
    active = statuses_repo.get_by_slug(conn, "active")
    assert active is not None
    members_repo.insert(
        conn, slug="alice", display_name="Alice", status_id=active.id, class_year=2027
    )
    conn.close()
    data, code = _run(
        db,
        "unavailability",
        "add",
        "alice",
        "--starts",
        "2026-03-01",
        "--ends",
        "2026-03-08",
    )
    assert code != 0
    assert data["error"]["code"] == "semester.no_current"


def test_unavailability_add_unknown_semester_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _seed_sp26(db)
    _run(db, "member", "add", "alice", "--display-name", "Alice")
    data, code = _run(
        db,
        "unavailability",
        "add",
        "alice",
        "--starts",
        "2026-03-01",
        "--ends",
        "2026-03-08",
        "--semester",
        "NOPE",
    )
    assert code != 0
    assert data["error"]["code"] == "semester.not_found"


def test_unavailability_list_unknown_member_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _seed_sp26(db)
    data, code = _run(db, "unavailability", "list", "--member", "ghost")
    assert code != 0
    assert data["error"]["code"] == "member.not_found"


def test_unavailability_remove_not_found_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _seed_sp26(db)
    data, code = _run(db, "unavailability", "remove", "9999")
    assert code != 0
    assert data["error"]["code"] == "unavailability.not_found"


def test_unavailability_add_duplicate_errors(tmp_path: Path) -> None:
    """Adding the same window twice for the same member triggers integrity."""
    db = tmp_path / "r.db"
    _seed_sp26(db)
    _run(db, "member", "add", "alice", "--display-name", "Alice")
    _run(
        db,
        "unavailability",
        "add",
        "alice",
        "--starts",
        "2026-03-01",
        "--ends",
        "2026-03-08",
    )
    data, code = _run(
        db,
        "unavailability",
        "add",
        "alice",
        "--starts",
        "2026-03-01",
        "--ends",
        "2026-03-08",
    )
    assert code != 0
    assert data["error"]["code"] in ("unavailability.duplicate", "unavailability.integrity")


# =====================================================================
# config/house.py
# =====================================================================


def test_house_add_duplicate_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _bare(db)
    _run(db, "config", "house", "add", "zta", "--display-name", "ZTA")
    data, code = _run(db, "config", "house", "add", "zta", "--display-name", "ZTA2")
    assert code != 0
    assert data["error"]["code"] == "house.integrity"


def test_house_set_pref_unknown_house_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _bare(db)
    data, code = _run(
        db,
        "config",
        "house",
        "set-pref",
        "ghost-house",
        "mixer",
        "door",
        "--min",
        "3",
        "--target",
        "4",
    )
    assert code != 0
    assert data["error"]["code"] == "house.not_found"


def test_house_set_pref_unknown_event_type_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _bare(db)
    _run(db, "config", "house", "add", "zta", "--display-name", "ZTA")
    data, code = _run(
        db,
        "config",
        "house",
        "set-pref",
        "zta",
        "no-such-event-type",
        "door",
        "--min",
        "3",
        "--target",
        "4",
    )
    assert code != 0
    assert data["error"]["code"] == "event_type.not_found"


def test_house_set_pref_unknown_shift_type_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _bare(db)
    _run(db, "config", "house", "add", "zta", "--display-name", "ZTA")
    data, code = _run(
        db,
        "config",
        "house",
        "set-pref",
        "zta",
        "mixer",
        "no-shift",
        "--min",
        "3",
        "--target",
        "4",
    )
    assert code != 0
    assert data["error"]["code"] == "shift_type.not_found"


def test_house_clear_pref_unknown_house_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _bare(db)
    data, code = _run(
        db,
        "config",
        "house",
        "clear-pref",
        "ghost",
        "mixer",
        "door",
    )
    assert code != 0
    assert data["error"]["code"] == "house.not_found"


def test_house_clear_pref_unknown_event_type_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _bare(db)
    _run(db, "config", "house", "add", "zta", "--display-name", "ZTA")
    data, code = _run(
        db,
        "config",
        "house",
        "clear-pref",
        "zta",
        "no-such-event-type",
        "door",
    )
    assert code != 0
    assert data["error"]["code"] == "event_type.not_found"


def test_house_clear_pref_unknown_shift_type_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _bare(db)
    _run(db, "config", "house", "add", "zta", "--display-name", "ZTA")
    data, code = _run(
        db,
        "config",
        "house",
        "clear-pref",
        "zta",
        "mixer",
        "no-shift",
    )
    assert code != 0
    assert data["error"]["code"] == "shift_type.not_found"


def test_house_list_prefs_unknown_house_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _bare(db)
    data, code = _run(db, "config", "house", "list-prefs", "ghost")
    assert code != 0
    assert data["error"]["code"] == "house.not_found"


def test_house_revert_prefs_unknown_house_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _bare(db)
    data, code = _run(
        db,
        "config",
        "house",
        "revert-prefs",
        "ghost",
        "--to",
        "2026-01-01T00:00:00Z",
    )
    assert code != 0
    assert data["error"]["code"] == "house.not_found"


def test_house_revert_prefs_apply_dry_run(tmp_path: Path) -> None:
    """revert-prefs --dry-run, after a real upsert, returns a plan."""
    db = tmp_path / "r.db"
    _bare(db)
    _run(db, "config", "house", "add", "zta", "--display-name", "ZTA")
    _run(
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
        "4",
    )
    # Now revert to the very beginning (snapshot is empty) - dry-run.
    data, code = _run(
        db,
        "config",
        "house",
        "revert-prefs",
        "zta",
        "--to",
        "1970-01-01T00:00:00Z",
        "--dry-run",
    )
    assert code == 0, data
    assert data["data"]["dry_run"] is True


def test_house_revert_prefs_apply_real(tmp_path: Path) -> None:
    """revert-prefs applies a real revert (delete branch)."""
    db = tmp_path / "r.db"
    _bare(db)
    _run(db, "config", "house", "add", "zta", "--display-name", "ZTA")
    _run(
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
        "4",
    )
    data, code = _run(
        db,
        "config",
        "house",
        "revert-prefs",
        "zta",
        "--to",
        "1970-01-01T00:00:00Z",
    )
    assert code == 0, data
    assert data["data"]["applied"] is True


def test_house_pref_history_unknown_house_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _bare(db)
    data, code = _run(db, "config", "house", "pref-history", "ghost")
    assert code != 0
    assert data["error"]["code"] == "house.not_found"


# =====================================================================
# config/event_type.py
# =====================================================================


def test_event_type_add_duplicate_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _bare(db)
    _run(db, "config", "event-type", "add", "social", "--display-name", "Social")
    data, code = _run(db, "config", "event-type", "add", "social", "--display-name", "Social")
    assert code != 0
    assert data["error"]["code"] == "event_type.integrity"


def test_event_type_allow_shift_type_unknown_event_type_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _bare(db)
    data, code = _run(
        db,
        "config",
        "event-type",
        "allow-shift-type",
        "no-event-type",
        "door",
    )
    assert code != 0
    assert data["error"]["code"] == "event_type.not_found"


def test_event_type_allow_shift_type_unknown_shift_type_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _bare(db)
    data, code = _run(
        db,
        "config",
        "event-type",
        "allow-shift-type",
        "mixer",
        "no-shift",
    )
    assert code != 0
    assert data["error"]["code"] == "shift_type.not_found"


def test_event_type_disallow_shift_type_unknown_event_type_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _bare(db)
    data, code = _run(
        db,
        "config",
        "event-type",
        "disallow-shift-type",
        "no-event-type",
        "door",
    )
    assert code != 0
    assert data["error"]["code"] == "event_type.not_found"


def test_event_type_disallow_shift_type_unknown_shift_type_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _bare(db)
    data, code = _run(
        db,
        "config",
        "event-type",
        "disallow-shift-type",
        "mixer",
        "no-shift",
    )
    assert code != 0
    assert data["error"]["code"] == "shift_type.not_found"


def test_event_type_set_default_unknown_event_type_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _bare(db)
    data, code = _run(
        db,
        "config",
        "event-type",
        "set-default",
        "no-event-type",
        "door",
        "--min",
        "3",
        "--target",
        "4",
    )
    assert code != 0
    assert data["error"]["code"] == "event_type.not_found"


def test_event_type_set_default_unknown_shift_type_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _bare(db)
    data, code = _run(
        db,
        "config",
        "event-type",
        "set-default",
        "mixer",
        "no-shift",
        "--min",
        "3",
        "--target",
        "4",
    )
    assert code != 0
    assert data["error"]["code"] == "shift_type.not_found"


def test_event_type_clear_default_unknown_event_type_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _bare(db)
    data, code = _run(
        db,
        "config",
        "event-type",
        "clear-default",
        "no-event-type",
        "door",
    )
    assert code != 0
    assert data["error"]["code"] == "event_type.not_found"


def test_event_type_clear_default_unknown_shift_type_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _bare(db)
    data, code = _run(
        db,
        "config",
        "event-type",
        "clear-default",
        "mixer",
        "no-shift",
    )
    assert code != 0
    assert data["error"]["code"] == "shift_type.not_found"


def test_event_type_show_unknown_event_type_errors(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    _bare(db)
    data, code = _run(db, "config", "event-type", "show", "no-event-type")
    assert code != 0
    assert data["error"]["code"] == "event_type.not_found"
