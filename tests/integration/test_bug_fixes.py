"""Regression tests for T1/T2 bug fixes.

Bug 1 — event add date validation crash
Bug 2 — event add with no shift requirements gives no warning
Bug 3 — unavailability add duplicate window
Bug 4 — swap request allows two open requests for same from_shift
Bug 5 — HUMAN mode raw dict output (set-host, swap request/cancel, unavail add)
"""

from __future__ import annotations

import json
import sqlite3
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
from risk.services import assignment, shift_requirements

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _run(db_path: Path, *args: str, mode: str = "--json") -> subprocess.CompletedProcess[str]:
    risk_bin = Path(sys.executable).parent / "risk"
    cmd = [str(risk_bin), "--db", str(db_path)]
    if mode:
        cmd.append(mode)
    cmd.extend(args)
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def _seed_members(db_path: Path) -> None:
    """Schema + SP26 current + alice + bob."""
    conn = connect(db_path)
    ensure_schema(conn)
    sem_id = semesters_repo.insert(conn, name="SP26", starts_on="2026-01-15", ends_on="2026-05-15")
    conn.execute("UPDATE semesters SET is_current = 1 WHERE id = ?", (sem_id,))
    active = statuses_repo.get_by_slug(conn, "active")
    assert active is not None
    members_repo.insert(
        conn, slug="alice", display_name="Alice", status_id=active.id, class_year=2027
    )
    members_repo.insert(conn, slug="bob", display_name="Bob", status_id=active.id, class_year=2027)
    conn.close()


def _seed_event(db_path: Path) -> int:
    """Schema + SP26 current + zta house + mixer event + 15 members auto-assigned."""
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
            conn, slug=f"m{i}", display_name=f"M {i}", status_id=active.id, class_year=2027
        )
    shift_requirements.snapshot_for_event(conn, event_id)
    with transaction(conn):
        assignment.auto_assign(conn, event_id=event_id, seed=42)
    conn.close()
    return event_id


# ---------------------------------------------------------------------------
# Bug 1 — Date validation: non-ISO date rejected before DB insert
# ---------------------------------------------------------------------------


class TestBug1DateValidation:
    def test_non_iso_date_rejected(self, tmp_path: Path) -> None:
        """'September 12' must be rejected with a clean error, not inserted."""
        db_path = tmp_path / "r.db"
        conn = connect(db_path)
        ensure_schema(conn)
        sem_id = semesters_repo.insert(
            conn, name="SP26", starts_on="2026-01-15", ends_on="2026-05-15"
        )
        conn.execute("UPDATE semesters SET is_current = 1 WHERE id = ?", (sem_id,))
        conn.close()

        res = _run(
            db_path,
            "event",
            "add",
            "--name",
            "Bad date event",
            "--type",
            "mixer",
            "--date",
            "September 12",
        )
        assert res.returncode != 0
        err = json.loads(res.stdout)["error"]
        assert err["code"] == "event.invalid_date"
        assert "September 12" in err["message"]
        assert "YYYY-MM-DD" in err["message"]

    def test_iso_date_accepted(self, tmp_path: Path) -> None:
        """A valid YYYY-MM-DD date creates the event without error."""
        db_path = tmp_path / "r.db"
        conn = connect(db_path)
        ensure_schema(conn)
        sem_id = semesters_repo.insert(
            conn, name="SP26", starts_on="2026-01-15", ends_on="2026-05-15"
        )
        conn.execute("UPDATE semesters SET is_current = 1 WHERE id = ?", (sem_id,))
        houses_repo.insert(conn, slug="zta", display_name="ZTA")
        conn.close()

        res = _run(
            db_path,
            "event",
            "add",
            "--name",
            "Good event",
            "--type",
            "mixer",
            "--host",
            "zta",
            "--date",
            "2026-09-12",
        )
        assert res.returncode == 0, res.stdout + res.stderr
        data = json.loads(res.stdout)["data"]
        assert data["event"]["date"] == "2026-09-12"

    def test_partial_iso_date_rejected(self, tmp_path: Path) -> None:
        """'2025-9-12' (missing zero-padding) is rejected."""
        db_path = tmp_path / "r.db"
        conn = connect(db_path)
        ensure_schema(conn)
        sem_id = semesters_repo.insert(
            conn, name="SP26", starts_on="2026-01-15", ends_on="2026-05-15"
        )
        conn.execute("UPDATE semesters SET is_current = 1 WHERE id = ?", (sem_id,))
        conn.close()

        res = _run(
            db_path,
            "event",
            "add",
            "--name",
            "E",
            "--type",
            "mixer",
            "--date",
            "2025-9-12",
        )
        assert res.returncode != 0
        err = json.loads(res.stdout)["error"]
        assert err["code"] == "event.invalid_date"


# ---------------------------------------------------------------------------
# Bug 2 — event add with no shift requirements warns; auto-assign with zero
#          assignments warns
# ---------------------------------------------------------------------------


class TestBug2NoShiftReqWarning:
    def _seed_empty_event_type(self, db_path: Path) -> None:
        """Seed a chair-added event type 'formal' with NO default shift requirements.

        The slug must be one the migrations do NOT seed — every seeded type
        carries defaults, so a seeded slug cannot exercise the warning. This
        stands in for any type the chair adds by hand and has not yet given
        counts to.
        """
        conn = connect(db_path)
        ensure_schema(conn)
        sem_id = semesters_repo.insert(
            conn, name="SP26", starts_on="2026-01-15", ends_on="2026-05-15"
        )
        conn.execute("UPDATE semesters SET is_current = 1 WHERE id = ?", (sem_id,))
        # Insert event type with no rows in event_type_shift_defaults.
        conn.execute("INSERT INTO event_types (slug, display_name) VALUES ('formal', 'Formal')")
        conn.close()

    def test_add_event_with_no_reqs_warns(self, tmp_path: Path) -> None:
        """Creating a 'formal' event with no shift defaults produces a warnings list."""
        db_path = tmp_path / "r.db"
        self._seed_empty_event_type(db_path)

        res = _run(
            db_path,
            "event",
            "add",
            "--name",
            "Formal 1",
            "--type",
            "formal",
            "--date",
            "2026-04-01",
        )
        assert res.returncode == 0, res.stdout + res.stderr
        data = json.loads(res.stdout)["data"]
        assert data["requirements"] == []
        assert len(data["warnings"]) > 0
        assert "formal" in data["warnings"][0]
        assert "no shift requirements" in data["warnings"][0]

    def test_add_event_with_reqs_no_warning(self, tmp_path: Path) -> None:
        """Creating a 'mixer' event (which has shift defaults) produces no warnings."""
        db_path = tmp_path / "r.db"
        conn = connect(db_path)
        ensure_schema(conn)
        sem_id = semesters_repo.insert(
            conn, name="SP26", starts_on="2026-01-15", ends_on="2026-05-15"
        )
        conn.execute("UPDATE semesters SET is_current = 1 WHERE id = ?", (sem_id,))
        houses_repo.insert(conn, slug="zta", display_name="ZTA")
        conn.close()

        res = _run(
            db_path,
            "event",
            "add",
            "--name",
            "ZTA mixer",
            "--type",
            "mixer",
            "--host",
            "zta",
            "--date",
            "2026-02-14",
        )
        assert res.returncode == 0, res.stdout + res.stderr
        data = json.loads(res.stdout)["data"]
        # mixer has shift defaults, so requirements should be non-empty
        assert len(data["requirements"]) > 0
        assert data["warnings"] == []

    def test_auto_assign_zero_assignments_warns(self, tmp_path: Path) -> None:
        """auto-assign on event with no shift requirements warns about empty result."""
        db_path = tmp_path / "r.db"
        self._seed_empty_event_type(db_path)
        # Add 5 members so we have a pool.
        conn = connect(db_path)
        active = statuses_repo.get_by_slug(conn, "active")
        assert active is not None
        for i in range(5):
            members_repo.insert(
                conn, slug=f"m{i}", display_name=f"M{i}", status_id=active.id, class_year=2027
            )
        # Look up the IDs we need.
        et = etypes_repo.get_by_slug(conn, "formal")
        assert et is not None
        sem = semesters_repo.get_by_name(conn, "SP26")
        assert sem is not None
        with transaction(conn):
            events_repo.insert(
                conn,
                semester_id=sem.id,
                event_type_id=et.id,
                display_name="Formal 1",
                date="2026-04-01",
            )
        conn.close()

        res = _run(db_path, "event", "auto-assign", "1")
        assert res.returncode == 0, res.stdout + res.stderr
        data = json.loads(res.stdout)["data"]
        assert data["assignments"] == []
        assert any("no shift requirements" in w for w in data["warnings"])


# ---------------------------------------------------------------------------
# Bug 3 — unavailability add duplicate window rejected
# ---------------------------------------------------------------------------


class TestBug3UnavailabilityDuplicate:
    def test_duplicate_window_rejected(self, tmp_path: Path) -> None:
        """Adding the same unavailability window twice returns error on second call."""
        db_path = tmp_path / "r.db"
        _seed_members(db_path)

        args = [
            "unavailability",
            "add",
            "alice",
            "--starts",
            "2026-10-15",
            "--ends",
            "2026-10-20",
            "--reason",
            "fall break",
        ]

        first = _run(db_path, *args)
        assert first.returncode == 0, first.stdout + first.stderr

        second = _run(db_path, *args)
        assert second.returncode != 0
        err = json.loads(second.stdout)["error"]
        assert err["code"] == "unavailability.duplicate"
        assert "alice" in err["message"]
        assert "2026-10-15" in err["message"]
        assert "2026-10-20" in err["message"]

    def test_different_dates_both_accepted(self, tmp_path: Path) -> None:
        """Different date ranges for the same member are both allowed."""
        db_path = tmp_path / "r.db"
        _seed_members(db_path)

        r1 = _run(
            db_path,
            "unavailability",
            "add",
            "alice",
            "--starts",
            "2026-10-15",
            "--ends",
            "2026-10-20",
        )
        r2 = _run(
            db_path,
            "unavailability",
            "add",
            "alice",
            "--starts",
            "2026-11-01",
            "--ends",
            "2026-11-05",
        )
        assert r1.returncode == 0, r1.stdout
        assert r2.returncode == 0, r2.stdout

    def test_same_dates_different_members_both_accepted(self, tmp_path: Path) -> None:
        """Same date range for different members is fine."""
        db_path = tmp_path / "r.db"
        _seed_members(db_path)

        r1 = _run(
            db_path,
            "unavailability",
            "add",
            "alice",
            "--starts",
            "2026-10-15",
            "--ends",
            "2026-10-20",
        )
        r2 = _run(
            db_path,
            "unavailability",
            "add",
            "bob",
            "--starts",
            "2026-10-15",
            "--ends",
            "2026-10-20",
        )
        assert r1.returncode == 0, r1.stdout
        assert r2.returncode == 0, r2.stdout


# ---------------------------------------------------------------------------
# Bug 4 — swap request duplicate open rejected
# ---------------------------------------------------------------------------


class TestBug4SwapDuplicate:
    def test_duplicate_open_swap_rejected(self, tmp_path: Path) -> None:
        """Two open swap requests for the same from_shift must be rejected."""
        db_path = tmp_path / "r.db"
        _seed_event(db_path)

        # Get two assigned shifts.
        shifts_res = _run(db_path, "shift", "list", "--status", "assigned")
        shifts = json.loads(shifts_res.stdout)["data"]
        assert len(shifts) >= 3, "Need at least 3 assigned shifts for this test"
        s1, s2, s3 = shifts[0], shifts[1], shifts[2]

        # First request succeeds.
        r1 = _run(
            db_path,
            "swap",
            "request",
            "--from-shift",
            str(s1["id"]),
            "--to-shift",
            str(s2["id"]),
        )
        assert r1.returncode == 0, r1.stdout + r1.stderr

        # Second request for the same from_shift must fail.
        r2 = _run(
            db_path,
            "swap",
            "request",
            "--from-shift",
            str(s1["id"]),
            "--to-shift",
            str(s3["id"]),
        )
        assert r2.returncode != 0
        err = json.loads(r2.stdout)["error"]
        assert err["code"] == "swap.duplicate_open"
        assert str(s1["id"]) in err["message"]

    def test_different_from_shifts_both_succeed(self, tmp_path: Path) -> None:
        """Different from_shifts can each have their own open swap request."""
        db_path = tmp_path / "r.db"
        _seed_event(db_path)

        shifts_res = _run(db_path, "shift", "list", "--status", "assigned")
        shifts = json.loads(shifts_res.stdout)["data"]
        assert len(shifts) >= 3
        s1, s2, s3 = shifts[0], shifts[1], shifts[2]

        r1 = _run(
            db_path,
            "swap",
            "request",
            "--from-shift",
            str(s1["id"]),
            "--to-shift",
            str(s2["id"]),
        )
        r2 = _run(
            db_path,
            "swap",
            "request",
            "--from-shift",
            str(s2["id"]),
            "--to-shift",
            str(s3["id"]),
        )
        assert r1.returncode == 0, r1.stdout
        assert r2.returncode == 0, r2.stdout

    def test_after_cancel_new_request_allowed(self, tmp_path: Path) -> None:
        """Cancelling the open request allows a new one for the same from_shift."""
        db_path = tmp_path / "r.db"
        _seed_event(db_path)

        shifts_res = _run(db_path, "shift", "list", "--status", "assigned")
        shifts = json.loads(shifts_res.stdout)["data"]
        s1, s2, s3 = shifts[0], shifts[1], shifts[2]

        r1 = _run(
            db_path,
            "swap",
            "request",
            "--from-shift",
            str(s1["id"]),
            "--to-shift",
            str(s2["id"]),
        )
        req_id = json.loads(r1.stdout)["data"]["swap_request"]["id"]

        cancel_res = _run(db_path, "swap", "cancel", str(req_id), "--yes")
        assert cancel_res.returncode == 0, cancel_res.stdout

        r2 = _run(
            db_path,
            "swap",
            "request",
            "--from-shift",
            str(s1["id"]),
            "--to-shift",
            str(s3["id"]),
        )
        assert r2.returncode == 0, r2.stdout + r2.stderr


def test_a_chair_override_survives_a_full_rebuild(db: sqlite3.Connection) -> None:
    """The gap that made every manual correction one rebuild from vanishing.

    The fill preserves whatever already holds a slot, so a per-event re-run
    always kept overrides. A full clear-and-rebuild did not, because it deletes
    every shift row first — and that is the operation actually reached for
    whenever anything upstream changes, which is constantly. FA26 had exactly
    one override, on the Wednesday rides, and the only thing standing between it
    and the next rebuild was a note asking somebody to put it back.
    """
    from risk.repos import event_types as etypes_repo
    from risk.repos import events as events_repo
    from risk.repos import member_statuses as statuses_repo
    from risk.repos import members as members_repo
    from risk.repos import semesters as semesters_repo
    from risk.repos import shifts as shifts_repo
    from risk.services import assignment, manual_assign
    from risk.services import shift_requirements as reqs_svc

    sem_id = semesters_repo.insert(db, name="FA26", starts_on="2026-08-20", ends_on="2026-12-19")
    active = statuses_repo.get_by_slug(db, "active")
    assert active is not None
    ids = [
        members_repo.insert(
            db,
            slug=f"brother-{i:02d}",
            display_name=f"Brother {i:02d}",
            status_id=active.id,
            class_year=2029,
        )
        for i in range(30)
    ]
    etype = etypes_repo.get_by_slug(db, "mixer")
    assert etype is not None
    with transaction(db):
        event_id = events_repo.insert(
            db,
            semester_id=sem_id,
            event_type_id=etype.id,
            display_name="Party",
            date="2026-09-04",
        )
        reqs_svc.snapshot_for_event(db, event_id)
        assignment.auto_assign(db, event_id=event_id, seed=1, commit=True)

    door = next(
        s
        for s in shifts_repo.list_for_event(db, event_id)
        if s.shift_type_slug == "door" and s.slot_index == 0
    )
    # Somebody not already standing a post at this party — the one-shift-per-
    # event rule applies to a chair override exactly as it does to the fill,
    # which is what the first draft of this test tripped over.
    taken = {
        s.assigned_member_id
        for s in shifts_repo.list_for_event(db, event_id)
        if s.assigned_member_id is not None
    }
    chosen = next(m for m in ids if m not in taken)
    with transaction(db):
        manual_assign.assign(db, shift_id=door.id, member_key=f"brother-{ids.index(chosen):02d}")

    after_override = shifts_repo.get_by_id(db, door.id)
    assert after_override is not None
    assert after_override.assigned_member_id == chosen
    assert after_override.chair_set is True

    # The full rebuild — the operation that used to discard it.
    with transaction(db):
        deleted, kept = manual_assign.clear_semester(db, semester_id=sem_id)
        assert kept == 1, "the override must not be deleted"
        assert deleted > 0
    with transaction(db):
        assignment.auto_assign_semester(db, semester_id=sem_id, seed=1, commit=True)

    survivor = next(
        s
        for s in shifts_repo.list_for_event(db, event_id)
        if s.shift_type_slug == "door" and s.slot_index == 0
    )
    assert survivor.assigned_member_id == chosen, (
        "a full rebuild overwrote a decision the solver cannot re-derive"
    )
    assert survivor.chair_set is True
    open_slots = db.execute(
        "SELECT COUNT(*) AS n FROM shifts WHERE assigned_member_id IS NULL"
    ).fetchone()["n"]
    assert open_slots == 0, "the rest of the event must still fill around it"


def test_a_manual_assign_is_still_subject_to_physics(db: sqlite3.Connection) -> None:
    """An override decides WHO, not whether the choice is possible.

    Skipping the checks here would make the override the one path capable of
    producing an unworkable schedule — and it is the path a chair uses when in
    a hurry.
    """
    from risk.repos import event_types as etypes_repo
    from risk.repos import events as events_repo
    from risk.repos import member_statuses as statuses_repo
    from risk.repos import members as members_repo
    from risk.repos import semesters as semesters_repo
    from risk.repos import shift_types as stypes_repo
    from risk.repos import shifts as shifts_repo
    from risk.repos import unavailability as unav_repo
    from risk.services import manual_assign
    from risk.services import shift_requirements as reqs_svc

    sem_id = semesters_repo.insert(db, name="FA26", starts_on="2026-08-20", ends_on="2026-12-19")
    active = statuses_repo.get_by_slug(db, "active")
    assert active is not None
    away = members_repo.insert(
        db, slug="away-guy", display_name="Away Guy", status_id=active.id, class_year=2029
    )
    etype = etypes_repo.get_by_slug(db, "mixer")
    assert etype is not None
    with transaction(db):
        event_id = events_repo.insert(
            db,
            semester_id=sem_id,
            event_type_id=etype.id,
            display_name="Party",
            date="2026-09-04",
        )
        reqs_svc.snapshot_for_event(db, event_id)
        unav_repo.insert(
            db,
            member_id=away,
            semester_id=sem_id,
            starts_on="2026-09-03",
            ends_on="2026-09-05",
            reason="tournament",
        )
        # `shifts` rows are created lazily by the fill, so an unassigned event
        # has none at all and there is no slot to override. One open row is
        # enough here; running the whole fill would just pick somebody else.
        door_type = stypes_repo.get_by_slug(db, "door")
        assert door_type is not None
        shifts_repo.insert_open(db, event_id=event_id, shift_type_id=door_type.id, slot_index=0)
    door = next(
        s
        for s in shifts_repo.list_for_event(db, event_id)
        if s.shift_type_slug == "door" and s.slot_index == 0
    )

    with pytest.raises(ValueError, match="unavailable"), transaction(db):
        manual_assign.assign(db, shift_id=door.id, member_key="away-guy")

    # force lands it, but hands back the objection rather than swallowing it.
    with transaction(db):
        result = manual_assign.assign(db, shift_id=door.id, member_key="away-guy", force=True)
    assert result.warnings, "an overridden objection must still be reported"
    assert "unavailable" in result.warnings[0]
