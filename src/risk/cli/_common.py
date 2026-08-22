"""Shared CLI helpers (output mode resolution, connection opening)."""

from __future__ import annotations

import sqlite3
from typing import Any

import typer

from risk.cli.output import OutputMode
from risk.db.connection import connect, resolve_db_path
from risk.db.schema import ensure_schema


def mode_from_ctx(ctx: typer.Context) -> OutputMode:
    obj: dict[str, Any] = ctx.obj or {}
    mode = obj.get("output_mode", OutputMode.HUMAN)
    assert isinstance(mode, OutputMode)
    return mode


def open_conn(ctx: typer.Context) -> sqlite3.Connection:
    obj: dict[str, Any] = ctx.obj or {}
    db_path = resolve_db_path(obj.get("db_path"))
    conn = connect(db_path)
    ensure_schema(conn)
    result = conn.execute("PRAGMA quick_check").fetchone()
    if result[0] != "ok":
        from risk.cli.output import emit_error

        emit_error(
            "db.corruption",
            f"Database integrity check failed: {result[0]}. Run 'risk db doctor' for details.",
            mode=mode_from_ctx(ctx),
        )
    return conn
