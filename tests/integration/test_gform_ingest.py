"""Integration tests for the Google Form roster ingest (services.ingest)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from risk.repos import member_roles as mr_repo
from risk.repos import members as members_repo
from risk.repos import roles as roles_repo
from risk.repos import semesters as semesters_repo
from risk.services import ingest

pytestmark = pytest.mark.integration

STANDARD_CSV = (
    "Full Name,Rising Class,PC,EC\n"
    "Alice Anderson,Rising Senior,Zeta,Yes\n"
    "Bob Brown,Rising Junior,Eta,No\n"
    "Carol Carter,Rising Sophomore,Theta,\n"
    "Dave Davis,Rising Freshman,Iota,yes\n"
)


def _semester(db: sqlite3.Connection, name: str = "FA26", starts: str = "2026-08-20") -> int:
    return semesters_repo.insert(db, name=name, starts_on=starts, ends_on="2026-12-15")


def _write(tmp_path: Path, content: str, name: str = "roster.csv") -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def test_apply_inserts_members_with_derived_class_year(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    _semester(db)
    path = _write(tmp_path, STANDARD_CSV)
    result = ingest.apply_gform_roster(db, path=path, semester_name="FA26")

    assert len(result.inserted_member_ids) == 4
    # base_year 2026 → senior+1, junior+2, sophomore+3, freshman+4
    expected = {
        "alice-anderson": (2027, "Zeta"),
        "bob-brown": (2028, "Eta"),
        "carol-carter": (2029, "Theta"),
        "dave-davis": (2030, "Iota"),
    }
    for slug, (year, pc) in expected.items():
        m = members_repo.get_by_slug(db, slug)
        assert m is not None, slug
        assert m.class_year == year
        assert m.pledge_class == pc


def test_exec_members_get_exec_role(db: sqlite3.Connection, tmp_path: Path) -> None:
    sem_id = _semester(db)
    path = _write(tmp_path, STANDARD_CSV)
    ingest.apply_gform_roster(db, path=path, semester_name="FA26")

    exec_role = roles_repo.get_by_slug(db, "exec")
    assert exec_role is not None
    alice = members_repo.get_by_slug(db, "alice-anderson")
    dave = members_repo.get_by_slug(db, "dave-davis")
    bob = members_repo.get_by_slug(db, "bob-brown")
    assert alice and dave and bob

    def roles_of(mid: int) -> set[str]:
        return {
            r.role_slug
            for r in mr_repo.list_for_member_in_semester(db, member_id=mid, semester_id=sem_id)
        }

    alice_roles = roles_of(alice.id)
    dave_roles = roles_of(dave.id)
    bob_roles = roles_of(bob.id)
    assert "exec" in alice_roles  # EC=Yes
    assert "exec" in dave_roles  # EC=yes (case-insensitive)
    assert "exec" not in bob_roles  # EC=No


def test_ingest_is_idempotent(db: sqlite3.Connection, tmp_path: Path) -> None:
    _semester(db)
    path = _write(tmp_path, STANDARD_CSV)
    first = ingest.apply_gform_roster(db, path=path, semester_name="FA26")
    second = ingest.apply_gform_roster(db, path=path, semester_name="FA26")
    assert len(first.inserted_member_ids) == 4
    assert second.inserted_member_ids == ()  # nothing new on replay
    assert len(members_repo.list_all(db)) == 4


def test_duplicate_names_get_distinct_slugs(db: sqlite3.Connection, tmp_path: Path) -> None:
    _semester(db)
    csv = "Full Name,Rising Class,PC,EC\nJohn Smith,Rising Senior,Zeta,No\nJohn Smith,Rising Junior,Eta,No\n"
    path = _write(tmp_path, csv)
    result = ingest.apply_gform_roster(db, path=path, semester_name="FA26")
    assert len(result.inserted_member_ids) == 2
    assert members_repo.get_by_slug(db, "john-smith") is not None
    assert members_repo.get_by_slug(db, "john-smith-2") is not None


def test_dry_run_preview_does_not_insert(db: sqlite3.Connection, tmp_path: Path) -> None:
    _semester(db)
    path = _write(tmp_path, STANDARD_CSV)
    preview = ingest.preview_gform_roster(db, path=path, semester_name="FA26")
    assert len(preview.new_members) == 4
    assert preview.base_year == 2026
    assert members_repo.list_all(db) == []  # preview is read-only


def test_unmapped_rising_class_yields_null_year_and_warning(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    _semester(db)
    csv = "Full Name,Rising Class,PC,EC\nGrad Student,Rising Grad,Mu,No\n"
    path = _write(tmp_path, csv)
    preview = ingest.preview_gform_roster(db, path=path, semester_name="FA26")
    assert "Grad Student" in preview.unmapped_rising_class
    ingest.apply_gform_roster(db, path=path, semester_name="FA26")
    m = members_repo.get_by_slug(db, "grad-student")
    assert m is not None and m.class_year is None


def test_unmapped_pledge_class_is_flagged_but_stored(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    _semester(db)
    csv = "Full Name,Rising Class,PC,EC\nFoo Bar,Rising Senior,Quux,No\n"
    path = _write(tmp_path, csv)
    preview = ingest.preview_gform_roster(db, path=path, semester_name="FA26")
    assert "Foo Bar" in preview.unmapped_pledge_class
    ingest.apply_gform_roster(db, path=path, semester_name="FA26")
    m = members_repo.get_by_slug(db, "foo-bar")
    assert m is not None and m.pledge_class == "Quux"  # stored even if non-Greek


def test_verbose_google_form_headers_resolve(db: sqlite3.Connection, tmp_path: Path) -> None:
    _semester(db)
    csv = (
        "Timestamp,What is your full name?,Rising class for Fall 2026,Pledge Class (PC),Exec board (EC)?\n"
        "2026-07-01,Eve Evans,Rising Senior,Kappa,Yes\n"
    )
    path = _write(tmp_path, csv)
    result = ingest.apply_gform_roster(db, path=path, semester_name="FA26")
    assert len(result.inserted_member_ids) == 1
    m = members_repo.get_by_slug(db, "eve-evans")
    assert m is not None
    assert m.class_year == 2027
    assert m.pledge_class == "Kappa"
    assert result.exec_roles_set == 1


def test_archived_semester_rejected(db: sqlite3.Connection, tmp_path: Path) -> None:
    _semester(db)
    db.execute("UPDATE semesters SET archived_at = '2026-12-31' WHERE name = 'FA26'")
    path = _write(tmp_path, STANDARD_CSV)
    with pytest.raises(ValueError, match="archived"):
        ingest.apply_gform_roster(db, path=path, semester_name="FA26")


def test_missing_semester_rejected(db: sqlite3.Connection, tmp_path: Path) -> None:
    path = _write(tmp_path, STANDARD_CSV)
    with pytest.raises(LookupError, match="not found"):
        ingest.apply_gform_roster(db, path=path, semester_name="NOPE")


def test_blank_rows_skipped(db: sqlite3.Connection, tmp_path: Path) -> None:
    _semester(db)
    csv = "Full Name,Rising Class,PC,EC\nReal Person,Rising Senior,Zeta,No\n,,,\n"
    path = _write(tmp_path, csv)
    result = ingest.apply_gform_roster(db, path=path, semester_name="FA26")
    assert len(result.inserted_member_ids) == 1
