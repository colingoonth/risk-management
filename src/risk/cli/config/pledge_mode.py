"""``risk config pledge-mode ...`` — read-only (closed set per resolver code)."""

from __future__ import annotations

from dataclasses import asdict

import typer

from risk.cli._common import mode_from_ctx, open_conn
from risk.cli.output import emit_success, simple_lookup_table
from risk.repos import pledge_modes as repo

app = typer.Typer(help="Pledge modes are a closed set; this command is read-only.")


@app.command("list")
def list_(ctx: typer.Context) -> None:
    rows = repo.list_all_modes(open_conn(ctx))
    emit_success(
        [asdict(r) for r in rows],
        mode=mode_from_ctx(ctx),
        table=simple_lookup_table("Pledge modes", rows),
    )
