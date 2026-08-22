"""``risk export`` — write the semester schedule to a .xlsx workbook.

Three tabs, in the order they get used:

  Schedule   — the grid the chapter already reads: one row per party, one column
               per slot, full names in the cells.
  Tally      — one row per member, counts by job, total, and target. This is the
               defensibility artefact: it is the only surface anywhere that
               answers "why do I have more shifts than him".
  By Brother — one row per shift, sorted by name, so somebody can find his own
               nights without reading a 43-row grid. Carries the WORKED-ON date,
               which for cleanup is the morning AFTER the party.

Kept in the CLI rather than behind a button in the app, and that is a decision
rather than laziness. The packaged app is a windowed PyInstaller bundle launched
from the Dock, so it inherits the launchd environment — and a file written from
there lands wherever the bundle's working directory happens to be, with no
save dialog wired up (``webview.create_window`` is called with no ``js_api``, so
nothing in the SPA can call Python at all today). A CLI command writes where the
chair asked, which is the whole job.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Annotated

import typer

from risk.cli._common import mode_from_ctx, open_conn
from risk.cli.output import emit_error, emit_success
from risk.repos import semesters as semesters_repo
from risk.services import export as export_svc

app = typer.Typer(help="Export the schedule for the chapter to read.")

# Duty Ledger, as close as a spreadsheet gets. Brass is the primary mark and
# oxblood is the alarm — the one load-bearing rule of the design system, and the
# reason UNFILLED is the only red thing on the page.
_INK = "1C1814"
_BRASS = "8A6A2F"
_OXBLOOD = "6E1F1F"
_RULE = "C9C0B2"
_STRIKE_BG = "FFB74D"  # strike make-ups: orange, Colin's call
_HEADER_BG = "EFE9DD"

# Lifted from the SP26 sheet, read back off the live document rather than
# guessed. The chapter has read this colour scheme for a year: the DATE cell is
# tinted by weekday, and the rest of the row is banded lavender.
#
# These are not colours anyone would choose from scratch, and that is the point.
# Matching what people already scan for is worth more than a nicer palette they
# have to learn — a chair pointing at "the magenta ones" is understood
# immediately. The two functional marks the app adds on top (oxblood for an
# unfilled slot, a warm fill for a strike make-up) are the only new signals, so
# they stand out against a scheme everybody already reads past.
_LEGACY_DAY_FILL = {
    "Tue": "FF00FF",
    "Fri": "00FF00",
    "Sat": "FF9900",
}
_LEGACY_ROW_BAND = "D9D2E9"
_LEGACY_PLACEHOLDER = "C27BA0"
_LEGACY_PLEDGING = "93C47D"


def _styles() -> dict[str, object]:
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    thin = Side(style="thin", color=_RULE)
    return {
        "header": Font(name="Helvetica Neue", size=10, bold=True, color=_INK),
        "body": Font(name="Helvetica Neue", size=10, color=_INK),
        "brass": Font(name="Helvetica Neue", size=10, bold=True, color=_BRASS),
        "alarm": Font(name="Helvetica Neue", size=10, bold=True, color=_OXBLOOD),
        "mono": Font(name="Menlo", size=9, color=_INK),
        "header_fill": PatternFill("solid", fgColor=_HEADER_BG),
        "strike_fill": PatternFill("solid", fgColor=_STRIKE_BG),
        "strike_font": Font(name="Helvetica Neue", size=10, bold=True, color=_INK),
        "band": PatternFill("solid", fgColor="F7F4EE"),
        "title": Font(name="Helvetica Neue", size=14, bold=True, color=_INK),
        "dim": Font(name="Helvetica Neue", size=10, color="9A9186"),
        "total": Font(name="Helvetica Neue", size=11, bold=True, color=_INK),
        "border": Border(bottom=thin),
        "center": Alignment(horizontal="center", vertical="center"),
        "left": Alignment(horizontal="left", vertical="center"),
        "wrap": Alignment(horizontal="left", vertical="top", wrap_text=True),
    }


NOTES_TAB = "Risk Notes"
"""The chair's notes, mirrored out of the database.

Read-only here. Colin writes these in the app — `Notes` in the nav, or
`risk note add` — and the database is the single copy. Mirroring rather than
hosting them means the sheet cannot drift from what the app acted on, which is
the failure the old tracker had between its grid and its TRACKER tab.

Every cell is therefore regenerated on a push like any other tab, and the header
says so, because a tab called "Risk Notes" in an editable spreadsheet otherwise
invites typing into it — and that text would vanish on the next refresh with
nothing to explain why.
"""


SCRATCH_TAB = "Scratch pad"  # matches the tab Colin created in the live sheet
"""The one tab that belongs to the readers rather than to this program.

Four people have write access to the published sheet, and every tab in it is
either regenerated wholesale on each push (Schedule, Tally, By Brother) or
mirrored out of the app (Risk Notes). So anything they type anywhere disappears
on the next refresh, silently, with nothing to explain where it went. A
spreadsheet you can type into but must not is a trap, and the fix is one tab
that is genuinely theirs.

Kept out of GENERATED_TABS, which is why push never names it. That list is
explicit rather than "every tab in the workbook" precisely so this works.
"""


def _write_scratch(ws, data: export_svc.SemesterExport, st: dict) -> None:  # noqa: ANN001
    """Header only. The rest of the tab is deliberately left empty."""
    from openpyxl.utils import get_column_letter

    ws.append([f"SCRATCH PAD — {data.semester_name}"])
    ws.cell(row=1, column=1).font = st["title"]
    for line in (
        "This tab is yours. Type anything — it is never overwritten.",
        "",
        "Every OTHER tab is rebuilt from the app each time the schedule is "
        "refreshed, so notes written there are lost without warning.",
        "Swap requests, corrections, questions, anyone who cannot make a shift — "
        "put them here and they will be picked up.",
    ):
        ws.append([line])
        ws.cell(row=ws.max_row, column=1).font = st["dim"] if line else st["body"]
    ws.append([])
    ws.append(["Date", "Who", "What"])
    for c in range(1, 4):
        cell = ws.cell(row=ws.max_row, column=c)
        cell.font = st["header"]
        cell.fill = st["header_fill"]
        cell.border = st["border"]
    for i, w in enumerate([14, 22, 110], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = ws.cell(row=ws.max_row + 1, column=1).coordinate


def _write_notes(ws, data: export_svc.SemesterExport, st: dict) -> None:  # noqa: ANN001
    """Mirror the chair notes into a tab the chapter can read."""
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    for row in _notes_values(data):
        ws.append(row)

    ws.cell(row=1, column=1).font = Font(name="Helvetica Neue", size=14, bold=True, color=_INK)
    ws.cell(row=2, column=1).font = Font(
        name="Helvetica Neue", size=10, italic=True, color="9A9186"
    )
    for c in range(1, 6):
        cell = ws.cell(row=4, column=c)
        cell.font = st["header"]
        cell.fill = st["header_fill"]
        cell.border = st["border"]
    for r in range(5, ws.max_row + 1):
        kind = ws.cell(row=r, column=1).value
        closed = ws.cell(row=r, column=4).value
        for c in range(1, 6):
            cell = ws.cell(row=r, column=c)
            cell.border = st["border"]
            cell.alignment = st["wrap"] if c in (3, 5) else st["left"]
            # A standing rule is the one that keeps applying, so it is the one
            # worth spotting in a long list.
            cell.font = st["brass"] if kind == "standing" and not closed else st["body"]
        if closed:
            for c in range(1, 6):
                ws.cell(row=r, column=c).font = Font(name="Helvetica Neue", size=10, color="9A9186")

    for i, w in enumerate([12, 9, 78, 20, 44], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _notes_values(data: export_svc.SemesterExport) -> list[list[str]]:
    """The notes tab as a plain grid, shared by the workbook and the push."""
    rows: list[list[str]] = [
        [f"RISK NOTES — {data.semester_name}"],
        [
            "Read-only mirror of the app's notes. Anything typed HERE is overwritten "
            f"on the next refresh — use the '{SCRATCH_TAB}' tab instead."
        ],
        [],
        ["Kind", "By", "Note", "Closed", "What was done"],
    ]
    for n in data.notes:
        rows.append(
            [
                "STANDING" if n.kind == "standing" else "one-off",
                n.author,
                n.body,
                (n.closed_at or "")[:16],
                n.closed_note or "",
            ]
        )
    if not data.notes:
        rows.append(["", "", "(no notes yet)", "", ""])
    return rows


def _write_schedule(ws, data: export_svc.SemesterExport, st: dict) -> None:  # noqa: ANN001
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    header_a = ["Date", "Day", "Event", "House", "Status", "Setup window", "Cleanup window"]
    row1: list[str] = list(header_a)
    row2: list[str] = [""] * len(header_a)
    for slug, width in data.shift_type_columns:
        # The header carries the DAY for anything not worked on the night of
        # the party. A reader scanning down the CLEANUP block will not look back
        # across seven columns to find out it means the next morning.
        label = data.column_headers.get(slug, export_svc._DISPLAY_LABEL.get(slug, slug.upper()))
        for i in range(width):
            row1.append(label if i == 0 else "")
            row2.append(str(i + 1))
    row1 += ["Needed", "Filled"]
    row2 += ["", ""]

    # Title + legend, in the SP26 sheet's shape: labels on one row, colour
    # swatches directly under them. Reproduced rather than improved on, because
    # the chapter has scanned this key for a year and "the magenta ones" is
    # understood instantly in a way a better palette would not be.
    ws.append([f"RISK {data.semester_name}"])
    ws.cell(row=1, column=1).font = Font(name="Helvetica Neue", size=14, bold=True, color=_INK)

    legend = [
        ("TUESDAY", _LEGACY_DAY_FILL["Tue"]),
        ("FRIDAY", _LEGACY_DAY_FILL["Fri"]),
        ("SATURDAY", _LEGACY_DAY_FILL["Sat"]),
        ("PLACEHOLDER", _LEGACY_PLACEHOLDER),
        ("PLEDGING", _LEGACY_PLEDGING),
        ("UNFILLED", _OXBLOOD),
        ("(strike) = make-up, uncounted", _STRIKE_BG),
    ]
    ws.append([label for label, _ in legend])
    ws.append([""] * len(legend))
    for i, (_label, colour) in enumerate(legend, start=1):
        ws.cell(row=2, column=i).font = st["header"]
        ws.cell(row=2, column=i).alignment = st["center"]
        ws.cell(row=3, column=i).fill = PatternFill("solid", fgColor=colour)
    ws.append([])  # a blank rule between the key and the grid, as SP26 had

    header_top = 5
    ws.append(row1)
    ws.append(row2)
    for r in (header_top, header_top + 1):
        for c in range(1, len(row1) + 1):
            cell = ws.cell(row=r, column=c)
            cell.font = st["header"]
            cell.fill = st["header_fill"]
            cell.alignment = st["center"]
            cell.border = st["border"]

    for row in data.schedule:
        values: list[object] = [
            row.date,
            row.weekday,
            row.display_name,
            row.host_house,
            row.planning_status,
            row.setup_window,
            row.cleanup_window,
        ]
        for slug, width in data.shift_type_columns:
            for i in range(width):
                values.append(row.cells.get((slug, i), ""))
        values += [row.needed, row.filled]
        ws.append(values)

        r = ws.max_row
        band = PatternFill("solid", fgColor=_LEGACY_ROW_BAND)
        for c in range(1, len(values) + 1):
            cell = ws.cell(row=r, column=c)
            cell.font = st["body"]
            cell.border = st["border"]
            cell.alignment = st["left"]
            # SP26 banded the whole party row lavender and left the DATE cell to
            # carry the weekday colour. Same here, so the eye lands on column A
            # to find "which Tuesday" exactly as it did last year.
            if c > 1:
                cell.fill = band
            text = cell.value
            if isinstance(text, str):
                if text == export_svc.UNFILLED:
                    cell.font = st["alarm"]
                elif text.endswith(export_svc.STRIKE_MARKER):
                    cell.fill = st["strike_fill"]
                    cell.font = st["strike_font"]

        # The DATE cell, tinted by weekday against the legend above. Days the
        # chapter does not normally throw parties on stay white, which is what
        # SP26 did with its one Thursday — an unusual night should look unusual.
        day_fill = _LEGACY_DAY_FILL.get(row.weekday)
        if day_fill:
            ws.cell(row=r, column=1).fill = PatternFill("solid", fgColor=day_fill)
        if row.planning_status == "placeholder":
            ws.cell(row=r, column=5).fill = PatternFill("solid", fgColor=_LEGACY_PLACEHOLDER)
        if row.filled < row.needed:
            ws.cell(row=r, column=len(values)).font = st["alarm"]

    # Merge each job's heading across its slot columns. Without this the long
    # "(NEXT MORNING, before 12:00)" is clipped at the first column's width and
    # the one thing it exists to say is the part that gets cut off.
    col = len(header_a) + 1
    for _slug, width in data.shift_type_columns:
        if width > 1:
            ws.merge_cells(
                start_row=header_top,
                start_column=col,
                end_row=header_top,
                end_column=col + width - 1,
            )
        col += width

    ws.freeze_panes = "D7"
    widths = [11, 5, 30, 15, 12, 30, 30] + [18] * (len(row1) - 9) + [8, 7]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


TALLY_SUBTITLE = (
    "Auto-generated — do not type here, it is rebuilt on every refresh. "
    "TOTAL is how many shifts. LOAD is the weighted figure the target is set in — "
    "a setup counts 0.7 because it is a 2h block you pick, so 17 setups is a full "
    "quota, not 143% of one. DJ nights and strike make-ups are real nights but "
    "count toward neither — see NIGHTS ON SITE."
)
BY_BROTHER_SUBTITLE = (
    "Auto-generated — do not type here, it is rebuilt on every refresh. "
    "Find your name. WORKED ON is the day you actually turn up — for cleanup that "
    "is the MORNING AFTER the party."
)


def _vs_target(row: export_svc.TallyRow) -> str:
    """The vs-target cell. ONE definition, used by both the workbook and the push.

    It was two, and they drifted: the workbook divided LOAD by target while the
    push payload still divided the headcount by it, so the file on the desktop
    said one setup-heavy brother was at 100% of quota and the sheet the chapter reads said
    143%. Thirty of sixty-seven rows disagreed, and the one that mattered most
    was the one people would argue about.

    LOAD, never TOTAL. Both mean "how much did he work"; only LOAD is in the
    units the target is set in, because a setup counts 0.7.
    """
    if row.target > 0:
        return f"{row.effort / row.target * 100:.0f}%"
    return "exempt" if row.exempt else ""


def _write_tally(ws, data: export_svc.SemesterExport, st: dict) -> None:  # noqa: ANN001
    from openpyxl.utils import get_column_letter

    types = [slug for slug, _ in data.shift_type_columns if slug != "dj"]
    # NIGHTS sits next to TOTAL rather than out past the uncounted columns. The
    # two answer the same question — how much did he do — and separating them by
    # four columns is how a DJ on 21 nights reads as somebody who did nothing.
    header = (
        ["Brother", "PC", "Class"]
        + [export_svc._DISPLAY_LABEL.get(s, s.upper()) for s in types]
        + [
            "TOTAL",
            "LOAD",
            "NIGHTS ON SITE",
            "Target",
            "vs target",
            "DJ (uncounted)",
            "Strike (uncounted)",
            "Note",
        ]
    )
    ws.append([f"SHIFT TALLY — {data.semester_name}"])
    ws.cell(row=1, column=1).font = st["title"]
    ws.append([TALLY_SUBTITLE])
    ws.cell(row=2, column=1).font = st["dim"]
    ws.append([])
    ws.append(header)
    head_row = 4
    for c in range(1, len(header) + 1):
        cell = ws.cell(row=head_row, column=c)
        cell.font = st["header"]
        cell.fill = st["header_fill"]
        cell.alignment = st["center"]
        cell.border = st["border"]

    total_col = 3 + len(types) + 1
    load_col = total_col + 1
    nights_col = total_col + 2
    vs_col = total_col + 4
    strike_col = total_col + 6
    note_col = len(header)

    for i, row in enumerate(data.tally):
        vs = _vs_target(row)
        ws.append(
            [row.display_name, row.pledge_class, row.class_label]
            + [row.per_type.get(s, 0) for s in types]
            + [
                row.counted_total,
                row.effort,
                row.nights_on_site,
                round(row.target, 1) if row.target else "",
                vs,
                row.dj_shifts,
                row.strike_shifts,
                row.note,
            ]
        )
        r = ws.max_row
        for c in range(1, len(header) + 1):
            cell = ws.cell(row=r, column=c)
            cell.font = st["body"]
            cell.border = st["border"]
            # Zebra banding. 67 rows of numbers with no rule is where a reader
            # slips onto the wrong line and then argues about somebody else's
            # total.
            if i % 2:
                cell.fill = st["band"]
        ws.cell(row=r, column=total_col).font = st["total"]
        ws.cell(row=r, column=load_col).font = st["total"]
        ws.cell(row=r, column=nights_col).font = st["total"]
        if row.exempt:
            # Not an alarm. An exempt officer at zero is the system working, and
            # the old sheet's habit of rendering low counts as delinquency is
            # exactly what makes a roll unreadable.
            for c in range(1, len(header) + 1):
                ws.cell(row=r, column=c).font = st["dim"]
            ws.cell(row=r, column=vs_col).font = st["brass"]
        elif row.target > 0:
            share = row.effort / row.target
            # Brass for over quota, dim for well under. Never oxblood: being
            # under target is usually the fill's doing, not the member's, and
            # flagging it red starts an argument the data cannot settle.
            if share >= 1.15 or share <= 0.6:
                ws.cell(row=r, column=vs_col).font = st["brass"]
        if row.strike_shifts:
            ws.cell(row=r, column=strike_col).fill = st["strike_fill"]
            ws.cell(row=r, column=strike_col).font = st["strike_font"]
        ws.cell(row=r, column=note_col).alignment = st["wrap"]

    ws.freeze_panes = ws.cell(row=head_row + 1, column=4).coordinate
    widths = [24, 10, 11] + [9] * len(types) + [8, 8, 15, 8, 10, 15, 17, 52]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _write_by_brother(ws, data: export_svc.SemesterExport, st: dict) -> None:  # noqa: ANN001
    from openpyxl.utils import get_column_letter

    header = ["Brother", "PC", "Class", "Party date", "Event", "Job", "Slot", "WORKED ON"]
    ws.append([f"EVERY SHIFT, BY BROTHER — {data.semester_name}"])
    ws.cell(row=1, column=1).font = st["title"]
    ws.append([BY_BROTHER_SUBTITLE])
    ws.cell(row=2, column=1).font = st["dim"]
    ws.append([])
    ws.append(header)
    head_row = 4
    for c in range(1, len(header) + 1):
        cell = ws.cell(row=head_row, column=c)
        cell.font = st["header"]
        cell.fill = st["header_fill"]
        cell.alignment = st["center"]
        cell.border = st["border"]

    # Band by PERSON, not by row. 589 alternating rows is noise; alternating
    # per brother draws the line where the reader's eye needs it — at the point
    # somebody else's shifts begin.
    shade = False
    previous: str | None = None
    for row in data.by_brother:
        if row.display_name != previous:
            shade = not shade
            previous = row.display_name
        ws.append(
            [
                row.display_name,
                row.pledge_class,
                row.class_label,
                row.date,
                row.event,
                row.shift_type + (export_svc.STRIKE_MARKER if row.is_strike else ""),
                row.slot_index + 1,
                row.worked_on,
            ]
        )
        r = ws.max_row
        for c in range(1, len(header) + 1):
            cell = ws.cell(row=r, column=c)
            cell.font = st["body"]
            cell.border = st["border"]
            if shade:
                cell.fill = st["band"]
        # The worked-on date is the single most valuable cell in the export:
        # cleanup is the MORNING AFTER the party, and a missed shift is a strike.
        if row.worked_on_date != row.date:
            ws.cell(row=r, column=8).font = st["brass"]
        if row.is_strike:
            ws.cell(row=r, column=6).fill = st["strike_fill"]
            ws.cell(row=r, column=6).font = st["strike_font"]

    ws.freeze_panes = ws.cell(row=head_row + 1, column=1).coordinate
    for i, w in enumerate([24, 10, 11, 12, 30, 22, 6, 46], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


@app.command("sheet")
def sheet(
    ctx: typer.Context,
    out: Annotated[Path, typer.Option("--out", help="Path to write the .xlsx to.")],
    semester: Annotated[
        str | None, typer.Option("--semester", help="Defaults to the current semester.")
    ] = None,
) -> None:
    """Write the semester schedule to a .xlsx workbook.

    Example:
        risk export sheet --semester FA26 --out ~/Desktop/FA26-risk.xlsx
    """
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)

    if semester is not None:
        sem = semesters_repo.get_by_name(conn, semester)
        if sem is None:
            emit_error("semester.not_found", f"No semester named {semester!r}.", mode=mode)
            return
    else:
        sem = semesters_repo.get_current(conn)
        if sem is None:
            emit_error(
                "semester.no_current",
                "No --semester given and no current semester set.",
                mode=mode,
            )
            return

    data = export_svc.build(conn, semester_id=sem.id)

    from openpyxl import Workbook

    st = _styles()
    wb = Workbook()
    _write_schedule(wb.active, data, st)
    wb.active.title = "Schedule"
    _write_tally(wb.create_sheet("Tally"), data, st)
    _write_by_brother(wb.create_sheet("By Brother"), data, st)
    # Last, so it sits at the right-hand end of the tab strip — it is the tab
    # the chair writes in, not one the chapter reads.
    _write_notes(wb.create_sheet(NOTES_TAB), data, st)
    _write_scratch(wb.create_sheet(SCRATCH_TAB), data, st)

    out = out.expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)

    needed = sum(r.needed for r in data.schedule)
    filled = sum(r.filled for r in data.schedule)
    emit_success(
        {
            "semester": sem.name,
            "path": str(out),
            "tabs": ["Schedule", "Tally", "By Brother", NOTES_TAB, SCRATCH_TAB],
            "events": len(data.schedule),
            "members": len(data.tally),
            "shift_rows": len(data.by_brother),
            "counted_slots": {"needed": needed, "filled": filled},
            "targets": {
                "senior": round(data.senior_target, 2),
                "underclass": round(data.underclass_target, 2),
            },
        },
        mode=mode,
    )


GENERATED_TABS = ("Schedule", "Tally", "By Brother", NOTES_TAB)
"""Tabs a push rebuilds.

Named explicitly rather than "every tab in the workbook", so anything a chair
adds by hand — a scratch tab, a copy of last year — survives a refresh. All four
of these are derived from the database, notes included: the app owns them now,
so mirroring is correct and there is nothing here to preserve.
"""


def _gog(args: list[str], *, account: str) -> tuple[int, str]:
    """Run a gog command, returning (exit code, combined output).

    Resolved through ``shutil.which`` rather than assumed on PATH. This runs
    from a terminal today, where /opt/homebrew/bin is present — but the moment
    anything calls it from a GUI-launched process it would not be, and a
    FileNotFoundError three layers down is a worse error than the one below.
    """
    import shutil
    import subprocess

    binary = shutil.which("gog")
    if binary is None:
        return 127, "gog not found on PATH (brew install gogcli, or add /opt/homebrew/bin)"
    proc = subprocess.run(  # noqa: S603
        [binary, *args, "-a", account],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, (proc.stdout + proc.stderr).strip()


@app.command("push")
def push(
    ctx: typer.Context,
    sheet_id: Annotated[str, typer.Option("--sheet-id", help="Target Google Sheet ID.")],
    account: Annotated[str, typer.Option("--account", help="gog account to push as.")],
    semester: Annotated[
        str | None, typer.Option("--semester", help="Defaults to the current semester.")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print what would be pushed, write nothing.")
    ] = False,
) -> None:
    """Refresh the generated tabs of an existing Google Sheet, in place.

    Updates Schedule, Tally and By Brother by name and leaves every other tab
    alone — which is the entire reason this exists rather than
    ``gog drive upload --replace``. That flag swaps the whole FILE: it keeps the
    URL and the sharing, and it deletes the chair's notes tab in the same
    breath, silently, on a command whose name suggests it is updating content.

    The sheet keeps its ID, so the link shared with the chapter stays good all
    semester.

    Example:
        risk export push --sheet-id 1MXNIz8... --account colin@example.com
    """
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)

    if semester is not None:
        sem = semesters_repo.get_by_name(conn, semester)
        if sem is None:
            emit_error("semester.not_found", f"No semester named {semester!r}.", mode=mode)
            return
    else:
        sem = semesters_repo.get_current(conn)
        if sem is None:
            emit_error(
                "semester.no_current",
                "No --semester given and no current semester set.",
                mode=mode,
            )
            return

    data = export_svc.build(conn, semester_id=sem.id)
    tabs = _tab_values(data)

    # Refuse to touch a sheet whose tabs are not the ones we think they are.
    # Pushing "Schedule" into a workbook that has no Schedule tab would create
    # one and leave the real grid stale beside it.
    code, meta = _gog(["sheets", "metadata", sheet_id, "-p"], account=account)
    if code != 0:
        emit_error("push.metadata_failed", meta, mode=mode)
        return
    # `gog sheets metadata -p` prints a TSV table whose first row is the literal
    # header "ID<tab>TITLE<tab>ROWS<tab>COLS". Keeping only rows whose first
    # field is a sheet id drops it — otherwise "TITLE" is reported back to the
    # chair as a tab that was left untouched, which is the kind of small lie
    # that makes someone stop trusting the rest of the output.
    present = {
        line.split("\t")[1]
        for line in meta.splitlines()
        if line.count("\t") >= 2 and line.split("\t")[0].strip().isdigit()
    }
    missing = [t for t in GENERATED_TABS if t not in present]
    if missing:
        emit_error(
            "push.tab_missing",
            f"sheet {sheet_id} has no tab(s) {missing}. Create the sheet with "
            f"`risk export sheet` + `gog drive upload --convert-to sheet` first.",
            mode=mode,
        )
        return

    plan = [
        {"tab": name, "rows": len(rows), "cols": max(len(r) for r in rows)}
        for name, rows in tabs.items()
    ]
    if dry_run:
        emit_success(
            {
                "sheet_id": sheet_id,
                "dry_run": True,
                "would_update": plan,
                "left_untouched": sorted(present - set(GENERATED_TABS)),
            },
            mode=mode,
        )
        return

    import json
    import tempfile

    for name, rows in tabs.items():
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(rows, fh)
            payload = fh.name
        # Clear first: the new grid can be SHORTER than the old one (an event
        # cancelled, a brother gone alumni), and update alone leaves the tail of
        # the previous push sitting underneath as if it were current data.
        code, out = _gog(["sheets", "clear", sheet_id, f"'{name}'!A:Z", "-y"], account=account)
        if code != 0:
            emit_error("push.clear_failed", f"{name}: {out}", mode=mode)
            return
        code, out = _gog(
            [
                "sheets",
                "update",
                sheet_id,
                f"'{name}'!A1",
                # RAW, not the USER_ENTERED default: under USER_ENTERED, Sheets
                # reinterprets what it is given — ISO dates get reformatted to
                # the sheet locale and stop sorting as text, "88%" becomes 0.88,
                # and any cell starting with = + or - is parsed as a formula.
                "--input",
                "RAW",
                "--values-json",
                f"@{payload}",
            ],
            account=account,
        )
        if code != 0:
            emit_error("push.update_failed", f"{name}: {out}", mode=mode)
            return

    emit_success(
        {
            "sheet_id": sheet_id,
            "url": f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit",
            "updated": plan,
            "left_untouched": sorted(present - set(GENERATED_TABS)),
        },
        mode=mode,
    )


def _tab_values(data: export_svc.SemesterExport) -> dict[str, list[list[str]]]:
    """The generated tabs as plain string grids, for the Sheets API.

    Values only — a push refreshes WHAT the sheet says, not how it looks. The
    fills and merges were set when the workbook was first converted and Sheets
    keeps them on a values update, so the chapter's colour key survives every
    refresh without being re-sent 43 rows at a time.
    """
    header_a = ["Date", "Day", "Event", "House", "Status", "Setup window", "Cleanup window"]
    row1 = list(header_a)
    row2 = [""] * len(header_a)
    for slug, width in data.shift_type_columns:
        label = data.column_headers.get(slug, export_svc._DISPLAY_LABEL.get(slug, slug.upper()))
        for i in range(width):
            row1.append(label if i == 0 else "")
            row2.append(str(i + 1))
    row1 += ["Needed", "Filled"]
    row2 += ["", ""]

    legend = [
        "TUESDAY",
        "FRIDAY",
        "SATURDAY",
        "PLACEHOLDER",
        "PLEDGING",
        "UNFILLED",
        "(strike) = make-up shift, does not count",
    ]
    schedule: list[list[str]] = [
        [f"RISK {data.semester_name}"],
        legend,
        [""] * len(legend),
        [],
        row1,
        row2,
    ]
    for row in data.schedule:
        values = [
            row.date,
            row.weekday,
            row.display_name,
            row.host_house,
            row.planning_status,
            row.setup_window,
            row.cleanup_window,
        ]
        for slug, width in data.shift_type_columns:
            values.extend(row.cells.get((slug, i), "") for i in range(width))
        values += [str(row.needed), str(row.filled)]
        schedule.append(values)

    types = [slug for slug, _ in data.shift_type_columns if slug != "dj"]
    tally: list[list[str]] = [
        [f"SHIFT TALLY — {data.semester_name}"],
        [TALLY_SUBTITLE],
        [],
        ["Brother", "PC", "Class"]
        + [export_svc._DISPLAY_LABEL.get(s, s.upper()) for s in types]
        + [
            "TOTAL",
            "LOAD",
            "NIGHTS ON SITE",
            "Target",
            "vs target",
            "DJ (uncounted)",
            "Strike (uncounted)",
            "Note",
        ],
    ]
    for row in data.tally:
        vs = _vs_target(row)
        tally.append(
            [row.display_name, row.pledge_class, row.class_label]
            + [str(row.per_type.get(s, 0)) for s in types]
            + [
                str(row.counted_total),
                f"{row.effort:.1f}",
                str(row.nights_on_site),
                f"{row.target:.1f}" if row.target else "",
                vs,
                str(row.dj_shifts),
                str(row.strike_shifts),
                row.note,
            ]
        )

    by_brother: list[list[str]] = [
        [f"EVERY SHIFT, BY BROTHER — {data.semester_name}"],
        [BY_BROTHER_SUBTITLE],
        [],
        ["Brother", "PC", "Class", "Party date", "Event", "Job", "Slot", "WORKED ON"],
    ]
    for row in data.by_brother:
        by_brother.append(
            [
                row.display_name,
                row.pledge_class,
                row.class_label,
                row.date,
                row.event,
                row.shift_type + (export_svc.STRIKE_MARKER if row.is_strike else ""),
                str(row.slot_index + 1),
                row.worked_on,
            ]
        )

    return {
        "Schedule": schedule,
        "Tally": tally,
        "By Brother": by_brother,
        NOTES_TAB: _notes_values(data),
    }


PDF_CSS = """
@page {
  size: A3 landscape;
  margin: 12mm 10mm 14mm 10mm;
  @bottom-left { content: "Kappa Sigma Phi-Alpha — sober monitor schedule"; font: 8pt "Helvetica Neue"; color: #6b645c; }
  @bottom-right { content: "page " counter(page) " of " counter(pages); font: 8pt "Helvetica Neue"; color: #6b645c; }
}
body { font-family: "Helvetica Neue", Helvetica, Arial, sans-serif; color: #1C1814; margin: 0; }
h1 { font-size: 17pt; margin: 0 0 1mm 0; letter-spacing: .02em; }
.sub { font-size: 8.5pt; color: #6b645c; margin: 0 0 3mm 0; }
.key { font-size: 7.5pt; margin: 0 0 3mm 0; }
.key span { padding: 1px 6px; margin-right: 5px; border: .3pt solid #C9C0B2; }
table { border-collapse: collapse; width: 100%; font-size: 7pt; }
/* Repeat the header on every page — a wide grid whose headers appear once is
   unreadable from page two, which is where most of the term lives. */
thead { display: table-header-group; }
th, td { border: .3pt solid #C9C0B2; padding: 1.6pt 2.5pt; text-align: left; vertical-align: top; }
th { background: #EFE9DD; font-size: 6.5pt; text-transform: uppercase; letter-spacing: .04em; }
th.job { text-align: center; }
tr { page-break-inside: avoid; }
td.date { font-weight: 600; white-space: nowrap; }
td.win { font-size: 6pt; color: #4a443d; }
td.name { font-size: 6.8pt; }
.strike { background: #FFB74D; font-weight: 600; }
.unfilled { color: #6E1F1F; font-weight: 700; }
.ph td { color: #6b645c; font-style: italic; }
"""


def _pdf_html(data: export_svc.SemesterExport) -> str:
    """The Schedule grid as standalone HTML, for weasyprint.

    The PUBLIC artefact. It carries the roster and nothing else — no Tally, no
    per-member totals, no strike counts beside a name, no chair notes. Those
    live in the spreadsheet, which is now restricted to people with edit access.
    A brother needs to know which night he is working; he does not need to be
    able to audit everybody else's load, and publishing that invites exactly the
    argument the Tally exists to settle privately.
    """
    from html import escape

    day_bg = {"Tue": "#FF00FF", "Fri": "#00FF00", "Sat": "#FF9900"}
    head_a = ["Date", "Day", "Event", "House", "Setup window", "Cleanup window"]

    cols: list[tuple[str, int, str]] = []
    for slug, width in data.shift_type_columns:
        cols.append((slug, width, data.column_headers.get(slug, slug.upper())))

    out: list[str] = [
        "<h1>RISK — FALL 2026</h1>",
        '<p class="sub">Sober monitor schedule. Setup is worked in the days BEFORE the party; '
        "cleanup is the MORNING AFTER. Check the window columns — the date a shift is listed "
        "under is the party, not always the day you work.</p>",
        '<p class="key">'
        '<span style="background:#FF00FF">TUESDAY</span>'
        '<span style="background:#00FF00">FRIDAY</span>'
        '<span style="background:#FF9900">SATURDAY</span>'
        '<span style="background:#FFB74D">(strike) = make-up shift</span>'
        '<span style="color:#6E1F1F;font-weight:700">UNFILLED</span>'
        '<span style="color:#6b645c;font-style:italic">placeholder = may not happen</span>'
        "</p>",
        "<table><thead><tr>",
    ]
    for h in head_a:
        out.append(f'<th rowspan="2">{escape(h)}</th>')
    for _slug, width, label in cols:
        out.append(f'<th class="job" colspan="{width}">{escape(label)}</th>')
    out.append("</tr><tr>")
    for _slug, width, _label in cols:
        for i in range(width):
            out.append(f'<th class="job">{i + 1}</th>')
    out.append("</tr></thead><tbody>")

    for row in data.schedule:
        klass = ' class="ph"' if row.planning_status == "placeholder" else ""
        bg = day_bg.get(row.weekday)
        style = f' style="background:{bg}"' if bg else ""
        out.append(f"<tr{klass}>")
        out.append(f'<td class="date"{style}>{escape(row.date)}</td>')
        out.append(f"<td>{escape(row.weekday)}</td>")
        name = row.display_name + (" (placeholder)" if row.planning_status == "placeholder" else "")
        out.append(f"<td>{escape(name)}</td>")
        out.append(f"<td>{escape(row.host_house)}</td>")
        out.append(f'<td class="win">{escape(row.setup_window)}</td>')
        out.append(f'<td class="win">{escape(row.cleanup_window)}</td>')
        for slug, width, _label in cols:
            for i in range(width):
                v = row.cells.get((slug, i), "")
                if v == export_svc.UNFILLED:
                    out.append('<td class="name unfilled">UNFILLED</td>')
                elif v.endswith(export_svc.STRIKE_MARKER):
                    out.append(f'<td class="name strike">{escape(v)}</td>')
                else:
                    out.append(f'<td class="name">{escape(v)}</td>')
        out.append("</tr>")
    out.append("</tbody></table>")
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<style>{PDF_CSS}</style></head><body>{''.join(out)}</body></html>"
    )


@app.command("pdf")
def pdf(
    ctx: typer.Context,
    out: Annotated[Path, typer.Option("--out", help="Path to write the .pdf to.")],
    semester: Annotated[
        str | None, typer.Option("--semester", help="Defaults to the current semester.")
    ] = None,
    keep_html: Annotated[
        bool, typer.Option("--keep-html", help="Also write the intermediate .html.")
    ] = False,
) -> None:
    """Render the SCHEDULE ONLY to a PDF — the public-facing artefact.

    Deliberately not the whole workbook. The spreadsheet holds per-member totals,
    quota percentages, strike counts and the chair's notes; this holds the
    roster. A brother needs to know which night he is working, not to be able to
    audit everybody else's load.

    Example:
        risk export pdf --semester FA26 --out ~/Desktop/FA26-schedule.pdf
    """
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    if semester is not None:
        sem = semesters_repo.get_by_name(conn, semester)
        if sem is None:
            emit_error("semester.not_found", f"No semester named {semester!r}.", mode=mode)
            return
    else:
        sem = semesters_repo.get_current(conn)
        if sem is None:
            emit_error("semester.no_current", "No --semester and no current semester.", mode=mode)
            return

    weasy = shutil.which("weasyprint")
    if weasy is None:
        emit_error(
            "pdf.no_weasyprint",
            "weasyprint is not on PATH (brew install weasyprint).",
            mode=mode,
        )
        return

    data = export_svc.build(conn, semester_id=sem.id)
    out = out.expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    html_path = out.with_suffix(".html")
    html_path.write_text(_pdf_html(data), encoding="utf-8")
    result = subprocess.run(  # noqa: S603
        [weasy, str(html_path), str(out)], capture_output=True, text=True, check=False
    )
    if not keep_html:
        html_path.unlink(missing_ok=True)
    if result.returncode != 0:
        emit_error("pdf.render_failed", result.stderr.strip()[:400], mode=mode)
        return

    emit_success(
        {
            "semester": sem.name,
            "path": str(out),
            "size_kb": round(out.stat().st_size / 1024, 1),
            "events": len(data.schedule),
            "contains": "schedule grid only — no tally, no totals, no notes",
        },
        mode=mode,
    )
