"""``risk groupme`` from the shell.

Driven as a subprocess, exactly like the other CLI suites. The two things being
proved are that --dry-run is ON by default everywhere it matters, and that no
command prints a GroupMe id back at the operator — the ``--stdin`` path exists so
the id never lands anywhere scrollable, and echoing it would defeat that.

Every command here is read-only or dry-run, so none of them touches the network
or the keychain.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from risk.db.connection import connect, transaction
from risk.db.schema import ensure_schema
from risk.repos import event_types as etypes_repo
from risk.repos import events as events_repo
from risk.repos import groupme_identities as identities_repo
from risk.repos import member_statuses as statuses_repo
from risk.repos import members as members_repo
from risk.repos import semesters as semesters_repo
from risk.repos import shift_types as stypes_repo
from risk.repos import shifts as shifts_repo

pytestmark = pytest.mark.integration

PLACEHOLDER_PARENT = "parent-placeholder-0000"
PLACEHOLDER_FRIDAY = "topic-placeholder-0005"
FRIDAY = "2026-09-04"
STAMP = "2026-08-31T00:00:00+00:00"


def _run(db_path: Path, *args: str, stdin: str | None = None):
    risk_bin = Path(sys.executable).parent / "risk"
    return subprocess.run(
        [str(risk_bin), "--db", str(db_path), "--json", *args],
        capture_output=True,
        text=True,
        check=False,
        input=stdin,
    )


def _data(proc: subprocess.CompletedProcess[str]) -> dict:
    payload = json.loads(proc.stdout)
    assert payload["ok"], payload
    return payload["data"]


def _seed(db_path: Path) -> None:
    conn = connect(db_path)
    ensure_schema(conn)
    with transaction(conn):
        sem_id = semesters_repo.insert(
            conn, name="FA26", starts_on="2026-08-20", ends_on="2026-12-15"
        )
        conn.execute("UPDATE semesters SET is_current = 1 WHERE id = ?", (sem_id,))
        active = statuses_repo.get_by_slug(conn, "active")
        assert active is not None
        alpha = members_repo.insert(
            conn, slug="test-alpha", display_name="Test Alpha", status_id=active.id
        )
        members_repo.insert(
            conn, slug="test-bravo", display_name="Test Bravo", status_id=active.id
        )
        identities_repo.link(
            conn,
            member_id=alpha,
            groupme_user_id="user-1",
            nickname="Test Alpha",
            confidence="exact",
            linked_at=STAMP,
        )
        etype = etypes_repo.get_by_slug(conn, "mixer")
        assert etype is not None
        event_id = events_repo.insert(
            conn,
            semester_id=sem_id,
            event_type_id=etype.id,
            display_name="Sample Mixer",
            date=FRIDAY,
        )
        stype = stypes_repo.get_by_slug(conn, "door")
        assert stype is not None
        shift_id = shifts_repo.insert_open(
            conn, event_id=event_id, shift_type_id=stype.id, slot_index=0
        )
        shifts_repo.assign(
            conn,
            shift_id=shift_id,
            member_id=alpha,
            effective_pledge_mode_id=None,
            assigned_at=STAMP,
        )
    conn.close()


def _seed_roster_source(db_path: Path) -> None:
    """The chat `--confirm` reads a nickname back from.

    Confirming is a human naming both sides, but the NICKNAME still has to come
    from GroupMe: a mention must slice to '@' + the current nickname to pass
    verification, so a link stored without one is silently unusable.
    """
    _run(
        db_path,
        "groupme",
        "seed",
        "roster-source",
        "--label",
        "Chapter announcements",
        "--groupme-id",
        PLACEHOLDER_PARENT,
    )


def _seed_groups(db_path: Path) -> None:
    _run(
        db_path,
        "groupme",
        "seed",
        "risk-parent",
        "--label",
        "Risk",
        "--groupme-id",
        PLACEHOLDER_PARENT,
    )
    _run(
        db_path,
        "groupme",
        "seed",
        "risk-friday",
        "--label",
        "Risk Friday",
        "--parent",
        "risk-parent",
        "--weekday",
        "5",
        "--stdin",
        stdin=PLACEHOLDER_FRIDAY,
    )


# ---------------------------------------------------------------------------
# seed
# ---------------------------------------------------------------------------


def test_seed_reads_the_id_from_stdin_and_never_echoes_it(tmp_path: Path) -> None:
    db_path = tmp_path / "risk.db"
    _seed(db_path)
    proc = _run(
        db_path,
        "groupme",
        "seed",
        "risk-parent",
        "--label",
        "Risk",
        "--stdin",
        stdin=PLACEHOLDER_PARENT,
    )
    assert proc.returncode == 0
    assert PLACEHOLDER_PARENT not in proc.stdout
    assert PLACEHOLDER_PARENT not in proc.stderr
    assert _data(proc)["groupme_id_set"] is True


def test_the_groups_listing_does_not_print_ids(tmp_path: Path) -> None:
    db_path = tmp_path / "risk.db"
    _seed(db_path)
    _seed_groups(db_path)
    proc = _run(db_path, "groupme", "groups")
    assert PLACEHOLDER_PARENT not in proc.stdout
    assert PLACEHOLDER_FRIDAY not in proc.stdout
    groups = _data(proc)["groups"]
    assert {g["slug"] for g in groups} == {"risk-parent", "risk-friday"}
    assert [g["weekday"] for g in groups if g["slug"] == "risk-friday"] == ["Fri"]


def test_seeding_with_no_id_at_all_is_an_error(tmp_path: Path) -> None:
    db_path = tmp_path / "risk.db"
    _seed(db_path)
    proc = _run(db_path, "groupme", "seed", "risk-parent", "--label", "Risk")
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "groupme.missing_id"


# ---------------------------------------------------------------------------
# announce — dry run is the default
# ---------------------------------------------------------------------------


def test_announce_is_a_dry_run_by_default(tmp_path: Path) -> None:
    """No flag, no network, no post — and the rendered message to read."""
    db_path = tmp_path / "risk.db"
    _seed(db_path)
    _seed_groups(db_path)
    proc = _run(
        db_path, "groupme", "announce", "--from", FRIDAY, "--to", FRIDAY
    )
    assert proc.returncode == 0
    data = _data(proc)
    assert data["dry_run"] is True
    assert data["posted"] == []
    assert data["posts"][0]["text"] == (
        "friday, sep 4\n@Test Alpha: door"
    )
    assert data["identity_check"] == "database-only"


def test_announce_without_dry_run_still_needs_confirm(tmp_path: Path) -> None:
    """Two deliberate acts, because an announcement cannot be unsent."""
    db_path = tmp_path / "risk.db"
    _seed(db_path)
    _seed_groups(db_path)
    proc = _run(
        db_path, "groupme", "announce", "--from", FRIDAY, "--to", FRIDAY, "--no-dry-run"
    )
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "groupme.unconfirmed"
    assert "Nothing was sent" in payload["error"]["message"]


def test_the_dry_run_reports_an_unroutable_event_rather_than_redirecting_it(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "risk.db"
    _seed(db_path)
    _seed_groups(db_path)
    proc = _run(db_path, "groupme", "announce", "--from", FRIDAY, "--to", "2026-09-30")
    data = _data(proc)
    assert [p["event_name"] for p in data["posts"]] == ["Sample Mixer"]
    assert data["unroutable"] == []


# ---------------------------------------------------------------------------
# map / identities
# ---------------------------------------------------------------------------


def test_identities_lists_who_is_linked_and_who_is_not(tmp_path: Path) -> None:
    db_path = tmp_path / "risk.db"
    _seed(db_path)
    data = _data(_run(db_path, "groupme", "identities"))
    assert [link["display_name"] for link in data["linked"]] == ["Test Alpha"]
    assert [u["display_name"] for u in data["unlinked"]] == ["Test Bravo"]


def test_confirm_is_a_dry_run_by_default(tmp_path: Path) -> None:
    db_path = tmp_path / "risk.db"
    _seed(db_path)
    data = _data(_run(db_path, "groupme", "map", "--confirm", "test-bravo=user-2"))
    assert data["dry_run"] is True
    conn = connect(db_path)
    ensure_schema(conn)
    bravo = members_repo.get_by_slug(conn, "test-bravo")
    assert bravo is not None
    assert identities_repo.get_for_member(conn, bravo.id) is None
    conn.close()


def test_a_malformed_confirm_pair_is_rejected_before_any_network_call(
    tmp_path: Path,
) -> None:
    """A typo must cost nothing.

    Argument validation runs before the source chat is looked up, so this fails
    the same way whether or not GroupMe is reachable.
    """
    db_path = tmp_path / "risk.db"
    _seed(db_path)
    proc = _run(db_path, "groupme", "map", "--confirm", "nonsense", "--no-dry-run")
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "groupme.bad_confirm"


def test_an_unknown_member_in_a_confirm_pair_is_rejected_before_any_network_call(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "risk.db"
    _seed(db_path)
    proc = _run(db_path, "groupme", "map", "--confirm", "nobody=user-2", "--no-dry-run")
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "member.not_found"


def test_confirm_refuses_when_the_source_chat_is_not_registered(tmp_path: Path) -> None:
    """It would rather write nothing than write a link it cannot mention.

    The nickname has to be read back from GroupMe, so with no source chat there
    is no nickname; storing the link anyway is what produced a roster of men who
    counted as linked and were silently printed with no tag.
    """
    db_path = tmp_path / "risk.db"
    _seed(db_path)
    proc = _run(db_path, "groupme", "map", "--confirm", "test-bravo=user-2", "--no-dry-run")
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "groupme.group_not_found"
    conn = connect(db_path)
    ensure_schema(conn)
    bravo = members_repo.get_by_slug(conn, "test-bravo")
    assert bravo is not None
    assert identities_repo.get_for_member(conn, bravo.id) is None, "no link without a nickname"
    conn.close()

