"""A loader that reports an error must not leave rows behind.

Both bulk loaders wrap their work in ``with transaction(conn)``, but their own
validation failures used a plain ``return 1`` from inside that block. ``return``
is not an exception, so the context manager took its success path and COMMITTED
— a run that printed ERROR and exited 1 had already written every row it got
through before the bad one.

The specific trap: ``scripts/README.md`` tells the chair to dry-run first, every
time, and the dry run does NOT validate host houses. So forgetting
``--house arena=Arena`` gives a clean dry run, and the real run then aborts on
the first arena-hosted row with a partially-loaded database and a non-zero exit
code that says nothing was written.

These are subprocess tests because the failure is in the ``return``/``with``
interaction at the top of the script, which is exactly what an in-process call
of the inner functions would skip.
"""

from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

import pytest

from risk.db.connection import connect
from risk.db.schema import ensure_schema
from risk.repos import semesters as semesters_repo

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
LOAD_EVENTS = REPO_ROOT / "scripts" / "load_events.py"

FIELDS = ("display_name", "date", "event_type", "host_house", "status", "note")


def _events_csv(path: Path, rows: list[dict[str, str]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({f: row.get(f, "") for f in FIELDS})
    return path


def _db_with_semester(path: Path) -> None:
    conn = connect(path)
    ensure_schema(conn)
    semesters_repo.insert(conn, name="FA26", starts_on="2026-08-25", ends_on="2026-12-05")
    conn.commit()
    conn.close()


def _run_loader(db_path: Path, csv_path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(LOAD_EVENTS),
            str(csv_path),
            "--db",
            str(db_path),
            "--semester",
            "FA26",
            *extra,
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


def _event_count(db_path: Path) -> int:
    conn = connect(db_path)
    ensure_schema(conn)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
    finally:
        conn.close()


@pytest.fixture()
def loaded(tmp_path: Path) -> tuple[Path, Path]:
    db_path = tmp_path / "loader.db"
    _db_with_semester(db_path)
    csv_path = _events_csv(
        tmp_path / "events.csv",
        [
            # Loads fine — off-site, so no house lookup.
            {"display_name": "Offsite mixer", "date": "2026-09-11", "event_type": "mixer"},
            # Fails — 'arena' does not exist and no --house flag was given.
            {
                "display_name": "Arena party",
                "date": "2026-09-18",
                "event_type": "krush",
                "host_house": "arena",
            },
            {"display_name": "Later mixer", "date": "2026-09-25", "event_type": "mixer"},
        ],
    )
    return db_path, csv_path


def test_a_failed_load_writes_nothing(loaded: tuple[Path, Path]) -> None:
    """The row before the bad one must be rolled back, not committed."""
    db_path, csv_path = loaded
    result = _run_loader(db_path, csv_path)

    assert result.returncode != 0, f"expected failure, got:\n{result.stdout}"
    assert "no house" in result.stderr.lower()
    assert _event_count(db_path) == 0, "a run that reported an error committed rows anyway"


def test_a_failed_load_leaves_no_orphan_shift_requirements(
    loaded: tuple[Path, Path],
) -> None:
    """Each event snapshots its requirements, so those must roll back too."""
    db_path, csv_path = loaded
    _run_loader(db_path, csv_path)

    conn = connect(db_path)
    ensure_schema(conn)
    try:
        reqs = conn.execute("SELECT COUNT(*) FROM event_shift_requirements").fetchone()[0]
    finally:
        conn.close()
    assert reqs == 0


def test_the_same_load_succeeds_once_the_house_is_supplied(
    loaded: tuple[Path, Path],
) -> None:
    """The success path is untouched — and recovery after the error is clean."""
    db_path, csv_path = loaded
    _run_loader(db_path, csv_path)  # fails, writes nothing

    result = _run_loader(db_path, csv_path, "--house", "arena=Arena")
    assert result.returncode == 0, result.stderr
    assert _event_count(db_path) == 3


def test_dry_run_still_writes_nothing_and_succeeds(loaded: tuple[Path, Path]) -> None:
    """The dry-run rollback path must keep behaving as it did."""
    db_path, csv_path = loaded
    result = _run_loader(db_path, csv_path, "--house", "arena=Arena", "--dry-run")

    assert result.returncode == 0, result.stderr
    assert "rolled back" in result.stdout
    assert _event_count(db_path) == 0


# ---------------------------------------------------------------------------
# load_sidecar has the same shape, and its docstring makes the promise explicit:
# "A partial load is worse than no load, because it looks like it worked."
# Its name pre-flight runs outside the transaction, but a bad qualification slug
# is only discovered inside it — which is the path that used to commit.
# ---------------------------------------------------------------------------

LOAD_SIDECAR = REPO_ROOT / "scripts" / "load_sidecar.py"


def test_a_failed_sidecar_load_applies_nothing(tmp_path: Path) -> None:
    db_path = tmp_path / "sidecar.db"
    conn = connect(db_path)
    ensure_schema(conn)
    sem_id = semesters_repo.insert(conn, name="FA26", starts_on="2026-08-25", ends_on="2026-12-05")
    from risk.repos import member_statuses as statuses_repo
    from risk.repos import members as members_repo

    active = statuses_repo.get_by_slug(conn, "active")
    assert active is not None
    # The sidecar resolves names by slugifying them, so the slug must match.
    members_repo.insert(conn, slug="test-person", display_name="Test Person", status_id=active.id)
    conn.commit()
    conn.close()
    _ = sem_id

    sidecar = tmp_path / "sidecar.json"
    sidecar.write_text(
        '{"aliases": {"Test Person": ["TP"]}, '
        '"qualifications": {"Test Person": ["not-a-real-qualification"]}}'
    )

    result = subprocess.run(
        [
            sys.executable,
            str(LOAD_SIDECAR),
            str(sidecar),
            "--db",
            str(db_path),
            "--semester",
            "FA26",
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )

    assert result.returncode != 0, f"expected failure, got:\n{result.stdout}"
    assert "not-a-real-qualification" in result.stderr

    conn = connect(db_path)
    ensure_schema(conn)
    try:
        aliases = conn.execute("SELECT COUNT(*) FROM member_aliases").fetchone()[0]
        quals = conn.execute("SELECT COUNT(*) FROM member_qualifications").fetchone()[0]
    finally:
        conn.close()
    assert (aliases, quals) == (0, 0), (
        "the alias was applied before the bad slug was reached and committed anyway"
    )
