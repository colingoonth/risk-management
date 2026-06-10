"""Integrity probe regression tests (verification pass 2026-06-09).

These guard against four classes of regression:

* Probe 2 — `PRAGMA foreign_key_check` returns zero rows on a populated DB.
* Probe 4 — `ensure_schema()` output == raw migration replay (no drift).
* Probe 5 — `.dump` sha256 of a backup matches the source bit-for-bit.
* Probe 6 — auto-assign(seed=N) is deterministic across re-runs AND survives
  a backup → restore round-trip (no wall-clock leakage into ordering).
"""

from __future__ import annotations

import hashlib
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from risk.db.connection import connect, transaction
from risk.db.schema import ensure_schema
from risk.repos import event_types as etypes_repo
from risk.repos import events as events_repo
from risk.repos import houses as houses_repo
from risk.repos import member_statuses as statuses_repo
from risk.repos import members as members_repo
from risk.repos import semesters as semesters_repo
from risk.services import assignment as assign_svc
from risk.services import shift_requirements as svc_reqs

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Populator — modest but real (10 members, 1 event with snapshotted reqs).
# ---------------------------------------------------------------------------

def _populate(db: sqlite3.Connection, *, n_members: int = 10) -> int:
    sem_id = semesters_repo.insert(
        db, name="FA25", starts_on="2025-08-25", ends_on="2025-12-15"
    )
    semesters_repo.set_current(db, "FA25")
    house_id = houses_repo.insert(db, slug="zta", display_name="ZTA")
    active = statuses_repo.get_by_slug(db, "active")
    assert active is not None
    for i in range(n_members):
        members_repo.insert(
            db,
            slug=f"m-{i:02d}",
            display_name=f"M{i:02d}",
            status_id=active.id,
            class_year=2027 + (i % 3),
        )
    et = etypes_repo.get_by_slug(db, "mixer")
    assert et is not None
    event_id = events_repo.insert(
        db,
        semester_id=sem_id,
        event_type_id=et.id,
        host_house_id=house_id,
        display_name="Probe Mixer",
        date="2025-10-10",
    )
    svc_reqs.snapshot_for_event(db, event_id)
    return event_id


# ---------------------------------------------------------------------------
# Probe 2 — FK orphans
# ---------------------------------------------------------------------------

def test_foreign_key_check_clean_on_populated_db(db: sqlite3.Connection) -> None:
    """Populated DB (with assignments) must have zero FK orphans."""
    event_id = _populate(db)
    with transaction(db):
        assign_svc.auto_assign(db, event_id=event_id, seed=7, commit=True)

    # PRAGMA must be ON to actually run the check.
    db.execute("PRAGMA foreign_keys = ON")
    orphans = db.execute("PRAGMA foreign_key_check").fetchall()
    assert orphans == [], f"FK orphans found: {[tuple(r) for r in orphans]}"

    integrity = [tuple(r) for r in db.execute("PRAGMA integrity_check").fetchall()]
    assert integrity == [("ok",)], f"integrity_check failed: {integrity}"


# ---------------------------------------------------------------------------
# Probe 4 — migration replay vs ensure_schema()
# ---------------------------------------------------------------------------

def _dump_schema(db_path: Path) -> str:
    """Use sqlite3 CLI to get .schema for byte-comparable output."""
    res = subprocess.run(
        ["sqlite3", str(db_path), ".schema"], capture_output=True, text=True, check=True
    )
    return res.stdout


def test_migration_replay_matches_ensure_schema(tmp_path: Path) -> None:
    """Manually replayed migrations should produce the same schema as code-path."""
    # Code path: connect() + ensure_schema().
    fresh = tmp_path / "fresh.db"
    conn = connect(fresh)
    ensure_schema(conn)
    conn.close()
    fresh_schema = _dump_schema(fresh)

    # Raw replay path: sqlite3 < each migration in order.
    incremental = tmp_path / "incremental.db"
    migrations_dir = Path(__file__).resolve().parents[2] / "src" / "risk" / "db" / "migrations"
    files = sorted(migrations_dir.glob("*.sql"))
    assert files, f"No migrations found at {migrations_dir}"
    for f in files:
        subprocess.run(
            ["sqlite3", str(incremental)],
            input=f.read_text(),
            text=True,
            check=True,
        )
    incremental_schema = _dump_schema(incremental)

    assert fresh_schema == incremental_schema, (
        "ensure_schema() and raw migration replay produced different schemas — "
        "schema/migration drift."
    )


# ---------------------------------------------------------------------------
# Probe 5 — backup round-trip integrity
# ---------------------------------------------------------------------------

def _dump_sha(db_path: Path) -> str:
    res = subprocess.run(
        ["sqlite3", str(db_path), ".dump"], capture_output=True, text=True, check=True
    )
    return hashlib.sha256(res.stdout.encode()).hexdigest()


def test_backup_dump_sha_matches_source(tmp_path: Path, db: sqlite3.Connection) -> None:
    """`.dump` sha256 of backup == source after `risk db backup`."""
    event_id = _populate(db)
    with transaction(db):
        assign_svc.auto_assign(db, event_id=event_id, seed=11, commit=True)

    # Find the source path the fixture wrote to.
    source_path = Path(db.execute("PRAGMA database_list").fetchone()[2])
    # Force a checkpoint so WAL contents land in the main file before sha-ing.
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    backup_path = tmp_path / "backup.db"
    risk_bin = Path(sys.executable).parent / "risk"
    res = subprocess.run(
        [str(risk_bin), "--db", str(source_path), "db", "backup", str(backup_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert backup_path.exists(), res.stderr

    src_sha = _dump_sha(source_path)
    bak_sha = _dump_sha(backup_path)
    assert src_sha == bak_sha, "backup .dump sha256 differs from source"


# ---------------------------------------------------------------------------
# Probe 6 — auto-assign reproducibility (no wall-clock leakage)
# ---------------------------------------------------------------------------

def _assignment_tuple(result) -> list[tuple[str, int, int]]:
    return sorted(
        (a.shift_type_slug, a.slot_index, a.member_id) for a in result.assignments
    )


def test_auto_assign_reproducible_same_db(db: sqlite3.Connection) -> None:
    """Same seed + same DB state → identical assignment plan."""
    event_id = _populate(db)
    r1 = assign_svc.auto_assign(db, event_id=event_id, seed=42, commit=False)
    r2 = assign_svc.auto_assign(db, event_id=event_id, seed=42, commit=False)
    assert _assignment_tuple(r1) == _assignment_tuple(r2)


def test_auto_assign_reproducible_after_backup_restore(
    tmp_path: Path, db: sqlite3.Connection
) -> None:
    """Backup → reopen → same seed yields identical assignment.

    If this fails the resolver is reading wall-clock somewhere
    (e.g. R3.2-A event.date tiebreaker leaking datetime.now()).
    """
    event_id = _populate(db)
    r_before = assign_svc.auto_assign(db, event_id=event_id, seed=42, commit=False)

    source_path = Path(db.execute("PRAGMA database_list").fetchone()[2])
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    backup_path = tmp_path / "backup.db"
    risk_bin = Path(sys.executable).parent / "risk"
    subprocess.run(
        [str(risk_bin), "--db", str(source_path), "db", "backup", str(backup_path)],
        capture_output=True,
        text=True,
        check=True,
    )

    restored = connect(backup_path)
    try:
        r_after = assign_svc.auto_assign(
            restored, event_id=event_id, seed=42, commit=False
        )
    finally:
        restored.close()

    assert _assignment_tuple(r_before) == _assignment_tuple(r_after), (
        "auto-assign output changed after backup/restore — wall-clock leak suspected"
    )
