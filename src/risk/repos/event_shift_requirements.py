"""Repo for ``event_shift_requirements`` (state) and ``event_shift_requirement_writes`` (audit).

ADR-010: state row stores min/target/keys only; the audit table tracks
provenance and history (``source_layer``).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EventShiftRequirement:
    event_id: int
    shift_type_id: int
    shift_type_slug: str
    min_count: int
    target_count: int


@dataclass(frozen=True, slots=True)
class RequirementWithSource(EventShiftRequirement):
    source_layer: str
    written_at: str


@dataclass(frozen=True, slots=True)
class SlotGroup:
    """Target vs assigned for one (event, shift_type) pair."""

    event_id: int
    shift_type_slug: str
    target_count: int
    assigned_count: int


def list_slot_groups_for_semester(
    conn: sqlite3.Connection, semester_id: int
) -> list[SlotGroup]:
    """Per-(event, shift type) targets and assignments across a whole semester.

    The calendar needs this to know that *this* mixer wants *four* cleanup slots —
    a semester-wide scalar cannot say which shift types an event actually carries,
    and cleanup is worked the morning AFTER the party, so the calendar has to place
    it on a different day from the event itself.

    Driven from ``events CROSS JOIN shift_types`` rather than from
    ``event_shift_requirements``, so a shift type holding assigned shifts but no
    requirement row — the orphaned lowered-target case — is still reported, at
    n/0. Driving from the requirements table would hide exactly the rows a chair
    most needs to see.

    The WHERE clause keeps the cross join honest: a pair is returned only if it
    has a target or has shifts, so this is ~5 rows per event, not 6 x every type.
    """
    rows = conn.execute(
        """
        SELECT
          e.id AS event_id,
          st.slug AS shift_type_slug,
          COALESCE(r.target_count, 0) AS target_count,
          (SELECT COUNT(*)
             FROM shifts sh
            WHERE sh.event_id = e.id
              AND sh.shift_type_id = st.id
              AND sh.assigned_member_id IS NOT NULL) AS assigned_count
        FROM events e
        CROSS JOIN shift_types st
        LEFT JOIN event_shift_requirements r
               ON r.event_id = e.id AND r.shift_type_id = st.id
        WHERE e.semester_id = ?
          AND (
            COALESCE(r.target_count, 0) > 0
            OR EXISTS (SELECT 1 FROM shifts s2
                        WHERE s2.event_id = e.id AND s2.shift_type_id = st.id)
          )
        ORDER BY e.id, st.id
        """,
        (semester_id,),
    ).fetchall()
    return [
        SlotGroup(
            event_id=int(r["event_id"]),
            shift_type_slug=r["shift_type_slug"],
            target_count=int(r["target_count"]),
            assigned_count=int(r["assigned_count"]),
        )
        for r in rows
    ]


def upsert(
    conn: sqlite3.Connection,
    *,
    event_id: int,
    shift_type_id: int,
    min_count: int,
    target_count: int,
    source_layer: str,
) -> None:
    """Upsert the state row AND append an audit row."""
    conn.execute(
        """
        INSERT INTO event_shift_requirements
          (event_id, shift_type_id, min_count, target_count)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (event_id, shift_type_id) DO UPDATE SET
          min_count = excluded.min_count,
          target_count = excluded.target_count
        """,
        (event_id, shift_type_id, min_count, target_count),
    )
    conn.execute(
        """
        INSERT INTO event_shift_requirement_writes
          (event_id, shift_type_id, source_layer, min_count, target_count)
        VALUES (?, ?, ?, ?, ?)
        """,
        (event_id, shift_type_id, source_layer, min_count, target_count),
    )


def delete(conn: sqlite3.Connection, *, event_id: int, shift_type_id: int) -> int:
    cur = conn.execute(
        """
        DELETE FROM event_shift_requirements
        WHERE event_id = ? AND shift_type_id = ?
        """,
        (event_id, shift_type_id),
    )
    return cur.rowcount


def list_for_event(conn: sqlite3.Connection, event_id: int) -> list[EventShiftRequirement]:
    rows = conn.execute(
        """
        SELECT
          r.event_id, r.shift_type_id, st.slug AS shift_type_slug,
          r.min_count, r.target_count
        FROM event_shift_requirements r
        JOIN shift_types st ON st.id = r.shift_type_id
        WHERE r.event_id = ?
        ORDER BY st.slug
        """,
        (event_id,),
    ).fetchall()
    return [
        EventShiftRequirement(
            event_id=r["event_id"],
            shift_type_id=r["shift_type_id"],
            shift_type_slug=r["shift_type_slug"],
            min_count=r["min_count"],
            target_count=r["target_count"],
        )
        for r in rows
    ]


def list_for_event_with_source(
    conn: sqlite3.Connection, event_id: int
) -> list[RequirementWithSource]:
    """Join state with the *latest* audit row per (event, shift_type).

    Tie-break on ``id`` (monotonic) since ``written_at`` uses second precision
    and seed-time bulk inserts can share a timestamp.
    """
    rows = conn.execute(
        """
        SELECT
          r.event_id, r.shift_type_id, st.slug AS shift_type_slug,
          r.min_count, r.target_count,
          w.source_layer, w.written_at
        FROM event_shift_requirements r
        JOIN shift_types st ON st.id = r.shift_type_id
        LEFT JOIN event_shift_requirement_writes w ON w.id = (
          SELECT id FROM event_shift_requirement_writes
          WHERE event_id = r.event_id AND shift_type_id = r.shift_type_id
          ORDER BY id DESC
          LIMIT 1
        )
        WHERE r.event_id = ?
        ORDER BY st.slug
        """,
        (event_id,),
    ).fetchall()
    return [
        RequirementWithSource(
            event_id=r["event_id"],
            shift_type_id=r["shift_type_id"],
            shift_type_slug=r["shift_type_slug"],
            min_count=r["min_count"],
            target_count=r["target_count"],
            source_layer=r["source_layer"] or "unknown",
            written_at=r["written_at"] or "",
        )
        for r in rows
    ]


def latest_source_layer(
    conn: sqlite3.Connection, *, event_id: int, shift_type_id: int
) -> str | None:
    """Return the most recent ``source_layer`` for this (event, shift_type)."""
    row = conn.execute(
        """
        SELECT source_layer
        FROM event_shift_requirement_writes
        WHERE event_id = ? AND shift_type_id = ?
        ORDER BY written_at DESC, id DESC
        LIMIT 1
        """,
        (event_id, shift_type_id),
    ).fetchone()
    return row["source_layer"] if row else None


def writes_history_for_event(conn: sqlite3.Connection, event_id: int) -> list[dict[str, object]]:
    rows = conn.execute(
        """
        SELECT id, event_id, shift_type_id, written_at, source_layer, min_count, target_count
        FROM event_shift_requirement_writes
        WHERE event_id = ?
        ORDER BY written_at DESC, id DESC
        """,
        (event_id,),
    ).fetchall()
    return [dict(r) for r in rows]
