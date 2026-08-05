"""``GET /events/summary`` — every event in a term with its staffing rollup.

The calendar paints 43 cells before the chair clicks anything, so it needs targets
and assigned counts in one round trip. Two things about that are easy to get
wrong, and both are load-bearing enough to test directly:

1. **The denominator cannot come from ``shifts``.** Those rows are created lazily
   by auto-assign, so an event nobody has assigned yet has none at all. Counting
   them would report 0 of 0 — which reads as "fully staffed" in any fraction or
   fill bar — for exactly the events that need attention most.

2. **Targets and assignments must not multiply.** Joining requirements and shifts
   in one pass fans out: five requirement rows times thirteen shift rows reports
   65 of 169 for a mixer that is honestly 13 of 13. That shape gets the unstaffed
   case right by luck, which is how it survives a casual test and ships.

A separate module from ``test_api.py`` on purpose — four tests there assert exact
counts against its shared ``_seed``, so adding events to it would break them.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from risk.api import create_app
from risk.db.connection import connect, transaction
from risk.repos import event_shift_requirements as req_repo
from risk.repos import event_types as etypes_repo
from risk.repos import events as events_repo
from risk.repos import houses as houses_repo
from risk.repos import member_qualifications as mq_repo
from risk.repos import member_statuses as statuses_repo
from risk.repos import members as members_repo
from risk.repos import qualifications as quals_repo
from risk.repos import semesters as semesters_repo
from risk.repos import shift_types as stypes_repo
from risk.repos import shifts as shifts_repo
from risk.services import assignment as assign_svc
from risk.services import shift_requirements as reqs_svc

pytestmark = pytest.mark.integration

SEM_START = "2026-08-25"
SEM_END = "2026-12-05"


def _event(
    conn: sqlite3.Connection,
    *,
    semester_id: int,
    type_slug: str,
    name: str,
    date: str,
) -> int:
    et = etypes_repo.get_by_slug(conn, type_slug)
    assert et is not None, f"event type {type_slug!r} not seeded"
    event_id = events_repo.insert(
        conn,
        semester_id=semester_id,
        event_type_id=et.id,
        display_name=name,
        date=date,
        host_house_id=None,
    )
    reqs_svc.snapshot_for_event(conn, event_id)
    return event_id


def _seed(db_path: Path) -> dict[str, int]:
    conn = connect(db_path)
    ensure = __import__("risk.db.schema", fromlist=["ensure_schema"]).ensure_schema
    ensure(conn)
    ids: dict[str, int] = {}
    with transaction(conn):
        sem_id = semesters_repo.insert(
            conn, name="FA26", starts_on=SEM_START, ends_on=SEM_END
        )
        conn.execute("UPDATE semesters SET is_current = 1 WHERE id = ?", (sem_id,))
        houses_repo.insert(conn, slug="summary-house", display_name="Summary House")
        active = statuses_repo.get_by_slug(conn, "active")
        assert active is not None
        member_ids = [
            members_repo.insert(
                conn,
                slug=f"sum-{i:02d}",
                display_name=f"Summary Member {i:02d}",
                status_id=active.id,
                class_year=2027,
            )
            for i in range(1, 21)
        ]
        # Somebody has to be able to DJ, or the gated dj slot never fills and
        # "Full Mixer" comes back 12/13 — the qualification filter working, not a
        # rollup bug. Two DJs, mirroring the real chapter.
        dj_qual = quals_repo.get_by_slug(conn, "dj")
        assert dj_qual is not None
        for member_id in member_ids[:2]:
            mq_repo.grant(
                conn,
                member_id=member_id,
                qualification_id=dj_qual.id,
                semester_id=sem_id,
            )
        ids["never"] = _event(
            conn, semester_id=sem_id, type_slug="mixer",
            name="Never Assigned Mixer", date="2026-09-11",
        )
        ids["full"] = _event(
            conn, semester_id=sem_id, type_slug="mixer",
            name="Full Mixer", date="2026-09-12",
        )
        ids["orphan"] = _event(
            conn, semester_id=sem_id, type_slug="mixer",
            name="Orphan Mixer", date="2026-09-25",
        )
        ids["cancelled"] = _event(
            conn, semester_id=sem_id, type_slug="krush",
            name="Dead Krush", date="2026-10-31",
        )
        ids["same_night"] = _event(
            conn, semester_id=sem_id, type_slug="mixer",
            name="Same Night", date="2026-10-31",
        )
        # No snapshot at all — an event carrying zero requirement rows.
        bare_type = etypes_repo.get_by_slug(conn, "philanthropy")
        assert bare_type is not None
        ids["bare"] = events_repo.insert(
            conn,
            semester_id=sem_id,
            event_type_id=bare_type.id,
            display_name="Bare Event",
            date="2026-11-07",
            host_house_id=None,
        )
        # A second term, to prove scoping.
        other = semesters_repo.insert(
            conn, name="SP27", starts_on="2027-01-10", ends_on="2027-05-10"
        )
        ids["other_semester"] = _event(
            conn, semester_id=other, type_slug="mixer",
            name="Next Term Mixer", date="2027-02-14",
        )
    ids["semester"] = sem_id

    # Fill: 'full' to completion, 'cancelled' partially, then cancel it.
    with transaction(conn):
        assign_svc.auto_assign(conn, event_id=ids["full"], seed=1)
    with transaction(conn):
        assign_svc.auto_assign(conn, event_id=ids["orphan"], seed=1)
    with transaction(conn):
        assign_svc.auto_assign(conn, event_id=ids["cancelled"], seed=1)
        events_repo.update_status(conn, event_id=ids["cancelled"], status="cancelled")

    # Now lower a requirement UNDER the assignments already made, manufacturing
    # the orphan case: assigned will exceed target for this event.
    setup = stypes_repo.get_by_slug(conn, "setup")
    assert setup is not None
    with transaction(conn):
        req_repo.upsert(
            conn,
            event_id=ids["orphan"],
            shift_type_id=setup.id,
            min_count=0,
            target_count=0,
            source_layer="manual_override",
        )
    conn.close()
    return ids


@pytest.fixture()
def world(tmp_path: Path) -> Iterator[tuple[TestClient, dict[str, int], Path]]:
    db_path = tmp_path / "summary.db"
    ids = _seed(db_path)
    app = create_app(db_path=db_path)
    with TestClient(app) as client:
        yield client, ids, db_path


def _by_id(payload: list[dict]) -> dict[int, dict]:
    return {row["id"]: row for row in payload}


def test_unstaffed_event_reads_zero_of_target_not_zero_of_zero(
    world: tuple[TestClient, dict[str, int], Path],
) -> None:
    """The one that matters most: no shift rows must not mean no requirement."""
    client, ids, _ = world
    # Pin the premise, so this test cannot quietly stop testing what it claims.
    assert client.get(f"/api/events/{ids['never']}/shifts").json() == []

    row = _by_id(client.get("/api/events/summary").json())[ids["never"]]
    assert row["assigned_slots"] == 0
    assert row["target_slots"] > 0, (
        "an event that has never been auto-assigned still has a real target; "
        "0/0 would render as fully staffed"
    )
    assert row["open_slots"] == row["target_slots"]
    assert row["orphan_slots"] == 0


def test_fully_staffed_event_does_not_double_count(
    world: tuple[TestClient, dict[str, int], Path],
) -> None:
    """A one-pass join fans out to 65/169 here. This is that regression guard."""
    client, ids, _ = world
    row = _by_id(client.get("/api/events/summary").json())[ids["full"]]
    assert row["assigned_slots"] == row["target_slots"]
    assert row["open_slots"] == 0
    assert row["target_slots"] < 30, (
        f"target {row['target_slots']} is fanned out — a mixer is ~13 slots"
    )


def test_summary_matches_the_slow_per_event_computation(
    world: tuple[TestClient, dict[str, int], Path],
) -> None:
    """Recompute every event the obvious way and demand the same answer.

    Catches every wrong join shape at once and keeps working as the fixture grows.
    """
    client, ids, db_path = world
    payload = _by_id(client.get("/api/events/summary").json())
    conn = connect(db_path)
    try:
        for event_id, row in payload.items():
            target = sum(r.target_count for r in req_repo.list_for_event(conn, event_id))
            assigned = sum(
                1
                for s in shifts_repo.list_for_event(conn, event_id)
                if s.assigned_member_id is not None
            )
            assert row["target_slots"] == target, f"target mismatch on event {event_id}"
            assert row["assigned_slots"] == assigned, f"assigned mismatch on {event_id}"
    finally:
        conn.close()
    _ = ids


def test_orphaned_assignments_are_surfaced_not_clamped(
    world: tuple[TestClient, dict[str, int], Path],
) -> None:
    """Lowering a target below its assignments must not read as 'nothing to do'."""
    client, ids, _ = world
    row = _by_id(client.get("/api/events/summary").json())[ids["orphan"]]
    assert row["assigned_slots"] > row["target_slots"], "fixture should be orphaned"
    assert row["orphan_slots"] == row["assigned_slots"] - row["target_slots"]
    assert row["open_slots"] == 0, "open_slots is clamped; a negative shortfall is not a thing"


def test_cancelled_events_are_returned_with_their_numbers(
    world: tuple[TestClient, dict[str, int], Path],
) -> None:
    """Omitting them would hide a night the chair remembers scheduling.

    Returning 0/0 would make a cancelled event indistinguishable from an
    unconfigured one. Status is the discriminator; the client dims and strikes it.
    """
    client, ids, _ = world
    row = _by_id(client.get("/api/events/summary").json())[ids["cancelled"]]
    assert row["status"] == "cancelled"
    assert row["target_slots"] > 0


def test_event_with_no_requirements_reads_zero_of_zero(
    world: tuple[TestClient, dict[str, int], Path],
) -> None:
    """'Nothing is required' is a real state, distinct from 'nothing is staffed'."""
    client, ids, _ = world
    row = _by_id(client.get("/api/events/summary").json())[ids["bare"]]
    assert (row["target_slots"], row["assigned_slots"], row["open_slots"]) == (0, 0, 0)


def test_summary_is_scoped_to_one_semester(
    world: tuple[TestClient, dict[str, int], Path],
) -> None:
    client, ids, _ = world
    payload = _by_id(client.get("/api/events/summary").json())
    assert ids["other_semester"] not in payload
    assert client.get("/api/events/summary?semester=SP27").status_code == 200
    assert ids["other_semester"] in _by_id(
        client.get("/api/events/summary?semester=SP27").json()
    )


def test_by_type_breaks_the_rollup_down_per_shift_type(
    world: tuple[TestClient, dict[str, int], Path],
) -> None:
    """The calendar needs per-type counts to place the cleanup crew on day +1."""
    client, ids, _ = world
    row = _by_id(client.get("/api/events/summary").json())[ids["never"]]
    by_type = {g["shift_type_slug"]: g for g in row["by_type"]}
    assert "cleanup" in by_type, "a mixer carries a cleanup crew"
    assert by_type["cleanup"]["target_count"] > 0
    assert by_type["cleanup"]["assigned_count"] == 0
    assert sum(g["target_count"] for g in row["by_type"]) == row["target_slots"], (
        "the per-type breakdown must reconcile with the rollup"
    )


def test_summary_route_is_not_swallowed_by_the_event_id_route(
    world: tuple[TestClient, dict[str, int], Path],
) -> None:
    """/events/summary must not be parsed as /events/{event_id}.

    FastAPI matches in declaration order, so this 422s if someone moves the
    handler below GET /{event_id}.
    """
    client, _, _ = world
    assert client.get("/api/events/summary").status_code == 200


def test_summary_is_two_queries_not_a_loop_over_events(
    world: tuple[TestClient, dict[str, int], Path],
) -> None:
    """The rollup must not reintroduce the dashboard's per-event N+1."""
    _, ids, db_path = world
    conn = connect(db_path)
    statements: list[str] = []
    try:
        conn.set_trace_callback(statements.append)
        events_repo.list_for_semester_with_fill(conn, ids["semester"])
        assert len(statements) == 1, f"expected 1 statement, traced {len(statements)}"
        statements.clear()
        req_repo.list_slot_groups_for_semester(conn, ids["semester"])
        assert len(statements) == 1, f"expected 1 statement, traced {len(statements)}"
    finally:
        conn.set_trace_callback(None)
        conn.close()


def test_shift_type_windows_expose_the_cleanup_offset(
    world: tuple[TestClient, dict[str, int], Path],
) -> None:
    """The calendar reads +1 from the data rather than hardcoding it."""
    client, _, _ = world
    windows = {w["shift_type_slug"]: w for w in client.get("/api/shift-type-windows").json()}
    assert windows["cleanup"]["offset_days_start"] == 1
    assert windows["cleanup"]["offset_days_end"] == 1
    assert windows["setup"]["offset_days_start"] != windows["setup"]["offset_days_end"], (
        "setup spans days — it is a task with a deadline, not an appointment"
    )
    assert windows["door"]["offset_days_start"] == 0
