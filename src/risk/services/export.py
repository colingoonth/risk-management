"""Assemble the semester schedule into the shape a chapter actually reads.

The chapter has read a Google Sheet for years — one row per party, one column
per slot, names in the cells. That layout is not a rendering detail, it is the
artefact everyone already knows how to read, so the export reproduces it rather
than inventing something tidier.

This module produces plain rows. Writing them into a workbook is the CLI's job
(``risk.cli.export``), which keeps the layering intact and means the same rows
could feed a CSV, a Google Sheet push, or a print view without being rebuilt.

WHAT THE OLD SHEET GOT WRONG, and what these rows do differently. The SP26
tracker could not count: its TRACKER tab claimed 149 shifts against 310 filled
cells, 21 people who worked were missing from it entirely, and one brother
appeared under four spellings at 4 shifts when he had worked 10. Twelve cells
held a bare first name with two people on the roster who matched it. So:

  - every cell carries the FULL display name, never a first name and never a
    slug. Three Masons, two Chrises, and two each of Nate, Mike, Joe, Jack,
    Christian, Alex, Albert and Aidan are on this roster.
  - the Tally tab is derived from the same rows as the Schedule tab, so the two
    cannot disagree the way the old sheet's two tabs did.
  - an unfilled slot says so in the cell rather than being blank, because a
    blank is indistinguishable from a slot that was never required.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date as _date
from datetime import timedelta

from risk.repos import events as events_repo
from risk.services.policy import is_senior_in_term, quota_targets

STRIKE_MARKER = " (strike)"
"""Appended to a name working off a strike.

Colin's wording. It has to be visible in the grid itself: a make-up shift does
not count toward the member's total, so without the marker the Schedule tab and
the Tally tab appear to contradict each other — a brother reads his name on
Aug 28 and then finds a total that does not include it.
"""

UNFILLED = "*** UNFILLED ***"
"""What an empty required slot says.

Never a blank. A blank cell cannot be told apart from "this party does not need
a bar", and the whole failure mode of the old tracker was cells that read as
information when they were absence.
"""


@dataclass(frozen=True, slots=True)
class ScheduleCell:
    shift_type_slug: str
    slot_index: int
    name: str


@dataclass(frozen=True, slots=True)
class ScheduleRow:
    """One party."""

    date: str
    weekday: str
    display_name: str
    event_type: str
    host_house: str
    planning_status: str
    setup_window: str
    cleanup_window: str
    needed: int
    filled: int
    cells: dict[tuple[str, int], str]


@dataclass(frozen=True, slots=True)
class TallyRow:
    """One member."""

    display_name: str
    pledge_class: str
    class_label: str
    per_type: dict[str, int]
    counted_total: int
    dj_shifts: int
    strike_shifts: int
    target: float
    exempt: bool
    note: str


@dataclass(frozen=True, slots=True)
class ShiftRow:
    """One shift, for the per-brother tab."""

    display_name: str
    pledge_class: str
    class_label: str
    date: str
    event: str
    shift_type: str
    slot_index: int
    worked_on: str
    is_strike: bool


@dataclass(frozen=True, slots=True)
class SemesterExport:
    semester_name: str
    schedule: list[ScheduleRow]
    tally: list[TallyRow]
    by_brother: list[ShiftRow]
    shift_type_columns: list[tuple[str, int]] = field(default_factory=list)
    senior_target: float = 0.0
    underclass_target: float = 0.0


_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# The order the chapter reads them in, which is NOT the order the app fills
# them. The old sheet ran RIDES, DOOR, BAR, SETUP, CLEANUP left to right and
# people know where to look; the fill order puts gated types first for reasons
# that are internal to the solver and mean nothing to a reader.
_DISPLAY_ORDER = ("driver", "door", "bar", "setup", "cleanup", "dj")
_DISPLAY_LABEL = {
    "driver": "RIDES",
    "door": "DOOR",
    "bar": "BAR",
    "setup": "SETUP",
    "cleanup": "CLEANUP",
    "dj": "DJ",
}


def _weekday(iso: str) -> str:
    return _WEEKDAYS[_date.fromisoformat(iso).weekday()]


def _shift(iso: str, days: int) -> str:
    return (_date.fromisoformat(iso) + timedelta(days=days)).isoformat()


def _class_label(class_year: int | None, *, term_start_year: int, term_is_fall: bool) -> str:
    """A human class name, computed fall-aware.

    Deliberately not derived from the replaced seniority phantom, whose year
    arithmetic was written for spring terms and reads a 2027 graduate as a
    junior in a fall 2026 one. Harmless while it only affected ranking; wrong
    the moment it is printed next to somebody's name.
    """
    if class_year is None:
        return "?"
    if is_senior_in_term(class_year, term_start_year=term_start_year, term_is_fall=term_is_fall):
        return "Senior"
    graduating = term_start_year + 1 if term_is_fall else term_start_year
    return {1: "Junior", 2: "Sophomore", 3: "Freshman"}.get(
        class_year - graduating, f"'{str(class_year)[-2:]}"
    )


def build(conn: sqlite3.Connection, *, semester_id: int) -> SemesterExport:
    """Assemble every row of the export in a handful of queries."""
    sem = conn.execute(
        "SELECT name, starts_on FROM semesters WHERE id = ?", (semester_id,)
    ).fetchone()
    if sem is None:
        raise LookupError(f"semester {semester_id} not found")
    term_start_year = int(sem["starts_on"][:4])
    term_is_fall = int(sem["starts_on"][5:7]) >= 7

    windows = {
        r["slug"]: r
        for r in conn.execute(
            """
            SELECT st.slug, w.offset_days_start, w.offset_days_end,
                   w.window_start_time, w.window_end_time
            FROM shift_type_windows w JOIN shift_types st ON st.id = w.shift_type_id
            """
        )
    }

    # Column widths come from the data, never a constant. FA26 runs 3 rides on
    # a big party and 2 on a mixer; hardcoding the old sheet's 3/4/2/4/4 would
    # print two permanently empty DOOR columns and silently truncate anything
    # the chapter scales up later.
    width_rows = conn.execute(
        """
        SELECT st.slug, MAX(r.target_count) AS w
        FROM event_shift_requirements r
        JOIN shift_types st ON st.id = r.shift_type_id
        JOIN events e ON e.id = r.event_id
        WHERE e.semester_id = ? AND e.status <> 'cancelled' AND r.target_count > 0
        GROUP BY st.slug
        """,
        (semester_id,),
    ).fetchall()
    widths = {r["slug"]: int(r["w"]) for r in width_rows}
    shift_type_columns = [(s, widths[s]) for s in _DISPLAY_ORDER if s in widths]

    # One pass over every REQUIRED slot. Driven from requirements CROSS JOIN the
    # slot index, not from `shifts`: shift rows are created lazily by the fill,
    # so an event nobody has assigned has zero of them and would export as a row
    # of blanks that reads exactly like a fully-staffed mixer with no bar.
    assigned = {}
    for r in conn.execute(
        """
        SELECT e.id AS event_id, st.slug AS shift_slug, s.slot_index,
               m.display_name, s.serves_strike_id
        FROM shifts s
        JOIN events e ON e.id = s.event_id
        JOIN shift_types st ON st.id = s.shift_type_id
        LEFT JOIN members m ON m.id = s.assigned_member_id
        WHERE e.semester_id = ? AND s.assigned_member_id IS NOT NULL
        """,
        (semester_id,),
    ):
        name = r["display_name"]
        if r["serves_strike_id"] is not None:
            name += STRIKE_MARKER
        assigned[(r["event_id"], r["shift_slug"], r["slot_index"])] = name

    requirements: dict[int, dict[str, int]] = {}
    for r in conn.execute(
        """
        SELECT r.event_id, st.slug, r.target_count
        FROM event_shift_requirements r
        JOIN shift_types st ON st.id = r.shift_type_id
        JOIN events e ON e.id = r.event_id
        WHERE e.semester_id = ?
        """,
        (semester_id,),
    ):
        requirements.setdefault(r["event_id"], {})[r["slug"]] = int(r["target_count"])

    schedule: list[ScheduleRow] = []
    for event in events_repo.list_for_semester(conn, semester_id):
        targets = requirements.get(event.id, {})
        cells: dict[tuple[str, int], str] = {}
        needed = filled = 0
        for slug, target in targets.items():
            if slug == "dj":
                continue  # counted separately; it is not risk work
            for i in range(target):
                needed += 1
                name = assigned.get((event.id, slug, i))
                cells[(slug, i)] = name or UNFILLED
                if name:
                    filled += 1
        for i in range(targets.get("dj", 0)):
            cells[("dj", i)] = assigned.get((event.id, "dj", i)) or UNFILLED

        setup_w = windows.get("setup")
        cleanup_w = windows.get("cleanup")
        schedule.append(
            ScheduleRow(
                date=event.date,
                weekday=_weekday(event.date),
                # The " (YYYY-MM-DD)" suffix exists to get repeated party names
                # past UNIQUE(semester_id, display_name). It is a database
                # concern; the reader already has the date in column A.
                display_name=event.display_name.split(" (2")[0],
                event_type=event.event_type_slug,
                host_house=event.host_house_slug or "(off-site)",
                planning_status=event.planning_status,
                setup_window=(
                    f"{_shift(event.date, int(setup_w['offset_days_start']))} to {event.date}"
                    if setup_w
                    else ""
                ),
                # Cleanup is the MORNING AFTER. Every text surface in this app
                # prints shifts against the event date, which quietly tells four
                # people to turn up a day early — and a missed shift is a strike.
                cleanup_window=(
                    f"{_shift(event.date, int(cleanup_w['offset_days_start']))} "
                    f"{cleanup_w['window_start_time']}-{cleanup_w['window_end_time']}"
                    if cleanup_w
                    else ""
                ),
                needed=needed,
                filled=filled,
                cells=cells,
            )
        )

    senior_target, underclass_target = _targets(conn, semester_id=semester_id)
    tally, by_brother = _build_member_rows(
        conn,
        semester_id=semester_id,
        term_start_year=term_start_year,
        term_is_fall=term_is_fall,
        windows=windows,
        senior_target=senior_target,
        underclass_target=underclass_target,
    )
    return SemesterExport(
        semester_name=sem["name"],
        schedule=schedule,
        tally=tally,
        by_brother=by_brother,
        shift_type_columns=shift_type_columns,
        senior_target=senior_target,
        underclass_target=underclass_target,
    )


def _targets(conn: sqlite3.Connection, *, semester_id: int) -> tuple[float, float]:
    from risk.services import fairness

    q = fairness.build_quota_context(conn, semester_id=semester_id)
    return q.senior_target, q.underclass_target


def _build_member_rows(
    conn: sqlite3.Connection,
    *,
    semester_id: int,
    term_start_year: int,
    term_is_fall: bool,
    windows: dict[str, sqlite3.Row],
    senior_target: float,
    underclass_target: float,
) -> tuple[list[TallyRow], list[ShiftRow]]:
    """The Tally and By-Brother tabs, from one pass over every assigned shift.

    Exempt officers get a row at zero rather than being omitted. HANDOFF is
    explicit about this and it is the whole point: the chair's answer to "why
    does the president never work" is a line in the sheet reading zero with the
    reason printed beside it, not the president's absence from a list.
    """
    members = conn.execute(
        """
        SELECT m.id, m.display_name, m.pledge_class, m.class_year, m.notes,
               EXISTS(
                 SELECT 1 FROM member_roles mr JOIN roles r ON r.id = mr.role_id
                 WHERE mr.member_id = m.id AND mr.semester_id = ?
                   AND r.default_excluded_from_assignment = 1 AND r.exclude_is_soft = 0
               ) AS exempt
        FROM members m
        JOIN member_statuses ms ON ms.id = m.status_id
        WHERE ms.excludes_from_assignment = 0
        ORDER BY m.display_name
        """,
        (semester_id,),
    ).fetchall()

    shifts = conn.execute(
        """
        SELECT s.assigned_member_id AS member_id, st.slug AS shift_slug,
               st.counts_toward_tally, s.slot_index, s.serves_strike_id,
               e.date, e.display_name AS event_name
        FROM shifts s
        JOIN events e ON e.id = s.event_id
        JOIN shift_types st ON st.id = s.shift_type_id
        WHERE e.semester_id = ? AND s.assigned_member_id IS NOT NULL
        ORDER BY e.date, st.slug, s.slot_index
        """,
        (semester_id,),
    ).fetchall()

    per_member: dict[int, list[sqlite3.Row]] = {}
    for r in shifts:
        per_member.setdefault(r["member_id"], []).append(r)

    tally: list[TallyRow] = []
    by_brother: list[ShiftRow] = []
    for m in members:
        label = _class_label(
            m["class_year"], term_start_year=term_start_year, term_is_fall=term_is_fall
        )
        rows = per_member.get(m["id"], [])
        per_type: dict[str, int] = {}
        counted = dj = strike = 0
        for r in rows:
            is_strike = r["serves_strike_id"] is not None
            if r["shift_slug"] == "dj":
                dj += 1
            elif is_strike:
                strike += 1
            else:
                counted += 1
                per_type[r["shift_slug"]] = per_type.get(r["shift_slug"], 0) + 1
            offset = windows.get(r["shift_slug"])
            worked = _shift(r["date"], int(offset["offset_days_start"])) if offset else r["date"]
            by_brother.append(
                ShiftRow(
                    display_name=m["display_name"],
                    pledge_class=m["pledge_class"] or "",
                    class_label=label,
                    date=r["date"],
                    event=r["event_name"].split(" (2")[0],
                    shift_type=_DISPLAY_LABEL.get(r["shift_slug"], r["shift_slug"].upper()),
                    slot_index=r["slot_index"],
                    worked_on=worked,
                    is_strike=is_strike,
                )
            )
        senior = is_senior_in_term(
            m["class_year"], term_start_year=term_start_year, term_is_fall=term_is_fall
        )
        tally.append(
            TallyRow(
                display_name=m["display_name"],
                pledge_class=m["pledge_class"] or "",
                class_label=label,
                per_type=per_type,
                counted_total=counted,
                dj_shifts=dj,
                strike_shifts=strike,
                # An exempt officer has no quota. Printing one would invite the
                # reading that he is 100% under target rather than out of scope.
                target=0.0 if m["exempt"] else (senior_target if senior else underclass_target),
                exempt=bool(m["exempt"]),
                note=m["notes"] or "",
            )
        )
    by_brother.sort(key=lambda r: (r.display_name, r.date, r.shift_type))
    tally.sort(key=lambda r: (r.exempt, -r.counted_total, r.display_name))
    return tally, by_brother


def quota_preview(
    *, rotation_slots: int, senior_count: int, underclass_count: int
) -> tuple[float, float]:
    """Re-exported so a caller can show the targets without a database."""
    return quota_targets(
        rotation_slots=rotation_slots,
        senior_count=senior_count,
        underclass_count=underclass_count,
    )
