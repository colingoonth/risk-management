"""Regression tests for the strike-dedup guard in ``services.strike_state``.

Fix 7: issuing a second active strike on the same shift_id for the same
member must raise ``ValueError`` rather than silently creating a duplicate.
"""

from __future__ import annotations

import sqlite3

import pytest

from risk.db.connection import connect, transaction
from risk.db.schema import ensure_schema
from risk.repos import member_statuses as statuses_repo
from risk.repos import members as members_repo
from risk.repos import semesters as semesters_repo
from risk.services.strike_state import issue_strike


def _make_db(tmp_path) -> tuple[sqlite3.Connection, int, int, int]:
    """Return (conn, member_id, semester_id) with FK constraints ON."""
    db_path = tmp_path / "dedup.db"
    conn = connect(db_path)
    ensure_schema(conn)

    sem_id = semesters_repo.insert(conn, name="SP26", starts_on="2026-01-15", ends_on="2026-05-15")
    active = statuses_repo.get_by_slug(conn, "active")
    assert active is not None
    mid = members_repo.insert(conn, slug="alice", display_name="Alice", status_id=active.id)

    # Insert a minimal shift row. To satisfy all FKs we must also insert
    # an event_type, a shift_type, and an event.  We bypass the CLI and write
    # directly so this stays a pure unit test with no subprocess overhead.
    conn.execute("INSERT INTO event_types (slug, display_name) VALUES ('social', 'Social')")
    conn.execute(
        "INSERT INTO shift_types (slug, display_name) VALUES ('sober-monitor', 'Sober Monitor')"
    )
    et_id = conn.execute("SELECT id FROM event_types WHERE slug='social'").fetchone()["id"]
    st_id = conn.execute("SELECT id FROM shift_types WHERE slug='sober-monitor'").fetchone()["id"]
    conn.execute(
        """
        INSERT INTO events (semester_id, event_type_id, display_name, date, status)
        VALUES (?, ?, 'Test Event', '2026-02-14', 'created')
        """,
        (sem_id, et_id),
    )
    ev_id = conn.execute("SELECT id FROM events ORDER BY id DESC LIMIT 1").fetchone()["id"]
    # Assign the member to the shift so assigned_member_id != NULL.
    conn.execute(
        """
        INSERT INTO shifts (event_id, shift_type_id, slot_index,
                            assigned_member_id, status, assigned_at)
        VALUES (?, ?, 0, ?, 'assigned', '2026-01-01')
        """,
        (ev_id, st_id, mid),
    )
    shift_id = conn.execute("SELECT id FROM shifts ORDER BY id DESC LIMIT 1").fetchone()["id"]
    return conn, mid, sem_id, shift_id


def test_dedup_guard_raises_on_second_issue(tmp_path) -> None:
    """A second active strike on the same shift_id must raise ValueError."""
    conn, mid, sem_id, shift_id = _make_db(tmp_path)

    with transaction(conn):
        issue_strike(
            conn,
            member_id=mid,
            semester_id=sem_id,
            issued_on="2026-02-14",
            reason="no-show",
            shift_id=shift_id,
        )

    with pytest.raises(ValueError, match="active strike already exists"), transaction(conn):
        issue_strike(
            conn,
            member_id=mid,
            semester_id=sem_id,
            issued_on="2026-02-14",
            reason="no-show",
            shift_id=shift_id,
        )


def test_dedup_guard_allows_null_shift_id(tmp_path) -> None:
    """Two strikes with shift_id=None (manual strikes) must both succeed."""
    conn, mid, sem_id, _shift_id = _make_db(tmp_path)

    with transaction(conn):
        r1 = issue_strike(
            conn,
            member_id=mid,
            semester_id=sem_id,
            issued_on="2026-02-14",
            reason="conduct",
            shift_id=None,
        )
    with transaction(conn):
        r2 = issue_strike(
            conn,
            member_id=mid,
            semester_id=sem_id,
            issued_on="2026-02-15",
            reason="conduct",
            shift_id=None,
        )
    assert r1.strike_id != r2.strike_id


def test_dedup_guard_allows_different_shifts(tmp_path) -> None:
    """Strikes on different shift_ids for the same member must both succeed."""
    conn, mid, sem_id, shift_id = _make_db(tmp_path)

    # Insert a second event + shift row (same event would violate the
    # shifts_one_assignment partial unique index for the same member+shift_type).
    et_id = conn.execute("SELECT id FROM event_types WHERE slug='social'").fetchone()["id"]
    st_id = conn.execute("SELECT id FROM shift_types WHERE slug='sober-monitor'").fetchone()["id"]
    conn.execute(
        """
        INSERT INTO events (semester_id, event_type_id, display_name, date, status)
        VALUES (?, ?, 'Test Event 2', '2026-02-21', 'created')
        """,
        (sem_id, et_id),
    )
    ev_id2 = conn.execute("SELECT id FROM events ORDER BY id DESC LIMIT 1").fetchone()["id"]
    conn.execute(
        """
        INSERT INTO shifts (event_id, shift_type_id, slot_index,
                            assigned_member_id, status, assigned_at)
        VALUES (?, ?, 0, ?, 'assigned', '2026-01-01')
        """,
        (ev_id2, st_id, mid),
    )
    shift_id2 = conn.execute("SELECT id FROM shifts ORDER BY id DESC LIMIT 1").fetchone()["id"]

    with transaction(conn):
        r1 = issue_strike(
            conn,
            member_id=mid,
            semester_id=sem_id,
            issued_on="2026-02-14",
            reason="no-show",
            shift_id=shift_id,
        )
    with transaction(conn):
        r2 = issue_strike(
            conn,
            member_id=mid,
            semester_id=sem_id,
            issued_on="2026-02-15",
            reason="no-show",
            shift_id=shift_id2,
        )
    assert r1.strike_id != r2.strike_id


def test_dedup_guard_allows_after_removal(tmp_path) -> None:
    """After removing the original strike, a new one for the same shift is allowed."""
    from risk.repos import strikes as strike_repo

    conn, mid, sem_id, shift_id = _make_db(tmp_path)

    with transaction(conn):
        r1 = issue_strike(
            conn,
            member_id=mid,
            semester_id=sem_id,
            issued_on="2026-02-14",
            reason="no-show",
            shift_id=shift_id,
        )

    # Close the first strike directly (simulating a removal).
    strike_repo.close(conn, r1.strike_id, closed_at="2026-02-20")

    # Now issuing on the same shift_id must succeed.
    with transaction(conn):
        r2 = issue_strike(
            conn,
            member_id=mid,
            semester_id=sem_id,
            issued_on="2026-02-28",
            reason="no-show",
            shift_id=shift_id,
        )
    assert r2.strike_id != r1.strike_id
