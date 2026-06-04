"""Output rendering: Rich tables by default, ``--json`` envelope, ``--json-raw`` bare."""

from __future__ import annotations

import enum
import json
import sys
from collections.abc import Sequence
from typing import Any

from rich.console import Console
from rich.table import Table

stdout_console = Console()
stderr_console = Console(stderr=True)


class OutputMode(enum.Enum):
    HUMAN = "human"
    JSON = "json"  # envelope: {ok, data, error, warnings}
    JSON_RAW = "json_raw"  # bare resource(s)


def emit_success(data: Any, *, mode: OutputMode, table: Table | None = None) -> None:
    if mode is OutputMode.JSON:
        sys.stdout.write(json.dumps({"ok": True, "data": data, "error": None, "warnings": []}))
        sys.stdout.write("\n")
    elif mode is OutputMode.JSON_RAW:
        sys.stdout.write(json.dumps(data))
        sys.stdout.write("\n")
    elif table is not None:
        stdout_console.print(table)
    elif data is not None:
        stdout_console.print(data)


def emit_error(code: str, message: str, *, mode: OutputMode, exit_code: int = 1) -> None:
    if mode in (OutputMode.JSON, OutputMode.JSON_RAW):
        payload: dict[str, Any] = {
            "ok": False,
            "data": None,
            "error": {"code": code, "message": message},
            "warnings": [],
        }
        sys.stdout.write(json.dumps(payload))
        sys.stdout.write("\n")
    else:
        stderr_console.print(f"[red]error[/red] [{code}] {message}")
    raise SystemExit(exit_code)


def _cell(value: Any) -> str:
    if isinstance(value, bool):
        return "✓" if value else ""
    if value is None:
        return "—"
    return str(value)


def simple_lookup_table(
    title: str,
    rows: Sequence[Any],
    *,
    extra_cols: Sequence[tuple[str, str]] = (),
) -> Table:
    """Render a ``(slug, display_name)`` lookup. ``extra_cols`` = ((header, attr), ...)."""
    table = Table(title=title)
    table.add_column("ID", justify="right", style="dim")
    table.add_column("Slug")
    table.add_column("Display name")
    for header, _ in extra_cols:
        table.add_column(header)
    for r in rows:
        cells = [str(r.id), r.slug, r.display_name]
        for _, attr in extra_cols:
            cells.append(_cell(getattr(r, attr)))
        table.add_row(*cells)
    return table


def attr_table(title: str, rows: Sequence[Any], cols: Sequence[tuple[str, str]]) -> Table:
    """Render arbitrary row attributes; no implicit id/slug/display_name columns."""
    table = Table(title=title)
    for header, _ in cols:
        table.add_column(header)
    for r in rows:
        table.add_row(*[_cell(getattr(r, attr)) for _, attr in cols])
    return table


def semesters_table(rows: Sequence[Any]) -> Table:
    table = Table(title="Semesters")
    table.add_column("ID", justify="right", style="dim")
    table.add_column("Name")
    table.add_column("Starts")
    table.add_column("Ends")
    table.add_column("Pledge takeover")
    table.add_column("Current", justify="center")
    table.add_column("Archived")
    for s in rows:
        table.add_row(
            str(s.id),
            s.name,
            s.starts_on,
            s.ends_on,
            s.pledge_takeover_starts_on or "—",
            "✓" if s.is_current else "",
            s.archived_at or "—",
        )
    return table
