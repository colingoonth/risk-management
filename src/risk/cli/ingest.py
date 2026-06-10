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


def _roster_summary(preview: svc.GformPreview) -> dict[str, object]:
    """Compact, render-friendly view of a roster preview (omits the full row dump)."""
    return {
        "semester": preview.semester_name,
        "base_year": preview.base_year,
        "total_rows": len(preview.rows),
        "new_members": len(preview.new_members),
        "existing_members": len(preview.existing_members),
        "exec_assignments": len(preview.exec_assignments),
        "unmapped_rising_class": list(preview.unmapped_rising_class),
        "unmapped_pledge_class": list(preview.unmapped_pledge_class),
    }


@app.command("gform-roster")
def gform_roster(
    ctx: typer.Context,
    path: Annotated[Path, typer.Argument(help="Path to the Google Form roster CSV export.")],
    semester: Annotated[
        str,
        typer.Option("--semester", help="Target semester name (e.g. FA26). Required."),
    ],
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Preview the roster diff without applying.")
    ] = False,
) -> None:
    """Ingest a Google-Form roster CSV (Full Name, Rising Class, PC, EC).

    Derives class_year from rising-class against the target semester, stores the
    pledge class, and assigns the exec role to EC members. Idempotent on slug.
    """
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    if not path.exists():
        emit_error("ingest.file_not_found", f"{path} does not exist.", mode=mode)
        return
    if dry_run:
        try:
            preview = svc.preview_gform_roster(conn, path=path, semester_name=semester)
        except (LookupError, ValueError) as exc:
            emit_error("ingest.preview_failed", str(exc), mode=mode)
            return
        emit_success({"preview": _roster_summary(preview), "dry_run": True}, mode=mode)
        return
    try:
        with transaction(conn):
            result = svc.apply_gform_roster(conn, path=path, semester_name=semester)
    except (LookupError, ValueError) as exc:
        emit_error("ingest.apply_failed", str(exc), mode=mode)
        return
    emit_success(
        {
            "preview": _roster_summary(result.preview),
            "inserted_members": len(result.inserted_member_ids),
            "exec_roles_set": result.exec_roles_set,
        },
        mode=mode,
    )
