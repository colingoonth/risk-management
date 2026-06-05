"""Three-layer shift-requirement merge + snapshot service.

Precedence (high → low):
  1. ``event_shift_requirements`` rows tagged ``manual_override`` in the audit
     table (per-event explicit edits the chair made).
  2. ``house_shift_preferences`` for the event's ``host_house_id``.
  3. ``event_type_shift_defaults`` for the event's ``event_type_id``.

Resync semantics: re-run the merge for an event, preserving rows whose latest
audit row has ``source_layer = 'manual_override'``. Replace all others with the
freshly-merged values, writing audit rows tagged ``resync``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from risk.repos import event_shift_requirements as req_repo
from risk.repos import event_type_shift_defaults as defaults_repo
from risk.repos import events as events_repo
from risk.repos import house_shift_preferences as prefs_repo


@dataclass(frozen=True, slots=True)
class MergedRow:
    shift_type_id: int
    shift_type_slug: str
    min_count: int
    target_count: int
    source_layer: str


def compute_merge(
    conn: sqlite3.Connection,
    *,
    event_type_id: int,
    host_house_id: int | None,
) -> list[MergedRow]:
    """Pure-ish merge — does NOT consider manual overrides or existing snapshot.

    Used both for initial snapshot (at event creation) and for the "default"
    side of resync. Manual overrides are layered in by the caller via
    ``snapshot_for_event`` / ``resync_event``.
    """
    defaults = defaults_repo.list_for_event_type(conn, event_type_id)
    merged: dict[int, MergedRow] = {
        d.shift_type_id: MergedRow(
            shift_type_id=d.shift_type_id,
            shift_type_slug=d.shift_type_slug,
            min_count=d.min_count,
            target_count=d.target_count,
            source_layer="event_type_default",
        )
        for d in defaults
    }
    if host_house_id is not None:
        for p in prefs_repo.list_for_house(conn, host_house_id):
            if p.event_type_id != event_type_id:
                continue
            merged[p.shift_type_id] = MergedRow(
                shift_type_id=p.shift_type_id,
                shift_type_slug=p.shift_type_slug,
                min_count=p.min_count,
                target_count=p.target_count,
                source_layer="house_preference",
            )
    return sorted(merged.values(), key=lambda r: r.shift_type_slug)


def snapshot_for_event(conn: sqlite3.Connection, event_id: int) -> list[MergedRow]:
    """Materialize ``event_shift_requirements`` from the merge result.

    Called by ``risk event add``. Writes one state row + one audit row per
    shift type the merged result contains. Caller must wrap in a transaction.
    """
    event = events_repo.get_by_id(conn, event_id)
    if event is None:
        raise LookupError(f"event {event_id} not found")
    merged = compute_merge(
        conn,
        event_type_id=event.event_type_id,
        host_house_id=event.host_house_id,
    )
    for row in merged:
        req_repo.upsert(
            conn,
            event_id=event_id,
            shift_type_id=row.shift_type_id,
            min_count=row.min_count,
            target_count=row.target_count,
            source_layer=row.source_layer,
        )
    return merged


def resync_semester(
    conn: sqlite3.Connection, *, semester_id: int
) -> dict[int, int]:
    """Run ``resync_event`` for every non-terminal event in a semester.

    Caller wraps in transaction. Returns {event_id: rows_rewritten}.
    """
    event_ids = [
        int(r["id"])
        for r in conn.execute(
            """
            SELECT id FROM events
            WHERE semester_id = ? AND status NOT IN ('completed', 'cancelled')
            ORDER BY date, id
            """,
            (semester_id,),
        ).fetchall()
    ]
    return {eid: len(resync_event(conn, eid)) for eid in event_ids}


def resync_event(conn: sqlite3.Connection, event_id: int) -> list[MergedRow]:
    """Re-pull defaults + house prefs; preserve manual overrides.

    For every (event, shift_type) where the latest audit row has
    ``source_layer = 'manual_override'``, leave the state row alone. For
    everything else (and for new shift types now present in defaults/prefs),
    write the merged value with ``source_layer = 'resync'``.

    Also clears the event's ``resync_pending`` flag. Caller wraps in transaction.
    """
    event = events_repo.get_by_id(conn, event_id)
    if event is None:
        raise LookupError(f"event {event_id} not found")

    existing = {
        r.shift_type_id: req_repo.latest_source_layer(
            conn, event_id=event_id, shift_type_id=r.shift_type_id
        )
        for r in req_repo.list_for_event(conn, event_id)
    }
    merged = compute_merge(
        conn,
        event_type_id=event.event_type_id,
        host_house_id=event.host_house_id,
    )
    written: list[MergedRow] = []
    for row in merged:
        if existing.get(row.shift_type_id) == "manual_override":
            continue
        req_repo.upsert(
            conn,
            event_id=event_id,
            shift_type_id=row.shift_type_id,
            min_count=row.min_count,
            target_count=row.target_count,
            source_layer="resync",
        )
        written.append(
            MergedRow(
                shift_type_id=row.shift_type_id,
                shift_type_slug=row.shift_type_slug,
                min_count=row.min_count,
                target_count=row.target_count,
                source_layer="resync",
            )
        )
    events_repo.clear_resync_pending(conn, event_id)
    return written


def apply_manual_override(
    conn: sqlite3.Connection,
    *,
    event_id: int,
    shift_type_id: int,
    min_count: int,
    target_count: int,
) -> None:
    """Chair-edit a single requirement. Caller wraps in transaction."""
    req_repo.upsert(
        conn,
        event_id=event_id,
        shift_type_id=shift_type_id,
        min_count=min_count,
        target_count=target_count,
        source_layer="manual_override",
    )


def clear_requirement(conn: sqlite3.Connection, *, event_id: int, shift_type_id: int) -> int:
    """Delete the requirement row entirely (cascade-deletes its audit rows)."""
    return req_repo.delete(conn, event_id=event_id, shift_type_id=shift_type_id)
