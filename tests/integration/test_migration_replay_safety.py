"""A database that accepted real data must still open on the next connect.

There is no ``_schema_migrations`` table: ``ensure_schema`` replays every
migration file on EVERY connection. That makes replay-safety a property of the
data, not just of the schema — a migration can be perfectly idempotent against
an empty database and still abort against a populated one.

Migration 0011 added ``UNIQUE(member_id, semester_id, starts_on, ends_on)`` on
``unavailability``. Migration 0012 replaced it with a wider index including the
time and recurrence columns, precisely so a member can say "busy 09:00-11:00"
AND "busy 18:00-22:00" on the same date. But 0011 still ran first on every
connect and recreated the narrow index, so the second such row made the
database permanently unopenable: every subsequent connect raised
``IntegrityError`` from inside ``ensure_schema``, with the chapter's real data
locked inside.

These tests connect twice with data in between, which is the only way this class
of defect shows up.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from risk.db.connection import connect, transaction
from risk.db.schema import ensure_schema
from risk.repos import member_statuses as statuses_repo
from risk.repos import members as members_repo
from risk.repos import semesters as semesters_repo
from risk.repos import unavailability as unav_repo

pytestmark = pytest.mark.integration


def _seed_member(conn: sqlite3.Connection) -> tuple[int, int]:
    sem_id = semesters_repo.insert(conn, name="FA26", starts_on="2026-08-25", ends_on="2026-12-05")
    active = statuses_repo.get_by_slug(conn, "active")
    assert active is not None
    member_id = members_repo.insert(
        conn, slug="replay-tester", display_name="Replay Tester", status_id=active.id
    )
    conn.commit()
    return member_id, sem_id


def test_two_timed_windows_on_one_date_survive_a_reconnect(tmp_path: Path) -> None:
    """The headline case: the exact pair of rows migration 0012 exists to allow."""
    path = tmp_path / "replay.db"
    conn = connect(path)
    ensure_schema(conn)
    member_id, sem_id = _seed_member(conn)
    with transaction(conn):
        unav_repo.insert(
            conn,
            member_id=member_id,
            semester_id=sem_id,
            starts_on="2026-10-03",
            ends_on="2026-10-03",
            starts_at_time="09:00",
            ends_at_time="11:00",
            reason="morning class",
        )
        unav_repo.insert(
            conn,
            member_id=member_id,
            semester_id=sem_id,
            starts_on="2026-10-03",
            ends_on="2026-10-03",
            starts_at_time="18:00",
            ends_at_time="22:00",
            reason="evening shift",
        )
    conn.close()

    reopened = connect(path)
    ensure_schema(reopened)  # this raised IntegrityError before the fix
    assert len(unav_repo.list_for_semester(reopened, semester_id=sem_id)) == 2
    reopened.close()


def test_recurring_and_one_off_on_the_same_range_survive_a_reconnect(
    tmp_path: Path,
) -> None:
    """Same collision via the other v2 column: one-off vs weekly over one range."""
    path = tmp_path / "replay_weekday.db"
    conn = connect(path)
    ensure_schema(conn)
    member_id, sem_id = _seed_member(conn)
    with transaction(conn):
        unav_repo.insert(
            conn,
            member_id=member_id,
            semester_id=sem_id,
            starts_on="2026-08-25",
            ends_on="2026-12-05",
            reason="away all term",
        )
        unav_repo.insert(
            conn,
            member_id=member_id,
            semester_id=sem_id,
            starts_on="2026-08-25",
            ends_on="2026-12-05",
            repeats_weekday=3,
            reason="lab every Thursday",
        )
    conn.close()

    reopened = connect(path)
    ensure_schema(reopened)
    assert len(unav_repo.list_for_semester(reopened, semester_id=sem_id)) == 2
    reopened.close()


def test_the_widened_unique_index_still_rejects_a_true_duplicate(
    tmp_path: Path,
) -> None:
    """De-dup must still work — 0011's purpose survives, only its scope changed."""
    path = tmp_path / "replay_dupe.db"
    conn = connect(path)
    ensure_schema(conn)
    member_id, sem_id = _seed_member(conn)
    with transaction(conn):
        unav_repo.insert(
            conn,
            member_id=member_id,
            semester_id=sem_id,
            starts_on="2026-10-03",
            ends_on="2026-10-03",
            starts_at_time="09:00",
            ends_at_time="11:00",
        )
    with pytest.raises(sqlite3.IntegrityError), transaction(conn):
        unav_repo.insert(
            conn,
            member_id=member_id,
            semester_id=sem_id,
            starts_on="2026-10-03",
            ends_on="2026-10-03",
            starts_at_time="09:00",
            ends_at_time="11:00",
        )
    conn.close()


def test_the_superseded_narrow_index_is_gone_after_a_replay(tmp_path: Path) -> None:
    """It must not come back on a later connect either."""
    path = tmp_path / "replay_index.db"
    conn = connect(path)
    ensure_schema(conn)
    conn.close()

    reopened = connect(path)
    ensure_schema(reopened)
    names = {
        r["name"]
        for r in reopened.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'unavailability'"
        )
    }
    assert "unavailability_member_semester_range" not in names
    assert "unavailability_member_semester_window" in names
    reopened.close()


def test_planning_status_back_fill_does_not_revert_a_chair_edit(tmp_path: Path) -> None:
    """0015 derives ``planning_status`` from notes ONCE, never again.

    This is the 0013 trap in a new place. The FA26 load wrote the value as
    ``status=placeholder`` inside ``events.notes``, and 0015 parses it out. But
    every migration replays on every connect, so an unguarded UPDATE would
    re-derive from notes forever — and a chair promoting a placeholder to a real
    party would watch it silently revert on their next command, with the notes
    field still saying ``status=placeholder`` and nothing anywhere explaining
    why the fill order had not changed.

    The guard is ``WHERE planning_status IS NULL``, which is true exactly once
    per row in the life of a database.
    """
    from risk.repos import event_types as etypes_repo
    from risk.repos import events as events_repo

    path = tmp_path / "planning.db"
    conn = connect(path)
    ensure_schema(conn)
    sem_id = semesters_repo.insert(conn, name="FA26", starts_on="2026-08-25", ends_on="2026-12-05")
    etype = etypes_repo.get_by_slug(conn, "mixer")
    assert etype is not None
    with transaction(conn):
        event_id = events_repo.insert(
            conn,
            semester_id=sem_id,
            event_type_id=etype.id,
            display_name="Held date",
            date="2026-09-18",
            notes="status=placeholder; social chair is holding this one",
            planning_status="placeholder",
        )
    conn.close()

    # The chair confirms the party.
    conn = connect(path)
    ensure_schema(conn)
    with transaction(conn):
        events_repo.update_planning_status(conn, event_id=event_id, planning_status="confirmed")
    conn.close()

    # Two further connects, i.e. two further full replays of 0015.
    for _ in range(2):
        conn = connect(path)
        ensure_schema(conn)
        event = events_repo.get_by_id(conn, event_id)
        assert event is not None
        assert event.planning_status == "confirmed", (
            "0015 re-derived planning_status from notes and reverted the chair's edit"
        )
        # The notes still say placeholder — that is the whole point. The value
        # was seeded from notes, then became independent of it.
        assert event.notes is not None and "status=placeholder" in event.notes
        conn.close()


def test_planning_status_rejects_a_value_the_fill_order_cannot_read(
    tmp_path: Path,
) -> None:
    """Legacy databases get this column by ALTER TABLE and carry no CHECK.

    ``schema._ensure_columns`` adds it as plain nullable TEXT because SQLite
    cannot attach a CHECK via ALTER, so on Colin's real database the repo guard
    is the only thing standing between a typo and an event that sorts into
    neither the confirmed pass nor the placeholder pass.
    """
    from risk.repos import event_types as etypes_repo
    from risk.repos import events as events_repo

    conn = connect(tmp_path / "planning-check.db")
    ensure_schema(conn)
    sem_id = semesters_repo.insert(conn, name="FA26", starts_on="2026-08-25", ends_on="2026-12-05")
    etype = etypes_repo.get_by_slug(conn, "mixer")
    assert etype is not None
    with pytest.raises(ValueError, match="planning_status"):
        events_repo.insert(
            conn,
            semester_id=sem_id,
            event_type_id=etype.id,
            display_name="Typo",
            date="2026-09-18",
            planning_status="placehodler",
        )
    conn.close()
