"""``risk db ...`` subcommands — backup + maintenance.

ADR R3.1-B (zero-cost runtime): no cloud backup tier; chair runs
``risk db backup PATH`` to take an online-consistent copy.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Annotated

import typer

from risk.cli._common import mode_from_ctx, open_conn
from risk.cli.output import emit_error, emit_success

app = typer.Typer(help="Database backup + maintenance.")


@app.command("backup")
def backup(
    ctx: typer.Context,
    dest: Annotated[
        Path,
        typer.Argument(help="Destination path for the backup .db file. Parent dirs auto-created."),
    ],
) -> None:
    """Hot-copy the live DB to ``dest`` via SQLite's online-backup API.

    Safe to run while other processes are reading/writing — SQLite's backup
    API takes a consistent snapshot without blocking writers.
    """
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)

    if dest.exists() and dest.is_dir():
        emit_error("backup.dest_is_dir", f"{dest} is a directory.", mode=mode)
        return

    dest.parent.mkdir(parents=True, exist_ok=True)

    # Write to a sibling temp file then atomic-rename, so a partial backup
    # never overwrites an existing good file and a stale dest gets replaced
    # cleanly (sqlite3.Connection.backup requires a clean target file).
    tmp = dest.with_name(dest.name + ".partial")
    tmp.unlink(missing_ok=True)
    dst_conn = sqlite3.connect(tmp)
    try:
        with dst_conn:
            conn.backup(dst_conn)
    finally:
        dst_conn.close()
    tmp.replace(dest)

    size = dest.stat().st_size
    emit_success({"dest": str(dest), "bytes": size}, mode=mode)
