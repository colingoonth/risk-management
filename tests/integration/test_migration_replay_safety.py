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
    sem_id = semesters_repo.insert(
        conn, name="FA26", starts_on="2026-08-25", ends_on="2026-12-05"
    )
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
            "SELECT name FROM sqlite_master "
            "WHERE type = 'index' AND tbl_name = 'unavailability'"
        )
    }
    assert "unavailability_member_semester_range" not in names
    assert "unavailability_member_semester_window" in names
    reopened.close()
