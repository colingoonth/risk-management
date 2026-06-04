"""Root Typer app for the ``risk`` CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from risk import __version__
from risk.cli.config import app as config_app
from risk.cli.member import app as member_app
from risk.cli.output import OutputMode
from risk.cli.semester import app as semester_app

app = typer.Typer(
    name="risk",
    help="Sober-monitor shift assignment + strike tracking.",
)
app.add_typer(config_app, name="config")
app.add_typer(semester_app, name="semester")
app.add_typer(member_app, name="member")


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    db: Annotated[
        Path | None,
        typer.Option(
            "--db", help="Override the SQLite DB path (else $RISK_DB_PATH or ./data/risk.db)."
        ),
    ] = None,
    json_out: Annotated[bool, typer.Option("--json", help="Emit JSON envelope on stdout.")] = False,
    json_raw: Annotated[
        bool, typer.Option("--json-raw", help="Emit bare-resource JSON on stdout (jq-friendly).")
    ] = False,
    version: Annotated[bool, typer.Option("--version", help="Print version and exit.")] = False,
) -> None:
    """Global flags resolve before any subcommand runs."""
    if version:
        typer.echo(f"risk {__version__}")
        raise typer.Exit()
    if json_out and json_raw:
        typer.echo("error: --json and --json-raw are mutually exclusive", err=True)
        raise typer.Exit(code=2)
    mode = OutputMode.JSON if json_out else OutputMode.JSON_RAW if json_raw else OutputMode.HUMAN
    ctx.obj = {"db_path": db, "output_mode": mode}
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()
