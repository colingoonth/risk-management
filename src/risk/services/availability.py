"""When is a member actually free to work a given shift type at a given event?

The primitive is ``free_intervals`` and it returns a LIST OF INTERVALS, not a
boolean. That shape is the whole design:

- rides / door / bar / dj (``min_contiguous_minutes IS NULL``) ask whether the
  whole window survived  → :func:`is_free_for_whole_window`
- setup asks whether any single free run is long enough
                         → :func:`has_contiguous_run`
- setup×4 and cleanup×4 must find a time when all four crew members are free
  SIMULTANEOUSLY. That solver is a later task, and it is only tractable if this
  function hands back intervals: intersecting four interval lists is easy,
  intersecting four booleans is impossible.

A boolean would throw away exactly the information the hard problem needs.

Scope: this module reads ``shift_type_windows`` and ``unavailability`` and
returns time. It knows nothing about assignment, fairness, eligibility, roles or
qualifications, and must stay that way.

Two rules that are easy to get wrong
------------------------------------

**Days are separate spans, never concatenated.** Setup's window is "a 2-hour
block in the 2 days before, or the day of". That is three separate 08:00–23:59
spans, and the 2-hour block has to fit inside ONE of them. Concatenating the
days would let a block start at 23:00 on Thursday and finish at 09:00 on Friday,
which is not a thing a person can do.

**``events.start_time`` is ignored on purpose.** The chair's rule is "on site by
8pm" whatever time the party actually starts. The window is the rule, not the
event's own clock.
"""

from __future__ import annotations

import sqlite3
from datetime import date as _date
from datetime import datetime, time, timedelta

from risk.repos import shift_type_windows as windows_repo
from risk.repos import unavailability as unav_repo
from risk.repos.shift_type_windows import ShiftTypeWindow
from risk.repos.unavailability import UnavailabilityWindow

Interval = tuple[datetime, datetime]
"""A half-open span of wall-clock time, ``[start, end)``."""


# ---------------------------------------------------------------------------
# Pure interval arithmetic. No database, no domain knowledge — easy to test and
# reusable by the group-overlap solver later.
# ---------------------------------------------------------------------------


def merge_intervals(intervals: list[Interval]) -> list[Interval]:
    """Merge overlapping or touching intervals into a minimal ordered set.

    Unavailability rows may overlap each other — "busy 09:00-13:00" plus "busy
    12:00-15:00". Subtracting them one at a time double-counts the overlap, so
    they are merged first.
    """
    ordered = sorted(i for i in intervals if i[1] > i[0])
    merged: list[Interval] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            prev_start, prev_end = merged[-1]
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def subtract(span: Interval, busy: list[Interval]) -> list[Interval]:
    """Remove ``busy`` from ``span``, returning what is left in order.

    ``busy`` need not be sorted, merged or clipped to the span. Zero-length
    results are dropped.
    """
    lo, hi = span
    if hi <= lo:
        return []
    free: list[Interval] = []
    cursor = lo
    for b_start, b_end in merge_intervals(busy):
        if b_end <= cursor:
            continue
        if b_start >= hi:
            break
        if b_start > cursor:
            free.append((cursor, min(b_start, hi)))
        cursor = max(cursor, b_end)
        if cursor >= hi:
            break
    if cursor < hi:
        free.append((cursor, hi))
    return [(a, b) for a, b in free if b > a]


def longest_free_run(intervals: list[Interval]) -> timedelta:
    """Longest single interval. Zero for an empty list.

    Deliberately the longest SINGLE interval rather than the total: two 1-hour
    gaps do not add up to a 2-hour block.
    """
    return max((end - start for start, end in intervals), default=timedelta(0))


def has_contiguous_run(intervals: list[Interval], *, minutes: int) -> bool:
    """Is there one unbroken free run of at least ``minutes``?"""
    return longest_free_run(intervals) >= timedelta(minutes=minutes)


# ---------------------------------------------------------------------------
# Resolving a shift-type window against a concrete event date.
# ---------------------------------------------------------------------------


def window_spans(window: ShiftTypeWindow, event_date: str | _date) -> list[Interval]:
    """Concrete spans this window covers for ``event_date`` — one per offset day.

    driver/door/bar/dj → one span, 20:00-23:59 on the event day.
    setup              → three spans, 08:00-23:59 on days -2, -1 and 0.
    cleanup            → one span, 00:00-12:00 the following morning.
    """
    day0 = _date.fromisoformat(event_date) if isinstance(event_date, str) else event_date
    start_t = time.fromisoformat(window.window_start_time)
    end_t = time.fromisoformat(window.window_end_time)
    spans: list[Interval] = []
    for offset in range(window.offset_days_start, window.offset_days_end + 1):
        day = day0 + timedelta(days=offset)
        spans.append((datetime.combine(day, start_t), datetime.combine(day, end_t)))
    return spans


def _applies_on(row: UnavailabilityWindow, day: _date) -> bool:
    """Does this unavailability row cover ``day``?

    A one-off row (``repeats_weekday IS NULL``) covers every day in
    ``starts_on..ends_on`` inclusive. A recurring row covers only days in that
    range whose weekday matches — so "every Thursday" blocks a Thursday event and
    leaves a Friday one alone, and stops mattering once the range has passed.
    """
    iso = day.isoformat()
    if not (row.starts_on <= iso <= row.ends_on):
        return False
    if row.repeats_weekday is None:
        return True
    return day.weekday() == row.repeats_weekday


def _busy_on(row: UnavailabilityWindow, day: _date) -> Interval:
    """The blocked span this row imposes on ``day``.

    Both times NULL means ALL DAY, which is midnight to midnight — not the
    window's own hours. Clipping to the span happens in :func:`subtract`.
    """
    if row.starts_at_time is None or row.ends_at_time is None:
        return (
            datetime.combine(day, time.min),
            datetime.combine(day + timedelta(days=1), time.min),
        )
    return (
        datetime.combine(day, time.fromisoformat(row.starts_at_time)),
        datetime.combine(day, time.fromisoformat(row.ends_at_time)),
    )


# ---------------------------------------------------------------------------
# The primitive.
# ---------------------------------------------------------------------------


def free_intervals(
    conn: sqlite3.Connection,
    *,
    member_id: int,
    semester_id: int,
    shift_type_id: int,
    event_date: str,
    honor_soft: bool = True,
) -> list[Interval]:
    """When ``member_id`` is free to work ``shift_type_id`` for an event on
    ``event_date``, as ordered non-overlapping intervals.

    ``honor_soft=False`` ignores preference rows and answers the narrower
    question "is this member genuinely unable to work". Assignment asks both:
    the hard answer decides who is in the pool at all, and the soft answer
    decides who gets picked last. They cannot be one call, because a preference
    that removed somebody from the pool would leave a post unstaffed rather than
    inconveniencing one runner — which is the wrong trade every time.

    Each returned interval lies inside a single offset day; intervals are never
    concatenated across a day boundary, because a shift cannot be.

    Raises ``LookupError`` if the shift type has no window row. Returning "free
    all the time" or "never free" would both be guesses, and a silently wrong
    availability answer is worse than a loud failure.
    """
    window = windows_repo.get_for_shift_type(conn, shift_type_id)
    if window is None:
        raise LookupError(
            f"shift type id={shift_type_id} has no row in shift_type_windows; "
            "availability cannot be computed for it"
        )
    rows = unav_repo.list_for_member_semester(conn, member_id=member_id, semester_id=semester_id)
    if not honor_soft:
        rows = [r for r in rows if not r.is_soft]

    free: list[Interval] = []
    for span in window_spans(window, event_date):
        day = span[0].date()
        busy = [_busy_on(r, day) for r in rows if _applies_on(r, day)]
        free.extend(subtract(span, busy))
    return free


# ---------------------------------------------------------------------------
# Thin layers over the primitive.
# ---------------------------------------------------------------------------


def is_free_for_whole_window(
    conn: sqlite3.Connection,
    *,
    member_id: int,
    semester_id: int,
    shift_type_id: int,
    event_date: str,
    honor_soft: bool = True,
) -> bool:
    """Did the entire window survive? The rule for driver/door/bar/dj."""
    window = windows_repo.get_for_shift_type(conn, shift_type_id)
    if window is None:
        raise LookupError(f"shift type id={shift_type_id} has no window row")
    return free_intervals(
        conn,
        member_id=member_id,
        semester_id=semester_id,
        shift_type_id=shift_type_id,
        event_date=event_date,
        honor_soft=honor_soft,
    ) == window_spans(window, event_date)


def can_cover(
    conn: sqlite3.Connection,
    *,
    member_id: int,
    semester_id: int,
    shift_type_id: int,
    event_date: str,
    honor_soft: bool = True,
) -> bool:
    """Can this member work this shift type at all, by that type's own rule?

    ``min_contiguous_minutes IS NULL`` means the member must hold the whole
    window (they are at the party for the night). A value means they only need
    one free run that long, inside one offset day.

    This answers "is it possible", NOT "should they be assigned" — that is
    assignment's job, and this module has no opinion on it.
    """
    window = windows_repo.get_for_shift_type(conn, shift_type_id)
    if window is None:
        raise LookupError(f"shift type id={shift_type_id} has no window row")
    intervals = free_intervals(
        conn,
        member_id=member_id,
        semester_id=semester_id,
        shift_type_id=shift_type_id,
        event_date=event_date,
        honor_soft=honor_soft,
    )
    if window.min_contiguous_minutes is None:
        return intervals == window_spans(window, event_date)
    return has_contiguous_run(intervals, minutes=window.min_contiguous_minutes)
