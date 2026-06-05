"""Two-process race: only one writer wins thanks to BEGIN IMMEDIATE +
the partial unique index on ``shifts``.

Spawns two ``risk event auto-assign`` invocations against the same DB and
verifies:
  - exactly one shift exists per (event, shift_type, slot) — no duplicates
  - both processes either succeed or one errors cleanly; no corrupted state
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from risk.db.connection import connect
from risk.db.schema import ensure_schema
from risk.repos import event_types as etypes_repo
from risk.repos import events as events_repo
from risk.repos import houses as houses_repo
from risk.repos import member_statuses as statuses_repo
from risk.repos import members as members_repo
from risk.repos import semesters as semesters_repo

pytestmark = pytest.mark.integration


def test_two_concurrent_auto_assigns_one_winner(tmp_path: Path) -> None:
    db_path = tmp_path / "race.db"
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
        )
    from risk.services import shift_requirements as svc

    svc.snapshot_for_event(conn, event_id)
    conn.close()

    risk_bin = Path(sys.executable).parent / "risk"
    procs = [
        subprocess.Popen(
            [
                str(risk_bin),
                "--db",
                str(db_path),
                "--json",
                "event",
                "auto-assign",
                "ZTA mixer",
                "--seed",
                str(seed),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for seed in (10, 20)
    ]
    outs = [p.communicate(timeout=15) for p in procs]
    codes = [p.returncode for p in procs]

    # The BEGIN IMMEDIATE serialization + 5s busy_timeout means typically both
    # succeed (the second waits, then sees existing shifts and preserves them
    # all). If both raced and one hit the partial unique index, we'd see one
    # `assignment.integrity` failure — also acceptable proof the index works.
    # Either way no double-assigns may have landed in the DB.
    assert all(code in (0, 1) for code in codes), (codes, outs)

    # The DB must now have at most one row per (event, shift_type, slot).
    conn = connect(db_path)
    duplicates = conn.execute(
        """
        SELECT event_id, shift_type_id, slot_index, COUNT(*) AS n
        FROM shifts
        GROUP BY event_id, shift_type_id, slot_index
        HAVING n > 1
        """
    ).fetchall()
    assert duplicates == []

    # And no member assigned twice to the same (event, shift_type).
    double_assigns = conn.execute(
        """
        SELECT event_id, shift_type_id, assigned_member_id, COUNT(*) AS n
        FROM shifts
        WHERE assigned_member_id IS NOT NULL
        GROUP BY event_id, shift_type_id, assigned_member_id
        HAVING n > 1
        """
    ).fetchall()
    assert double_assigns == []

    # At least one process succeeded — sanity-check the JSON output.
    success_envelopes = [
        json.loads(out[0]) for code, out in zip(codes, outs, strict=False) if code == 0
    ]
    assert success_envelopes, f"No process succeeded: {outs}"

    conn.close()
