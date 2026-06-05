"""``risk ingest ...`` subcommands (Phase 7)."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer

from risk.cli._common import mode_from_ctx, open_conn
from risk.cli.output import emit_error, emit_success
from risk.db.connection import transaction
from risk.services import ingest as svc

app = typer.Typer(help="Tier-2 ingest of Tier-1 (Claude-parsed) JSON files.")


@app.command("strike-sheet")
def strike_sheet(
    ctx: typer.Context,
    path: Annotated[Path, typer.Argument(help="Path to canonical JSON.")],
    semester: Annotated[
        str | None,
        typer.Option(
            "--semester",
            help="Override or provide the target semester name. Required if "
            "the JSON payload omits 'semester'.",
        ),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Preview diff without applying.")
    ] = False,
) -> None:
    """Ingest a strike-sheet JSON file (idempotent upsert)."""
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    if not path.exists():
        emit_error("ingest.file_not_found", f"{path} does not exist.", mode=mode)
        return
    try:
        payload = svc.load_strike_sheet(path)
    except ValueError as exc:
        emit_error("ingest.invalid_json", str(exc), mode=mode)
        return
    if dry_run:
        try:
            preview = svc.preview_strike_sheet(
                conn, payload=payload, semester_name_override=semester
            )
        except (LookupError, ValueError) as exc:
            emit_error("ingest.preview_failed", str(exc), mode=mode)
            return
        emit_success({"preview": asdict(preview), "dry_run": True}, mode=mode)
        return
    try:
        with transaction(conn):
            result = svc.apply_strike_sheet(
                conn, payload=payload, semester_name_override=semester
            )
    except (LookupError, ValueError) as exc:
        emit_error("ingest.apply_failed", str(exc), mode=mode)
        return
    emit_success(
        {
            "preview": asdict(result.preview),
            "applied_strike_ids": list(result.applied_strike_ids),
        },
        mode=mode,
    )
