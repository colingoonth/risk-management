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
    slug. Several first names are shared by two or more members of this
    roster.
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

from risk.repos import chair_notes as notes_repo
from risk.repos import events as events_repo
from risk.services.policy import (
    is_senior_in_term,
    is_sophomore_or_younger_in_term,
    quota_targets,
)

STRIKE_MARKER = " (strike)"
"""Appended to a name working off a strike.

Colin's wording. It has to be visible in the grid itself: a make-up shift does
not count toward the member's total, so without the marker the Schedule tab and
the Tally tab appear to contradict each other — a brother reads his name on
Aug 28 and then finds a total that does not include it.
"""

UNFILLED = "*** UNFILLED ***"

HOUSE_UNDECIDED = "NA"
"""Shown when an event has no host house on it.

Was "(off-site)", which asserted a fact the database does not hold. A NULL
host_house_id means nobody has SAID where the party is; it does not mean the
party is off-site, and 40 of FA26's 44 events were reading as a settled
off-site decision that had never been made. "NA" says the true thing — not
decided yet — and stops the chapter planning around a venue call nobody took.
Colin 2026-08-23.
"""
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
    effort: float
    """Weighted load — a setup counts 0.7, everything else 1.0.

    The number that is actually comparable to ``target``. ``counted_total`` is a
    headcount and the target is denominated in effort, so dividing one by the
    other overstates anyone setup-heavy: one setup-heavy brother's 17 setups read as 143%
    of quota when they are exactly 100% of it. That is the single most
    argument-starting cell the sheet could print, and it was wrong."""
    dj_shifts: int
    strike_shifts: int
    nights_on_site: int
    """Distinct events the member appears at, whatever the job.

    DISTINCT events rather than a sum of the three counts above: a member can
    hold two shifts at one party (HANDOFF permits DJ plus setup or cleanup), and
    adding the columns would report him as being there twice.

    Exists because TOTAL alone libels the DJs. One DJ stands 21 DJ nights
    and one strike make-up, none of which is rotation work, so his TOTAL is 0
    against a target of 5.1 — printed beside a sophomore on 16 that reads as
    somebody who skated, and the DJ column that explains it is three columns to
    the right. TOTAL still means risk shifts, so the quota column keeps meaning
    what it says; this is the number that answers "was he actually around"."""
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
    """Human phrasing, ISO-prefixed so the column still sorts."""
    worked_on_date: str
    """The bare ISO date, for anything that needs to compare rather than read."""
    is_strike: bool


@dataclass(frozen=True, slots=True)
class NoteRow:
    """One chair note, mirrored into the workbook."""

    kind: str
    author: str
    body: str
    created_at: str
    closed_at: str | None
    closed_note: str | None


@dataclass(frozen=True, slots=True)
class SemesterExport:
    semester_name: str
    schedule: list[ScheduleRow]
    tally: list[TallyRow]
    by_brother: list[ShiftRow]
    notes: list[NoteRow] = field(default_factory=list)
    shift_type_columns: list[tuple[str, int]] = field(default_factory=list)
    column_headers: dict[str, str] = field(default_factory=dict)
    senior_target: float = 0.0
    junior_target: float = 0.0
    sophomore_target: float = 0.0
    effort_weights: dict[str, float] = field(default_factory=dict)
    """Live ``shift_types.effort_weight`` by slug.

    Carried on the export so the Tally caption can STATE the weighting instead
    of asserting a number somebody typed once. It shipped "a setup counts 0.7 …
    so 17 setups is a full quota" to the exec for four days after the weights
    were retuned to 0.4, which is the same class of bug as the two vs-target
    code paths that drifted: a fact about the model, written down a second time,
    somewhere nothing recomputes it.
    """
    schedule_note: str = ""
    """A banner rendered directly above the Schedule grid, or empty.

    Set only when the PUBLISHED range actually contains a placeholder, so a
    block of nothing but confirmed parties does not carry a paragraph about
    held dates that are not on it. A standing caption everyone has learned to
    skip is worth less than one that only appears when it is true.
    """



PLACEHOLDER_NOTE = (
    "PLACEHOLDER rows are HELD DATES, not confirmed parties — about half do not happen. "
    "If yours is called off you are the first cover: anyone listed on a placeholder may be "
    "pulled onto another night when somebody cannot make their shift. Do not assume you are "
    "off until the date has passed."
)
"""The caption above the Schedule grid when the published block holds a placeholder.

Colin's instruction, 2026-08-26, on deciding to publish the 18 Sep placeholder
rather than hide it. Defined once here and rendered by all three surfaces —
xlsx, the Sheets push and the PDF — because a sentence the chapter is asked to
act on cannot say three slightly different things depending on where it is read.
"""


_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# The order the chapter reads them in, which is NOT the order the app fills
# them. The old sheet ran RIDES, DOOR, BAR, SETUP, CLEANUP left to right and
# people know where to look; the fill order puts gated types first for reasons
# that are internal to the solver and mean nothing to a reader.
# DJ is deliberately absent. It is not risk work — it is already excluded from
# the coverage count for that reason — and a DJ column on a sheet the whole
# chapter reads invites the question of why a man is on the risk schedule for a
# job the chair does not track. The assignment still exists and is still
# announced; it just is not a column here.
_DISPLAY_ORDER = ("driver", "door", "bar", "setup", "cleanup")
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


def _pretty_date(iso: str) -> str:
    """``2026-08-29`` -> ``Sat 29 Aug``. For humans, beside the ISO date."""
    d = _date.fromisoformat(iso)
    return f"{_WEEKDAYS[d.weekday()]} {d.day} {d.strftime('%b')}"


def _describe_window(win: sqlite3.Row | None, event_date: str) -> str:
    """When a shift is actually worked, in words, derived from the window row.

    Every text surface in this app prints shifts against the PARTY date. For
    four of the six jobs that is right. For the other two it is wrong in
    opposite directions: setup runs up to two days BEFORE, and cleanup is the
    morning AFTER. Telling four people to turn up on the night of the party for
    a shift that happens the next morning manufactures no-shows, and a no-show
    is a strike — so this is the app inventing discipline problems.

    Phrasing is derived from ``shift_type_windows``, never hardcoded, so if the
    chapter moves cleanup to noon-to-four the label moves with it. The three
    shapes below are read off the times rather than the slug:

      starts at midnight   -> "before HH:MM"   (a deadline, not a shift start)
      runs to 23:59        -> "from HH:MM"     (an open-ended evening)
      anything else        -> "HH:MM-HH:MM"

    ``00:00-12:00`` is technically an accurate rendering of the cleanup window
    and a terrible thing to put in front of 67 people, most of whom will read
    "00:00" as "be there at midnight".
    """
    if win is None:
        return ""
    start = str(win["window_start_time"])
    end = str(win["window_end_time"])
    if start == "00:00":
        when = f"before {end}"
    elif end == "23:59":
        when = f"from {start}"
    else:
        when = f"{start}-{end}"

    first = _shift(event_date, int(win["offset_days_start"]))
    last = _shift(event_date, int(win["offset_days_end"]))
    days = _pretty_date(first) if first == last else f"{_pretty_date(first)} - {_pretty_date(last)}"

    minutes = win["min_contiguous_minutes"]
    if minutes:
        hours = int(minutes) // 60
        return f"{days}, {when}, any {hours}h block"
    return f"{days}, {when}"


def _window_header(win: sqlite3.Row | None, label: str) -> str:
    """The column heading for a job whose window is not the night of the party.

    Carried in the header rather than left to the per-row window column, because
    the grid is what gets printed and pinned to a wall, and a reader scanning
    down the CLEANUP block will never look back across seven columns to find out
    which day it means.
    """
    if win is None:
        return label
    offset = int(win["offset_days_start"])
    if offset > 0:
        end = str(win["window_end_time"])
        return (
            f"{label} (NEXT MORNING, before {end})" if offset == 1 else f"{label} (+{offset} days)"
        )
    if int(win["offset_days_end"]) < 0 or offset < 0:
        return f"{label} (BEFORE the party)"
    return label


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


def build(
    conn: sqlite3.Connection,
    *,
    semester_id: int,
    on_or_after: str | None = None,
    on_or_before: str | None = None,
) -> SemesterExport:
    """Assemble every row of the export in a handful of queries.

    ``on_or_after`` / ``on_or_before`` narrow the SCHEDULE grid and the
    By-Brother rows to one published block. Added 2026-08-26 with fortnightly
    publishing: past the first block the calendar is deliberately unfilled, and
    a grid of 44 parties with 37 empty rows does not read as "not built yet", it
    reads as "nobody is working these", which is the more alarming of the two.

    Filtered by DATE, never by "has an assignment". An event that failed to fill
    must still appear, loudly and empty — a party quietly dropping off the
    schedule because nobody was seated on it is the single worst thing this
    export could do.

    THE TALLY IS NOT FILTERED, and that is deliberate. It reports progress
    against a SEASON target, so restricting it to a fortnight would print
    everybody at roughly a seventh of their quota and make the "vs target"
    column meaningless. Season totals, block schedule — the two questions the
    sheet answers are on different clocks.
    """
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
                   w.window_start_time, w.window_end_time, w.min_contiguous_minutes
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
    column_headers = {
        slug: _window_header(windows.get(slug), _DISPLAY_LABEL.get(slug, slug.upper()))
        for slug, _ in shift_type_columns
    }

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
        if on_or_after is not None and event.date < on_or_after:
            continue
        if on_or_before is not None and event.date > on_or_before:
            continue
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
                host_house=event.host_house_slug or HOUSE_UNDECIDED,
                planning_status=event.planning_status,
                setup_window=_describe_window(setup_w, event.date),
                # Cleanup is the MORNING AFTER. Every text surface in this app
                # prints shifts against the event date, which quietly tells four
                # people to turn up a day early — and a missed shift is a strike.
                cleanup_window=_describe_window(cleanup_w, event.date),
                needed=needed,
                filled=filled,
                cells=cells,
            )
        )

    senior_target, junior_target, sophomore_target = _targets(conn, semester_id=semester_id)
    tally, by_brother = _build_member_rows(
        conn,
        semester_id=semester_id,
        term_start_year=term_start_year,
        term_is_fall=term_is_fall,
        windows=windows,
        senior_target=senior_target,
        junior_target=junior_target,
        sophomore_target=sophomore_target,
    )
    # By Brother answers "which nights am I working", so it follows the block.
    # The Tally above does NOT — see the docstring.
    if on_or_after is not None:
        by_brother = [r for r in by_brother if r.date >= on_or_after]
    if on_or_before is not None:
        by_brother = [r for r in by_brother if r.date <= on_or_before]
    notes = [
        NoteRow(
            kind=n.kind,
            author=n.author,
            body=n.body,
            created_at=n.created_at,
            closed_at=n.closed_at,
            closed_note=n.closed_note,
        )
        for n in notes_repo.list_for_semester(conn, semester_id)
    ]
    return SemesterExport(
        semester_name=sem["name"],
        effort_weights={
            r["slug"]: float(r["effort_weight"])
            for r in conn.execute("SELECT slug, effort_weight FROM shift_types")
        },
        schedule_note=(
            PLACEHOLDER_NOTE
            if any(r.planning_status == "placeholder" for r in schedule)
            else ""
        ),
        schedule=schedule,
        tally=tally,
        by_brother=by_brother,
        notes=notes,
        shift_type_columns=shift_type_columns,
        column_headers=column_headers,
        senior_target=senior_target,
        junior_target=junior_target,
        sophomore_target=sophomore_target,
    )


def _targets(conn: sqlite3.Connection, *, semester_id: int) -> tuple[float, float, float]:
    from risk.services import fairness

    q = fairness.build_quota_context(conn, semester_id=semester_id)
    return q.senior_target, q.junior_target, q.sophomore_target


def _build_member_rows(
    conn: sqlite3.Connection,
    *,
    semester_id: int,
    term_start_year: int,
    term_is_fall: bool,
    windows: dict[str, sqlite3.Row],
    senior_target: float,
    junior_target: float,
    sophomore_target: float,
) -> tuple[list[TallyRow], list[ShiftRow]]:
    """The Tally and By-Brother tabs, from one pass over every assigned shift.

    Exempt officers get a row at zero rather than being omitted. HANDOFF is
    explicit about this and it is the whole point: the chair's answer to "why
    does the president never work" is a line in the sheet reading zero with the
    reason printed beside it, not the president's absence from a list.
    """
    members = conn.execute(
        """
        SELECT m.id, m.display_name, m.pledge_class, m.class_year,
               COALESCE(m.risk_class_year, m.class_year) AS quota_class_year,
               m.notes,
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
               st.counts_toward_tally, st.effort_weight, s.slot_index,
               s.serves_strike_id, e.date, e.display_name AS event_name
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
        effort = 0.0
        nights: set[str] = set()
        for r in rows:
            nights.add(r["date"])
            is_strike = r["serves_strike_id"] is not None
            if r["shift_slug"] == "dj":
                dj += 1
            elif is_strike:
                strike += 1
            else:
                counted += 1
                effort += float(r["effort_weight"])
                per_type[r["shift_slug"]] = per_type.get(r["shift_slug"], 0) + 1
            offset = windows.get(r["shift_slug"])
            worked = _shift(r["date"], int(offset["offset_days_start"])) if offset else r["date"]
            # ISO first so the column still sorts, then the same phrase the
            # Schedule tab uses. A brother finding his own row needs the day and
            # the deadline in the cell he is already looking at, not a lookup
            # back to a header seven columns away.
            worked_label = f"{worked}  {_describe_window(offset, r['date'])}" if offset else worked
            by_brother.append(
                ShiftRow(
                    display_name=m["display_name"],
                    pledge_class=m["pledge_class"] or "",
                    class_label=label,
                    date=r["date"],
                    event=r["event_name"].split(" (2")[0],
                    shift_type=_DISPLAY_LABEL.get(r["shift_slug"], r["shift_slug"].upper()),
                    slot_index=r["slot_index"],
                    worked_on=worked_label,
                    worked_on_date=worked,
                    is_strike=is_strike,
                )
            )
        # QUOTA year for the target, ROSTER year for the label above. A chair
        # override changes what a member is held to; it does not change what
        # class he is in, and printing "Junior" beside a sophomore would be the
        # sheet lying about the roster to explain a number.
        quota_year = m["quota_class_year"]
        senior = is_senior_in_term(
            quota_year, term_start_year=term_start_year, term_is_fall=term_is_fall
        )
        # Same precedence as fairness.QuotaContext.target_for, and it has to
        # stay the same: this number is what the sheet PRINTS beside his name,
        # and the fill divides by the other one. Two of these drifted once
        # already (the vs-target column) and shipped a wrong percentage to the
        # chapter.
        if senior:
            member_target = senior_target
        elif is_sophomore_or_younger_in_term(
            quota_year, term_start_year=term_start_year, term_is_fall=term_is_fall
        ):
            member_target = sophomore_target
        else:
            member_target = junior_target
        tally.append(
            TallyRow(
                display_name=m["display_name"],
                pledge_class=m["pledge_class"] or "",
                class_label=label,
                per_type=per_type,
                counted_total=counted,
                effort=round(effort, 1),
                dj_shifts=dj,
                strike_shifts=strike,
                nights_on_site=len(nights),
                # An exempt officer has no quota. Printing one would invite the
                # reading that he is 100% under target rather than out of scope.
                target=0.0 if m["exempt"] else member_target,
                exempt=bool(m["exempt"]),
                note=m["notes"] or "",
            )
        )
    by_brother.sort(key=lambda r: (r.display_name, r.date, r.shift_type))
    tally.sort(key=lambda r: (r.exempt, -r.counted_total, r.display_name))
    return tally, by_brother


def quota_preview(
    *, rotation_slots: int, senior_count: int, junior_count: int, sophomore_count: int = 0
) -> tuple[float, float, float]:
    """Re-exported so a caller can show the targets without a database."""
    return quota_targets(
        rotation_slots=rotation_slots,
        senior_count=senior_count,
        junior_count=junior_count,
        sophomore_count=sophomore_count,
    )
