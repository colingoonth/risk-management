"""Per-entity repository for ``shift_type_windows`` (seeded in migration 0012).

WHEN a shift type is worked, relative to the event date. One row per shift type;
the availability service turns a row plus an event date into concrete spans.

``occupies_event_night`` is a flag rather than something inferred from the times,
and that is deliberate: setup's window is ~72 hours wide and therefore
numerically overlaps the party, but doing setup at 2pm Thursday does not stop you
working door at 10pm Friday. Overlap arithmetic alone would wrongly block that
pairing.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ShiftTypeWindow:
    shift_type_id: int
    shift_type_slug: str
    offset_days_start: int
    offset_days_end: int
    window_start_time: str
    window_end_time: str
    min_contiguous_minutes: int | None
    occupies_event_night: bool
    requires_group_overlap: bool


_SELECT_JOINED = """
SELECT
  w.shift_type_id, st.slug AS shift_type_slug,
  w.offset_days_start, w.offset_days_end,
  w.window_start_time, w.window_end_time,
  w.min_contiguous_minutes, w.occupies_event_night, w.requires_group_overlap
FROM shift_type_windows w
JOIN shift_types st ON st.id = w.shift_type_id
"""


def _row(r: sqlite3.Row) -> ShiftTypeWindow:
    return ShiftTypeWindow(
        shift_type_id=r["shift_type_id"],
        shift_type_slug=r["shift_type_slug"],
        offset_days_start=r["offset_days_start"],
        offset_days_end=r["offset_days_end"],
        window_start_time=r["window_start_time"],
        window_end_time=r["window_end_time"],
        min_contiguous_minutes=r["min_contiguous_minutes"],
        occupies_event_night=bool(r["occupies_event_night"]),
        requires_group_overlap=bool(r["requires_group_overlap"]),
    )


def get_for_shift_type(
    conn: sqlite3.Connection, shift_type_id: int
) -> ShiftTypeWindow | None:
    row = conn.execute(
        f"{_SELECT_JOINED} WHERE w.shift_type_id = ?", (shift_type_id,)
    ).fetchone()
    return _row(row) if row else None


def get_by_slug(conn: sqlite3.Connection, slug: str) -> ShiftTypeWindow | None:
    row = conn.execute(f"{_SELECT_JOINED} WHERE st.slug = ?", (slug,)).fetchone()
    return _row(row) if row else None


def list_all(conn: sqlite3.Connection) -> list[ShiftTypeWindow]:
    rows = conn.execute(f"{_SELECT_JOINED} ORDER BY st.slug").fetchall()
    return [_row(r) for r in rows]
