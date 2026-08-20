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
_STRIKE_BG = "F2E6CE"
_HEADER_BG = "EFE9DD"


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
        "border": Border(bottom=thin),
        "center": Alignment(horizontal="center", vertical="center"),
        "left": Alignment(horizontal="left", vertical="center"),
        "wrap": Alignment(horizontal="left", vertical="top", wrap_text=True),
    }


def _write_schedule(ws, data: export_svc.SemesterExport, st: dict) -> None:  # noqa: ANN001
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

    ws.append(row1)
    ws.append(row2)
    for r in (1, 2):
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
        for c in range(1, len(values) + 1):
            cell = ws.cell(row=r, column=c)
            cell.font = st["body"]
            cell.border = st["border"]
            cell.alignment = st["left"]
            text = cell.value
            if isinstance(text, str):
                if text == export_svc.UNFILLED:
                    cell.font = st["alarm"]
                elif text.endswith(export_svc.STRIKE_MARKER):
                    cell.fill = st["strike_fill"]
                    cell.font = st["brass"]
        # A placeholder is dimmer than a real party, because half of them will
        # not happen and the reader needs to know which half he is looking at.
        if row.planning_status == "placeholder":
            ws.cell(row=r, column=5).font = st["brass"]
        if row.filled < row.needed:
            ws.cell(row=r, column=len(values)).font = st["alarm"]

    # Merge each job's heading across its slot columns. Without this the long
    # "(NEXT MORNING, before 12:00)" is clipped at the first column's width and
    # the one thing it exists to say is the part that gets cut off.
    col = len(header_a) + 1
    for _slug, width in data.shift_type_columns:
        if width > 1:
            ws.merge_cells(start_row=1, start_column=col, end_row=1, end_column=col + width - 1)
        col += width

    ws.freeze_panes = "D3"
    widths = [11, 5, 30, 15, 12, 30, 30] + [18] * (len(row1) - 9) + [8, 7]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _write_tally(ws, data: export_svc.SemesterExport, st: dict) -> None:  # noqa: ANN001
    from openpyxl.utils import get_column_letter

    types = [slug for slug, _ in data.shift_type_columns if slug != "dj"]
    header = (
        ["Brother", "PC", "Class"]
        + [export_svc._DISPLAY_LABEL.get(s, s.upper()) for s in types]
        + ["TOTAL", "Target", "vs target", "DJ (uncounted)", "Strike (uncounted)", "Note"]
    )
    ws.append(header)
    for c in range(1, len(header) + 1):
        cell = ws.cell(row=1, column=c)
        cell.font = st["header"]
        cell.fill = st["header_fill"]
        cell.alignment = st["center"]
        cell.border = st["border"]

    for row in data.tally:
        vs = ""
        if row.target > 0:
            vs = f"{row.counted_total / row.target * 100:.0f}%"
        elif row.exempt:
            vs = "exempt"
        ws.append(
            [row.display_name, row.pledge_class, row.class_label]
            + [row.per_type.get(s, 0) for s in types]
            + [
                row.counted_total,
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
        if row.exempt:
            # Not an alarm. An exempt officer at zero is the system working, and
            # the old sheet's habit of rendering low counts as delinquency is
            # exactly what makes a roll unreadable.
            for c in range(1, len(header) + 1):
                ws.cell(row=r, column=c).font = st["brass"]
        if row.strike_shifts:
            ws.cell(row=r, column=len(header) - 1).fill = st["strike_fill"]
        ws.cell(row=r, column=len(header)).alignment = st["wrap"]

    ws.freeze_panes = "A2"
    widths = [24, 10, 11] + [9] * len(types) + [8, 8, 10, 15, 17, 52]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _write_by_brother(ws, data: export_svc.SemesterExport, st: dict) -> None:  # noqa: ANN001
    from openpyxl.utils import get_column_letter

    header = ["Brother", "PC", "Class", "Party date", "Event", "Job", "Slot", "WORKED ON"]
    ws.append(header)
    for c in range(1, len(header) + 1):
        cell = ws.cell(row=1, column=c)
        cell.font = st["header"]
        cell.fill = st["header_fill"]
        cell.alignment = st["center"]
        cell.border = st["border"]

    for row in data.by_brother:
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
        # The worked-on date is the single most valuable cell in the export:
        # cleanup is the MORNING AFTER the party, and a missed shift is a strike.
        if row.worked_on != row.date:
            ws.cell(row=r, column=8).font = st["brass"]
        if row.is_strike:
            ws.cell(row=r, column=6).fill = st["strike_fill"]

    ws.freeze_panes = "A2"
    for i, w in enumerate([24, 10, 11, 12, 30, 16, 6, 14], start=1):
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

    out = out.expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)

    needed = sum(r.needed for r in data.schedule)
    filled = sum(r.filled for r in data.schedule)
    emit_success(
        {
            "semester": sem.name,
            "path": str(out),
            "tabs": ["Schedule", "Tally", "By Brother"],
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
