"""Per-entity repository for ``auto_assign_runs`` (audit of auto-assign invocations)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AutoAssignRun:
    id: int
    event_id: int
    run_at: str
    resolved_mode_id: int
    resolved_mode_slug: str
    eligible_pledges_at_run: int
    eligible_brothers_at_run: int
    seed: int
    payload_json: str | None


def insert(
    conn: sqlite3.Connection,
    *,
    event_id: int,
    resolved_mode_id: int,
    eligible_pledges_at_run: int,
    eligible_brothers_at_run: int,
    seed: int,
    payload_json: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO auto_assign_runs
          (event_id, resolved_mode_id, eligible_pledges_at_run,
           eligible_brothers_at_run, seed, payload_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            resolved_mode_id,
            eligible_pledges_at_run,
            eligible_brothers_at_run,
            seed,
            payload_json,
        ),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def list_for_event(conn: sqlite3.Connection, event_id: int) -> list[AutoAssignRun]:
    rows = conn.execute(
        """
        SELECT
          r.id, r.event_id, r.run_at, r.resolved_mode_id,
          pm.slug AS resolved_mode_slug,
          r.eligible_pledges_at_run, r.eligible_brothers_at_run,
          r.seed, r.payload_json
        FROM auto_assign_runs r
        JOIN pledge_modes pm ON pm.id = r.resolved_mode_id
        WHERE r.event_id = ?
        ORDER BY r.id DESC
        """,
        (event_id,),
    ).fetchall()
    return [
        AutoAssignRun(
            id=r["id"],
            event_id=r["event_id"],
            run_at=r["run_at"],
            resolved_mode_id=r["resolved_mode_id"],
            resolved_mode_slug=r["resolved_mode_slug"],
            eligible_pledges_at_run=r["eligible_pledges_at_run"],
            eligible_brothers_at_run=r["eligible_brothers_at_run"],
            seed=r["seed"],
            payload_json=r["payload_json"],
        )
        for r in rows
    ]
