"""Integration tests for Phase 6 unavailability: CRUD + eligibility filter."""

from __future__ import annotations

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
from risk.repos import unavailability as unav_repo
from risk.services import availability, eligibility

pytestmark = pytest.mark.integration


def _world(tmp_path: Path):
    conn = connect(tmp_path / "p6.db")
    ensure_schema(conn)
    sem_id = semesters_repo.insert(conn, name="SP26", starts_on="2026-01-15", ends_on="2026-05-15")
    with transaction(conn):
        semesters_repo.set_current(conn, "SP26")
    active = statuses_repo.get_by_slug(conn, "active")
    assert active is not None
    alice = members_repo.insert(conn, slug="alice", display_name="Alice", status_id=active.id)
    bob = members_repo.insert(conn, slug="bob", display_name="Bob", status_id=active.id)
    return conn, sem_id, alice, bob


def test_insert_and_lookup_by_date(tmp_path: Path) -> None:
    conn, sem_id, alice, _bob = _world(tmp_path)
    with transaction(conn):
        unav_repo.insert(
            conn,
            member_id=alice,
            semester_id=sem_id,
            starts_on="2026-02-10",
            ends_on="2026-02-15",
            reason="exam week",
        )
    # On the start day, on the end day, and inside the range — all hit.
    for d in ["2026-02-10", "2026-02-12", "2026-02-15"]:
        ids = unav_repo.member_ids_unavailable_on(conn, semester_id=sem_id, date=d)
        assert alice in ids, f"alice should be unavailable on {d}"
    # Before and after — miss.
    for d in ["2026-02-09", "2026-02-16"]:
        ids = unav_repo.member_ids_unavailable_on(conn, semester_id=sem_id, date=d)
        assert alice not in ids, f"alice should NOT be unavailable on {d}"


def test_eligibility_excludes_unavailable_member(tmp_path: Path) -> None:
    conn, sem_id, alice, _bob = _world(tmp_path)
    etypes_repo.insert(conn, slug="testtype", display_name="Test")
    et = etypes_repo.get_by_slug(conn, "testtype")
    assert et is not None
    zta = houses_repo.insert(conn, slug="zta", display_name="ZTA")
    events_repo.insert(
        conn,
        semester_id=sem_id,
        event_type_id=et.id,
        display_name="Test event",
        date="2026-02-14",
        host_house_id=zta,
    )
    with transaction(conn):
        unav_repo.insert(
            conn,
            member_id=alice,
            semester_id=sem_id,
            starts_on="2026-02-13",
            ends_on="2026-02-15",
        )
    # eligible_for answers "may this PERSON work this event" and no longer
    # touches unavailability at all — it cannot, because it is asked once for
    # the whole event and two of the six shift types are not worked on the
    # event's date. Alice stays in the pool here.
    pool = eligibility.eligible_for(
        conn,
        event_id=1,
        semester_id=sem_id,
        host_house_id=zta,
    )
    assert any(m.member_id == alice for m in pool.eligible)

    # The exclusion now happens per shift type, against that type's own window.
    # Alice is blacked out 13-15 Feb, so she cannot work the party night...
    door = stypes_repo.get_by_slug(conn, "door")
    assert door is not None
    assert not availability.can_cover(
        conn,
        member_id=alice,
        semester_id=sem_id,
        shift_type_id=door.id,
        event_date="2026-02-14",
    )
    # ...nor the cleanup crew, which is worked the morning of the 15th and is
    # still inside her window. Under the old date-only rule this second case was
    # invisible: the shift is stored against the 14th.
    cleanup = stypes_repo.get_by_slug(conn, "cleanup")
    assert cleanup is not None
    assert not availability.can_cover(
        conn,
        member_id=alice,
        semester_id=sem_id,
        shift_type_id=cleanup.id,
        event_date="2026-02-14",
    )
    # But a party on the 16th has a setup window of the 14th-16th, and she is
    # free from the 16th, so she can still take a 2h setup block.
    setup = stypes_repo.get_by_slug(conn, "setup")
    assert setup is not None
    assert availability.can_cover(
        conn,
        member_id=alice,
        semester_id=sem_id,
        shift_type_id=setup.id,
        event_date="2026-02-16",
    )


def test_unavailability_is_per_semester(tmp_path: Path) -> None:
    conn, sem_a, alice, _bob = _world(tmp_path)
    sem_b = semesters_repo.insert(conn, name="FA26", starts_on="2026-08-15", ends_on="2026-12-15")
    with transaction(conn):
        unav_repo.insert(
            conn,
            member_id=alice,
            semester_id=sem_a,
            starts_on="2026-02-10",
            ends_on="2026-02-15",
        )
    # Spring window does NOT bleed into fall semester even on a date inside the range.
    fall_ids = unav_repo.member_ids_unavailable_on(conn, semester_id=sem_b, date="2026-02-12")
    assert alice not in fall_ids


def test_invalid_range_rejected(tmp_path: Path) -> None:
    import sqlite3

    conn, sem_id, alice, _bob = _world(tmp_path)
    with pytest.raises(sqlite3.IntegrityError), transaction(conn):
        unav_repo.insert(
            conn,
            member_id=alice,
            semester_id=sem_id,
            starts_on="2026-02-15",
            ends_on="2026-02-10",  # ends < starts
        )
