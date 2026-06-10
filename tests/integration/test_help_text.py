"""Regression tests for CLI ergonomics polish: --version, confirmation prompts, help text."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from risk import __version__
from risk.cli.main import _maybe_handle_version_eager, app

pytestmark = pytest.mark.integration


def test_version_eager_at_any_depth() -> None:
    """``risk event list --version`` (and similar) must succeed at any depth.

    The eager-handler intercepts ``--version`` before Typer parses the
    subcommand, so users don't have to remember the flag has to come first.
    """
    # The eager-handler is the load-bearing piece; tests the helper directly
    # so we don't depend on subprocess wiring.
    assert _maybe_handle_version_eager(["event", "list", "--version"]) is True
    assert _maybe_handle_version_eager(["swap", "cancel", "5", "--version"]) is True
    assert _maybe_handle_version_eager(["--version"]) is True

    # And nothing fires when --version is absent.
    assert _maybe_handle_version_eager(["event", "list"]) is False
    assert _maybe_handle_version_eager([]) is False


def test_version_root_flag_via_clirunner() -> None:
    """``risk --version`` still works via the root callback (is_eager=True)."""
    runner = CliRunner()
    res = runner.invoke(app, ["--version"])
    assert res.exit_code == 0
    assert __version__ in res.output


def _seed_db_for_swap(runner: CliRunner, db: Path) -> int:
    """Create a swap request and return its ID."""

    def _run(*args: str) -> dict[str, object]:
        res = runner.invoke(app, ["--json", "--db", str(db), *args])
        assert res.exit_code == 0, res.output
        return json.loads(res.output)  # type: ignore[no-any-return]

    _run("semester", "add", "SP26", "--starts", "2026-01-15", "--ends", "2026-05-15")
    _run("semester", "set-current", "SP26")
    _run("config", "house", "add", "main", "--display-name", "Main")
    # mixer + door are seeded by migration 0002.
    _run(
        "config",
        "event-type",
        "set-default",
        "mixer",
        "door",
        "--min",
        "1",
        "--target",
        "1",
    )
    _run("member", "add", "alice", "--display-name", "Alice")
    _run("member", "add", "bob", "--display-name", "Bob")
    _run(
        "event",
        "add",
        "--name",
        "Mixer",
        "--type",
        "mixer",
        "--date",
        "2026-02-01",
        "--host",
        "main",
    )
    aa = _run("event", "auto-assign", "Mixer")
    assignments = aa["data"]["assignments"]  # type: ignore[index]
    assert assignments, "expected at least one assignment"
    shift_id = None
    # Look up the shift held by alice; if she didn't get one, use whoever did.
    for a in assignments:  # type: ignore[union-attr]
        if a["member"] == "alice":
            # find shift id via shift list
            shifts = _run("shift", "list", "--event", "Mixer")
            for s in shifts["data"]:  # type: ignore[union-attr,index]
                if s["assigned_member_slug"] == "alice":
                    shift_id = s["id"]
                    break
            break
    if shift_id is None:
        shifts = _run("shift", "list", "--event", "Mixer")
        for s in shifts["data"]:  # type: ignore[union-attr,index]
            if s["assigned_member_slug"] is not None:
                shift_id = s["id"]
                break
    assert shift_id is not None
    req = _run("swap", "request", "--from-shift", str(shift_id), "--counterparty", "bob")
    return int(req["data"]["swap_request"]["id"])  # type: ignore[index]


def test_swap_cancel_with_yes_skips_prompt(tmp_path: Path) -> None:
    """``swap cancel --yes`` skips the confirmation and cancels."""
    runner = CliRunner()
    db = tmp_path / "swap_yes.db"
    req_id = _seed_db_for_swap(runner, db)

    res = runner.invoke(
        app,
        ["--json", "--db", str(db), "swap", "cancel", str(req_id), "--yes"],
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["ok"] is True
    assert payload["data"]["state"] == "cancelled"


def test_swap_cancel_prompts_without_yes(tmp_path: Path) -> None:
    """Without --yes, ``swap cancel`` prompts; rejecting aborts cleanly."""
    runner = CliRunner()
    db = tmp_path / "swap_prompt.db"
    req_id = _seed_db_for_swap(runner, db)

    # Decline the prompt.
    res = runner.invoke(
        app,
        ["--db", str(db), "swap", "cancel", str(req_id)],
        input="n\n",
    )
    # Aborted path exits 0 (user-cancelled is not an error).
    assert res.exit_code == 0, res.output
    assert "Cancel swap request" in res.output

    # The request should still be open.
    res2 = runner.invoke(app, ["--json", "--db", str(db), "swap", "list"])
    payload = json.loads(res2.output)
    states = [r["state"] for r in payload["data"]["swap_requests"]]
    assert "open" in states


def test_event_cancel_has_yes_flag(tmp_path: Path) -> None:
    """``event cancel`` accepts --yes and reports success."""
    runner = CliRunner()
    db = tmp_path / "event_cancel.db"

    def _run(*args: str) -> dict[str, object]:
        res = runner.invoke(app, ["--json", "--db", str(db), *args])
        assert res.exit_code == 0, res.output
        return json.loads(res.output)  # type: ignore[no-any-return]

    _run("semester", "add", "SP26", "--starts", "2026-01-15", "--ends", "2026-05-15")
    _run("semester", "set-current", "SP26")
    _run(
        "event",
        "add",
        "--name",
        "Mixer",
        "--type",
        "mixer",
        "--date",
        "2026-02-01",
    )
    payload = _run("event", "cancel", "Mixer", "--yes")
    assert payload["data"]["status"] == "cancelled"


def test_typer_argument_help_rendered() -> None:
    """Help text contains the new help= descriptions on arguments."""
    runner = CliRunner()

    # member set-role: member + role both got help strings.
    res = runner.invoke(app, ["member", "set-role", "--help"])
    assert res.exit_code == 0, res.output
    assert "Member slug, ID, or alias." in res.output
    assert "Role slug" in res.output

    # event auto-assign: event arg + docstring example.
    res = runner.invoke(app, ["event", "auto-assign", "--help"])
    assert res.exit_code == 0, res.output
    assert "Event ID or display name." in res.output
    assert "risk event auto-assign 1 --seed 42 --dry-run" in res.output

    # config house set-pref: three positional args all got help strings.
    res = runner.invoke(app, ["config", "house", "set-pref", "--help"])
    assert res.exit_code == 0, res.output
    assert "House slug" in res.output
    assert "Event type slug" in res.output
    assert "Shift type slug" in res.output


def test_destructive_commands_advertise_yes_flag() -> None:
    """``swap cancel``, ``event cancel``, ``semester unarchive`` all show --yes in --help."""
    runner = CliRunner()
    for argv in (
        ["swap", "cancel", "--help"],
        ["event", "cancel", "--help"],
        ["semester", "unarchive", "--help"],
    ):
        res = runner.invoke(app, argv)
        assert res.exit_code == 0, res.output
        assert "--yes" in res.output, f"--yes missing from {argv}"


def test_top_commands_have_examples() -> None:
    """Top-traffic commands have an `Example:` block in their --help."""
    runner = CliRunner()
    for argv in (
        ["event", "auto-assign", "--help"],
        ["strike", "issue", "--help"],
        ["swap", "request", "--help"],
        ["semester", "archive", "--help"],
        ["member", "add", "--help"],
    ):
        res = runner.invoke(app, argv)
        assert res.exit_code == 0, res.output
        assert "Example:" in res.output, f"Example missing from {argv}"
