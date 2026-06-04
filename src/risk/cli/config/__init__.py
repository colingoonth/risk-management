"""``risk config ...`` subapp tree."""

from __future__ import annotations

import typer

from risk.cli.config.event_type import app as event_type_app
from risk.cli.config.house import app as house_app
from risk.cli.config.pledge_mode import app as pledge_mode_app
from risk.cli.config.removal_method import app as removal_method_app
from risk.cli.config.role import app as role_app
from risk.cli.config.shift_type import app as shift_type_app

app = typer.Typer(help="Manage rules and reference data (ADR-009 config-as-data).")
app.add_typer(event_type_app, name="event-type")
app.add_typer(house_app, name="house")
app.add_typer(role_app, name="role")
app.add_typer(shift_type_app, name="shift-type")
app.add_typer(removal_method_app, name="removal-method")
app.add_typer(pledge_mode_app, name="pledge-mode")
