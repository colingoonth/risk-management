"""Unit tests for ``services.availability``.

The nine cases below are the ones that actually decide whether the scheduler is
correct. The tricky three are recurring-weekday matching, the setup block having
to fit inside a SINGLE day, and overlapping unavailability rows needing to be
merged before subtraction rather than subtracted one at a time.

Dates are pinned to real FA26 weekdays:
    2026-10-08 is a Thursday, 2026-10-09 a Friday, 2026-09-11 a Friday.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest

from risk.repos import member_statuses as statuses_repo
from risk.repos import members as members_repo
from risk.repos import semesters as semesters_repo
from risk.repos import shift_types as stypes_repo
from risk.repos import unavailability as unav_repo
from risk.services import availability

pytestmark = pytest.mark.integration

FRIDAY = "2026-09-11"
THURSDAY = "2026-10-08"
FRIDAY_AFTER_THURSDAY = "2026-10-09"


def _dt(day: str, hhmm: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{hhmm}")


def _plus_days(day: str, n: int) -> str:
    return (datetime.fromisoformat(day) + timedelta(days=n)).date().isoformat()


@pytest.fixture()
def world(db: sqlite3.Connection) -> tuple[int, int]:
    """A semester and one member. Returns (member_id, semester_id)."""
    sem_id = semesters_repo.insert(db, name="FA26", starts_on="2026-08-25", ends_on="2026-12-05")
    active = statuses_repo.get_by_slug(db, "active")
    assert active is not None
    member_id = members_repo.insert(
        db, slug="alice", display_name="Alice", status_id=active.id, class_year=2027
    )
    db.commit()
    return member_id, sem_id


def _shift_type_id(db: sqlite3.Connection, slug: str) -> int:
    st = stypes_repo.get_by_slug(db, slug)
    assert st is not None, f"shift type {slug!r} not seeded"
    return st.id


def _busy(
    db: sqlite3.Connection,
    world: tuple[int, int],
    *,
    starts_on: str,
    ends_on: str | None = None,
    starts_at_time: str | None = None,
    ends_at_time: str | None = None,
    repeats_weekday: int | None = None,
) -> None:
    member_id, sem_id = world
    unav_repo.insert(
        db,
        member_id=member_id,
        semester_id=sem_id,
        starts_on=starts_on,
        ends_on=ends_on or starts_on,
        starts_at_time=starts_at_time,
        ends_at_time=ends_at_time,
        repeats_weekday=repeats_weekday,
    )
    db.commit()


def _free(
    db: sqlite3.Connection, world: tuple[int, int], slug: str, event_date: str
) -> list[availability.Interval]:
    member_id, sem_id = world
    return availability.free_intervals(
        db,
        member_id=member_id,
        semester_id=sem_id,
        shift_type_id=_shift_type_id(db, slug),
        event_date=event_date,
    )


# ---------------------------------------------------------------------------
# 1. No unavailability → the full window comes back.
# ---------------------------------------------------------------------------


def test_no_unavailability_returns_full_window(
    db: sqlite3.Connection, world: tuple[int, int]
) -> None:
    assert _free(db, world, "door", FRIDAY) == [(_dt(FRIDAY, "20:00"), _dt(FRIDAY, "23:59"))]


def test_setup_returns_one_span_per_offset_day(
    db: sqlite3.Connection, world: tuple[int, int]
) -> None:
    """Setup spans days -2..0 — three separate spans, never concatenated."""
    got = _free(db, world, "setup", FRIDAY)
    assert got == [
        (_dt(_plus_days(FRIDAY, -2), "08:00"), _dt(_plus_days(FRIDAY, -2), "23:59")),
        (_dt(_plus_days(FRIDAY, -1), "08:00"), _dt(_plus_days(FRIDAY, -1), "23:59")),
        (_dt(FRIDAY, "08:00"), _dt(FRIDAY, "23:59")),
    ]


# ---------------------------------------------------------------------------
# 2. All-day on the event date → door empty, cleanup UNAFFECTED (it is the
#    next day). This is the case that catches treating "unavailable on the
#    event date" as unavailable for every shift type.
# ---------------------------------------------------------------------------


def test_all_day_blocks_door_but_not_cleanup(
    db: sqlite3.Connection, world: tuple[int, int]
) -> None:
    _busy(db, world, starts_on=FRIDAY)  # no times = all day
    assert _free(db, world, "door", FRIDAY) == []
    assert _free(db, world, "cleanup", FRIDAY) == [
        (_dt(_plus_days(FRIDAY, 1), "00:00"), _dt(_plus_days(FRIDAY, 1), "12:00"))
    ]


# ---------------------------------------------------------------------------
# 3. Partial evening conflict trims the window rather than removing it.
# ---------------------------------------------------------------------------


def test_evening_conflict_leaves_exact_remainder(
    db: sqlite3.Connection, world: tuple[int, int]
) -> None:
    _busy(db, world, starts_on=FRIDAY, starts_at_time="18:00", ends_at_time="22:00")
    assert _free(db, world, "door", FRIDAY) == [(_dt(FRIDAY, "22:00"), _dt(FRIDAY, "23:59"))]


# ---------------------------------------------------------------------------
# 4. Recurring weekday matching.
# ---------------------------------------------------------------------------


def test_recurring_weekday_blocks_matching_day_only(
    db: sqlite3.Connection, world: tuple[int, int]
) -> None:
    """ "Every Thursday 18:00-23:00" blocks a Thursday, not the Friday after."""
    _busy(
        db,
        world,
        starts_on="2026-08-25",
        ends_on="2026-12-05",
        starts_at_time="18:00",
        ends_at_time="23:00",
        repeats_weekday=3,  # Thursday
    )
    assert _free(db, world, "door", THURSDAY) == [(_dt(THURSDAY, "23:00"), _dt(THURSDAY, "23:59"))]
    assert _free(db, world, "door", FRIDAY_AFTER_THURSDAY) == [
        (
            _dt(FRIDAY_AFTER_THURSDAY, "20:00"),
            _dt(FRIDAY_AFTER_THURSDAY, "23:59"),
        )
    ]


# ---------------------------------------------------------------------------
# 5. A recurring row whose weekday matches but whose range has ended does
#    nothing. Recurrence is bounded by starts_on..ends_on, not infinite.
# ---------------------------------------------------------------------------


def test_recurring_weekday_outside_date_range_has_no_effect(
    db: sqlite3.Connection, world: tuple[int, int]
) -> None:
    _busy(
        db,
        world,
        starts_on="2026-08-25",
        ends_on="2026-09-30",  # ends before THURSDAY (2026-10-08)
        starts_at_time="18:00",
        ends_at_time="23:00",
        repeats_weekday=3,
    )
    assert _free(db, world, "door", THURSDAY) == [(_dt(THURSDAY, "20:00"), _dt(THURSDAY, "23:59"))]


# ---------------------------------------------------------------------------
# 6. The setup minimum is a CONTIGUOUS run, and it must fit inside one day.
# ---------------------------------------------------------------------------


def _leave_gap_every_setup_day(
    db: sqlite3.Connection, world: tuple[int, int], gap_end: str
) -> None:
    """Busy 08:00-20:00 and gap_end-23:59 on each of the three setup days."""
    first, last = _plus_days(FRIDAY, -2), FRIDAY
    _busy(db, world, starts_on=first, ends_on=last, starts_at_time="08:00", ends_at_time="20:00")
    _busy(db, world, starts_on=first, ends_on=last, starts_at_time=gap_end, ends_at_time="23:59")


def test_setup_90_minute_gap_fails_120_minute_minimum(
    db: sqlite3.Connection, world: tuple[int, int]
) -> None:
    _leave_gap_every_setup_day(db, world, "21:30")  # 20:00-21:30 = 90 min
    intervals = _free(db, world, "setup", FRIDAY)
    assert availability.longest_free_run(intervals) == timedelta(minutes=90)
    assert not availability.has_contiguous_run(intervals, minutes=120)
    member_id, sem_id = world
    assert not availability.can_cover(
        db,
        member_id=member_id,
        semester_id=sem_id,
        shift_type_id=_shift_type_id(db, "setup"),
        event_date=FRIDAY,
    )


def test_setup_150_minute_gap_passes_120_minute_minimum(
    db: sqlite3.Connection, world: tuple[int, int]
) -> None:
    _leave_gap_every_setup_day(db, world, "22:30")  # 20:00-22:30 = 150 min
    intervals = _free(db, world, "setup", FRIDAY)
    assert availability.longest_free_run(intervals) == timedelta(minutes=150)
    assert availability.has_contiguous_run(intervals, minutes=120)
    member_id, sem_id = world
    assert availability.can_cover(
        db,
        member_id=member_id,
        semester_id=sem_id,
        shift_type_id=_shift_type_id(db, "setup"),
        event_date=FRIDAY,
    )


def test_setup_block_must_fit_inside_a_single_day(
    db: sqlite3.Connection, world: tuple[int, int]
) -> None:
    """Free time is never SUMMED across days.

    Free 23:00-23:59 on day -2 (59 min) and 08:00-09:00 on day -1 (60 min).
    Total free time is 119 minutes, but the longest single run is 60, so a
    120-minute setup block does not fit and must be reported as not fitting.
    This is why ``longest_free_run`` takes the max of the intervals rather than
    their sum.

    (With the seeded windows two spans can never be clock-adjacent — day -2 ends
    at 23:59 and day -1 starts at 08:00 — so merging across midnight cannot
    happen by accident. The guarantee that matters is structural: subtraction
    runs per span and results are never combined.)
    """
    d2, d1, d0 = _plus_days(FRIDAY, -2), _plus_days(FRIDAY, -1), FRIDAY
    _busy(db, world, starts_on=d2, starts_at_time="08:00", ends_at_time="23:00")
    _busy(db, world, starts_on=d1, starts_at_time="09:00", ends_at_time="23:59")
    _busy(db, world, starts_on=d0)  # all day
    intervals = _free(db, world, "setup", FRIDAY)
    assert intervals == [
        (_dt(d2, "23:00"), _dt(d2, "23:59")),
        (_dt(d1, "08:00"), _dt(d1, "09:00")),
    ]
    assert availability.longest_free_run(intervals) == timedelta(minutes=60)


# ---------------------------------------------------------------------------
# 7. Only ONE offset day needs to hold the block.
# ---------------------------------------------------------------------------


def test_setup_passes_when_only_the_earliest_day_is_free(
    db: sqlite3.Connection, world: tuple[int, int]
) -> None:
    _busy(db, world, starts_on=_plus_days(FRIDAY, -1), ends_on=FRIDAY)  # all day, -1 and 0
    intervals = _free(db, world, "setup", FRIDAY)
    assert intervals == [
        (_dt(_plus_days(FRIDAY, -2), "08:00"), _dt(_plus_days(FRIDAY, -2), "23:59"))
    ]
    assert availability.has_contiguous_run(intervals, minutes=120)


# ---------------------------------------------------------------------------
# 8. Cleanup is the following morning, and a conflict clips it.
# ---------------------------------------------------------------------------


def test_cleanup_morning_conflict_clips_window(
    db: sqlite3.Connection, world: tuple[int, int]
) -> None:
    """Busy 09:00-14:00 the next day leaves 00:00-09:00 of the 00:00-12:00 window.

    The busy range runs past the window's end; the remainder must be clipped to
    the window, not extended to 14:00.
    """
    next_day = _plus_days(FRIDAY, 1)
    _busy(db, world, starts_on=next_day, starts_at_time="09:00", ends_at_time="14:00")
    assert _free(db, world, "cleanup", FRIDAY) == [(_dt(next_day, "00:00"), _dt(next_day, "09:00"))]


# ---------------------------------------------------------------------------
# 9. Overlapping rows are merged, not subtracted twice.
# ---------------------------------------------------------------------------


def test_overlapping_unavailability_is_merged_not_double_counted(
    db: sqlite3.Connection, world: tuple[int, int]
) -> None:
    _busy(db, world, starts_on=FRIDAY, starts_at_time="20:00", ends_at_time="22:00")
    _busy(db, world, starts_on=FRIDAY, starts_at_time="21:00", ends_at_time="23:00")
    assert _free(db, world, "door", FRIDAY) == [(_dt(FRIDAY, "23:00"), _dt(FRIDAY, "23:59"))]


def test_merge_intervals_is_pure_and_handles_touching_spans() -> None:
    a = (datetime(2026, 9, 11, 20), datetime(2026, 9, 11, 22))
    b = (datetime(2026, 9, 11, 21), datetime(2026, 9, 11, 23))
    touching = (datetime(2026, 9, 11, 23), datetime(2026, 9, 11, 23, 30))
    empty = (datetime(2026, 9, 11, 12), datetime(2026, 9, 11, 12))
    assert availability.merge_intervals([b, a, touching, empty]) == [
        (datetime(2026, 9, 11, 20), datetime(2026, 9, 11, 23, 30))
    ]


# ---------------------------------------------------------------------------
# Layer behaviour + the loud-failure contract.
# ---------------------------------------------------------------------------


def test_whole_window_rule_for_event_night_shift_types(
    db: sqlite3.Connection, world: tuple[int, int]
) -> None:
    member_id, sem_id = world
    kwargs = {
        "member_id": member_id,
        "semester_id": sem_id,
        "shift_type_id": _shift_type_id(db, "door"),
        "event_date": FRIDAY,
    }
    assert availability.is_free_for_whole_window(db, **kwargs)
    assert availability.can_cover(db, **kwargs)
    # One minute of conflict is enough to lose a whole-window shift.
    _busy(db, world, starts_on=FRIDAY, starts_at_time="21:00", ends_at_time="21:01")
    assert not availability.is_free_for_whole_window(db, **kwargs)
    assert not availability.can_cover(db, **kwargs)


def test_shift_type_without_a_window_raises(db: sqlite3.Connection, world: tuple[int, int]) -> None:
    """A missing window must fail loudly, not guess.

    Returning "free all the time" would silently over-assign; returning "never
    free" would silently drop the shift type. Both are worse than an exception.
    """
    member_id, sem_id = world
    db.execute("INSERT INTO shift_types (slug, display_name) VALUES ('juice', 'Juice')")
    db.commit()
    with pytest.raises(LookupError):
        availability.free_intervals(
            db,
            member_id=member_id,
            semester_id=sem_id,
            shift_type_id=_shift_type_id(db, "juice"),
            event_date=FRIDAY,
        )
