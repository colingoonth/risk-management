"""Integration tests for Phase 7 strike-sheet ingest: dry-run + apply + idempotency."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from risk.db.connection import connect, transaction
from risk.db.schema import ensure_schema
from risk.repos import member_statuses as statuses_repo
from risk.repos import members as members_repo
from risk.repos import semesters as semesters_repo
from risk.repos import strikes as strikes_repo
from risk.services import ingest, semester_archive

pytestmark = pytest.mark.integration


def _world(tmp_path: Path):
    conn = connect(tmp_path / "ingest.db")
    ensure_schema(conn)
    sem = semesters_repo.insert(conn, name="SP26", starts_on="2026-01-15", ends_on="2026-05-15")
    with transaction(conn):
        semesters_repo.set_current(conn, "SP26")
    active = statuses_repo.get_by_slug(conn, "active")
    assert active is not None
    alice = members_repo.insert(conn, slug="alice", display_name="Alice", status_id=active.id)
    bob = members_repo.insert(conn, slug="bob", display_name="Bob", status_id=active.id)
    return conn, sem, alice, bob


def _payload(semester: str | None, entries: list[dict]) -> dict:
    p = {"ingest_type": "strikes", "entries": entries}
    if semester is not None:
        p["semester"] = semester
    return p


def test_load_rejects_wrong_type(tmp_path: Path) -> None:
    f = tmp_path / "bad.json"
    f.write_text(json.dumps({"ingest_type": "roster", "entries": []}))
    with pytest.raises(ValueError, match="ingest_type"):
        ingest.load_strike_sheet(f)


def test_preview_counts_new_and_no_op(tmp_path: Path) -> None:
    conn, _sem, _alice, _bob = _world(tmp_path)
    payload = _payload(
        "SP26",
        [
            {"member_slug": "alice", "issued_on": "2026-02-10", "reason": "no-show"},
            {"member_slug": "bob", "issued_on": "2026-02-11", "reason": "late"},
        ],
    )
    pv1 = ingest.preview_strike_sheet(conn, payload=payload)
    assert pv1.new_strikes == 2
    assert pv1.no_op_strikes == 0
    # Apply, then preview again — both should be no-ops.
    with transaction(conn):
        ingest.apply_strike_sheet(conn, payload=payload)
    pv2 = ingest.preview_strike_sheet(conn, payload=payload)
    assert pv2.new_strikes == 0
    assert pv2.no_op_strikes == 2


def test_apply_is_idempotent(tmp_path: Path) -> None:
    conn, sem, alice, _bob = _world(tmp_path)
    payload = _payload(
        "SP26",
        [
            {"member_slug": "alice", "issued_on": "2026-02-10", "reason": "no-show"},
        ],
    )
    with transaction(conn):
        r1 = ingest.apply_strike_sheet(conn, payload=payload)
    with transaction(conn):
        r2 = ingest.apply_strike_sheet(conn, payload=payload)
    assert len(r1.applied_strike_ids) == 1
    assert len(r2.applied_strike_ids) == 0
    assert strikes_repo.count_active(conn, member_id=alice, semester_id=sem) == 1


def test_member_not_found_collected(tmp_path: Path) -> None:
    conn, _sem, _alice, _bob = _world(tmp_path)
    payload = _payload(
        "SP26",
        [
            {"member_slug": "ghost", "issued_on": "2026-02-10", "reason": "x"},
            {"member_slug": "alice", "issued_on": "2026-02-11", "reason": "y"},
        ],
    )
    pv = ingest.preview_strike_sheet(conn, payload=payload)
    assert pv.member_not_found == ("ghost",)
    assert pv.new_strikes == 1


def test_missing_semester_rejected(tmp_path: Path) -> None:
    conn, _sem, _alice, _bob = _world(tmp_path)
    payload = _payload(
        None,
        [{"member_slug": "alice", "issued_on": "2026-02-10", "reason": "x"}],
    )
    with pytest.raises(ValueError, match="semester"):
        ingest.preview_strike_sheet(conn, payload=payload)


def test_override_wins_when_both_given(tmp_path: Path) -> None:
    conn, _sem, _alice, _bob = _world(tmp_path)
    semesters_repo.insert(conn, name="FA26", starts_on="2026-08-15", ends_on="2026-12-15")
    payload = _payload(
        "SP26",
        [{"member_slug": "alice", "issued_on": "2026-09-01", "reason": "x"}],
    )
    pv = ingest.preview_strike_sheet(conn, payload=payload, semester_name_override="FA26")
    assert pv.semester_name == "FA26"


def test_archive_semester_rejects_ingest(tmp_path: Path) -> None:
    conn, sem, _alice, _bob = _world(tmp_path)
    with transaction(conn):
        semester_archive.archive(conn, semester_id=sem, archived_at="2026-05-31")
    payload = _payload(
        "SP26",
        [{"member_slug": "alice", "issued_on": "2026-02-10", "reason": "x"}],
    )
    with pytest.raises(ValueError, match="archived"):
        ingest.preview_strike_sheet(conn, payload=payload)


def test_low_confidence_entries_flagged(tmp_path: Path) -> None:
    conn, _sem, _alice, _bob = _world(tmp_path)
    payload = _payload(
        "SP26",
        [
            {
                "member_slug": "alice",
                "issued_on": "2026-02-10",
                "reason": "ambiguous cell",
                "_parse_confidence": "low",
            }
        ],
    )
    pv = ingest.preview_strike_sheet(conn, payload=payload)
    assert pv.low_confidence_entries == ("alice",)
