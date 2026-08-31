"""``risk-forwarder`` and the LaunchAgent that fires it.

The entrypoint is the whole contract with launchd: one cycle per invocation, an
exit code launchd can read, and a single summary line in a log file that lives
on a machine whose working tree is a public repo. What that line may contain is
as much a requirement as what the poller does.
"""

from __future__ import annotations

import json
import plistlib
import re
import sqlite3
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest

from risk import forwarder
from risk.db.connection import connect, transaction
from risk.db.schema import ensure_schema
from risk.repos import groupme_inbound as inbound_repo
from risk.services import groupme_forward as fwd
from risk.services import groupme_poll as poll
from tests.integration.test_groupme_forwarder import TUESDAY, FakeGroupMe, _seed_topics

pytestmark = pytest.mark.integration

PLIST_TEMPLATE = (
    Path(__file__).resolve().parents[2]
    / "packaging"
    / "launchd"
    / "com.colin.riskforwarder.plist.template"
)


@pytest.fixture(autouse=True)
def _no_ambient_cmux(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (fwd.SURFACE_ENV, fwd.CMUX_SAY_ENV, poll.HEARTBEAT_ENV, "RISK_DB_PATH"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def db_path(tmp_path: Path) -> Iterator[Path]:
    path = tmp_path / "cli.db"
    conn = connect(path)
    ensure_schema(conn)
    _seed_topics(conn, TUESDAY)
    conn.close()
    yield path


@pytest.fixture()
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeGroupMe:
    client = FakeGroupMe()
    monkeypatch.setattr(poll, "_default_list_messages", lambda: client.list_messages)
    return client


def _recorder(tmp_path: Path) -> tuple[Path, Path]:
    transcript = tmp_path / "sent.txt"
    script = tmp_path / "cmux-say"
    script.write_text(f'#!/bin/sh\nprintf "%s\\n" "$2" >> "{transcript}"\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script, transcript


def _open(db_path: Path) -> sqlite3.Connection:
    conn = connect(db_path)
    ensure_schema(conn)
    return conn


def test_one_invocation_polls_forwards_and_exits_zero(
    db_path: Path, fake: FakeGroupMe, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    script, transcript = _recorder(tmp_path)
    fake.add(TUESDAY.groupme_id, message_id=1, text="door needs a hand", name="Test Alpha")

    code = forwarder.main(
        [
            "--db-path",
            str(db_path),
            "--surface",
            "surface:test",
            "--cmux-say",
            str(script),
        ]
    )

    assert code == 0
    assert transcript.read_text().strip() == '"door needs a hand" - Test Alpha'
    assert "stored=1 dup=0 forwarded=1" in capsys.readouterr().out


def test_the_log_line_carries_no_message_text_sender_or_group_id(
    db_path: Path, fake: FakeGroupMe, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """This line goes into a file beside a public repo. Timestamps, slugs,
    codes and counts — nothing else."""
    script, _ = _recorder(tmp_path)
    fake.add(
        TUESDAY.groupme_id, message_id=1, text="a very identifying sentence", name="Test Alpha"
    )

    forwarder.main(
        ["--db-path", str(db_path), "--surface", "surface:test", "--cmux-say", str(script)]
    )

    line = capsys.readouterr().out
    assert "identifying" not in line
    assert "Test Alpha" not in line
    assert TUESDAY.groupme_id not in line
    assert TUESDAY.group_slug not in line or "failed=" in line


def test_a_second_run_with_nothing_new_forwards_nothing(
    db_path: Path, fake: FakeGroupMe, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """launchd fires this every 120 seconds forever. The quiet case is the
    common case and it must be a no-op."""
    script, transcript = _recorder(tmp_path)
    args = ["--db-path", str(db_path), "--surface", "surface:test", "--cmux-say", str(script)]
    fake.add(TUESDAY.groupme_id, message_id=1, text="once")
    forwarder.main(args)
    capsys.readouterr()

    assert forwarder.main(args) == 0
    assert "stored=0 dup=0 forwarded=0" in capsys.readouterr().out
    assert transcript.read_text().strip().count("\n") == 0


def test_no_forward_stores_without_touching_cmux(
    db_path: Path, fake: FakeGroupMe, tmp_path: Path
) -> None:
    script, transcript = _recorder(tmp_path)
    fake.add_run(TUESDAY.groupme_id, first=1, count=3, at=poll._utcnow())

    assert (
        forwarder.main(
            [
                "--db-path",
                str(db_path),
                "--surface",
                "surface:test",
                "--cmux-say",
                str(script),
                "--no-forward",
            ]
        )
        == 0
    )

    assert not transcript.exists()
    conn = _open(db_path)
    assert inbound_repo.count_unforwarded(conn) == 3
    conn.close()


def test_forward_only_drains_the_queue_without_calling_groupme(
    db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The flag for "cmux is back up, send me what I missed" — and it must work
    even when the GroupMe client is not installed at all."""

    def _no_client() -> poll.ListMessages:
        raise ImportError("risk.services.groupme")

    monkeypatch.setattr(poll, "_default_list_messages", _no_client)
    script, transcript = _recorder(tmp_path)
    conn = _open(db_path)
    with transaction(conn):
        inbound_repo.insert_if_new(
            conn,
            groupme_message_id="55",
            group_slug=TUESDAY.group_slug,
            sender_name="Test Bravo",
            text="left over",
            created_at="2026-09-04T01:00:00+00:00",
            received_at="2026-09-04T01:00:00+00:00",
        )
    conn.close()

    code = forwarder.main(
        [
            "--db-path",
            str(db_path),
            "--surface",
            "surface:test",
            "--cmux-say",
            str(script),
            "--forward-only",
        ]
    )

    assert code == 0
    assert transcript.read_text().strip() == '"left over" - Test Bravo'


def test_a_missing_client_exits_one_with_a_code_and_no_traceback(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _no_client() -> poll.ListMessages:
        raise ImportError("no module named risk.services.groupme at /some/path")

    monkeypatch.setattr(poll, "_default_list_messages", _no_client)

    assert forwarder.main(["--db-path", str(db_path)]) == 1
    captured = capsys.readouterr()
    assert captured.err.strip() == "risk-forwarder: client_unavailable"
    assert "/some/path" not in captured.err

    # And the lease is free again. A cycle that failed before it started must
    # not park the lease for its whole TTL — that would make the next three
    # LaunchAgent runs skip for no reason a log could explain.
    from risk.repos import groupme_poll_lease as lease_repo

    conn = _open(db_path)
    assert lease_repo.get(conn) is None
    conn.close()


def test_a_dead_cmux_still_exits_zero(
    db_path: Path, fake: FakeGroupMe, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Not a condition launchd can do anything about, and the messages are
    safely stored. A non-zero exit here would be noise in every log."""
    fake.add(TUESDAY.groupme_id, message_id=1)

    code = forwarder.main(
        [
            "--db-path",
            str(db_path),
            "--surface",
            "surface:test",
            "--cmux-say",
            str(tmp_path / "not-here"),
        ]
    )

    assert code == 0
    assert "cmux=cmux_binary_missing" in capsys.readouterr().out


def test_json_mode_is_one_parsable_line(
    db_path: Path, fake: FakeGroupMe, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.add_run(TUESDAY.groupme_id, first=1, count=2, at=poll._utcnow())

    forwarder.main(["--db-path", str(db_path), "--json", "--no-forward"])

    out = capsys.readouterr().out.strip()
    assert "\n" not in out
    payload = json.loads(out)
    assert payload["groups"][0]["stored"] == 2
    assert payload["groups"][0]["error_code"] is None
    assert payload["skipped_locked"] is False


def test_an_overlapping_run_says_so_and_exits_zero(
    db_path: Path, fake: FakeGroupMe, capsys: pytest.CaptureFixture[str]
) -> None:
    """launchd firing during a long catch-up is expected, not a fault."""
    from datetime import timedelta

    from risk.repos import groupme_poll_lease as lease_repo

    conn = _open(db_path)
    held_until = poll._utcnow() + timedelta(seconds=300)
    with transaction(conn):
        lease_repo.acquire(
            conn,
            holder="in-flight",
            now=poll.stamp(poll._utcnow()),
            expires_at=poll.stamp(held_until),
        )
    conn.close()

    assert forwarder.main(["--db-path", str(db_path)]) == 0
    assert "skipped=lease_held" in capsys.readouterr().out
    assert fake.calls == []


# ---------------------------------------------------------------------------
# The LaunchAgent template.
# ---------------------------------------------------------------------------


def test_the_plist_is_a_template_and_nothing_installs_it() -> None:
    """It carries machine-specific values, and this repo is public. A finished
    plist in the tree is a leak waiting to be committed, and an installer that
    ran on its own would be a background job Colin never asked for."""
    assert PLIST_TEMPLATE.name.endswith(".plist.template")
    assert not list(PLIST_TEMPLATE.parent.glob("*.plist"))
    body = PLIST_TEMPLATE.read_text()
    assert "__RISK_FORWARDER_BIN__" in body
    assert "__CMUX_SURFACE_ID__" in body
    assert "__HOME__" in body


def test_the_plist_fires_every_120_seconds_and_once_on_load() -> None:
    parsed = plistlib.loads(PLIST_TEMPLATE.read_bytes())
    assert parsed["Label"] == "com.colin.riskforwarder"
    assert parsed["StartInterval"] == 120
    assert parsed["RunAtLoad"] is True


def test_the_plist_is_a_trigger_and_not_a_daemon() -> None:
    """KeepAlive would restart the process the instant it exited — a poll loop
    with no interval, and a fast way to get rate-limited."""
    parsed = plistlib.loads(PLIST_TEMPLATE.read_bytes())
    assert "KeepAlive" not in parsed
    assert parsed["ProgramArguments"] == ["__RISK_FORWARDER_BIN__"]


def test_the_plist_puts_the_cmux_binary_and_the_keychain_tool_on_path() -> None:
    """launchd hands a job /usr/bin:/bin:/usr/sbin:/sbin. cmux-say is in
    ~/.local/bin and it would simply never be found."""
    env = plistlib.loads(PLIST_TEMPLATE.read_bytes())["EnvironmentVariables"]
    assert ".local/bin" in env["PATH"]
    assert "/usr/bin" in env["PATH"]


def test_the_plist_carries_no_secret_and_no_real_identifier() -> None:
    """The token comes from the keychain at run time and never from a plist,
    and every machine-specific value is still a placeholder."""
    body = PLIST_TEMPLATE.read_text()
    env = plistlib.loads(PLIST_TEMPLATE.read_bytes())["EnvironmentVariables"]
    assert not any("TOKEN" in key.upper() for key in env)
    assert env["RISK_CMUX_SURFACE"] == "__CMUX_SURFACE_ID__"
    assert not re.search(r"\b\d{5,}\b", body), "a long numeric id looks like a GroupMe id"
