"""Phase 2 tests: members, aliases, roles per semester, house assignments, current-semester."""

from __future__ import annotations

import sqlite3

import pytest

from risk.db.connection import transaction
from risk.repos import houses as houses_repo
from risk.repos import member_house_assignments as mha_repo
from risk.repos import member_roles as mr_repo
from risk.repos import member_statuses as statuses_repo
from risk.repos import members as members_repo
from risk.repos import roles as roles_repo
from risk.repos import semesters as semesters_repo

pytestmark = pytest.mark.integration


def _setup_basics(db: sqlite3.Connection) -> dict[str, int]:
    active = statuses_repo.get_by_slug(db, "active")
    assert active is not None
    with transaction(db):
        sp26 = semesters_repo.insert(db, name="SP26", starts_on="2026-01-15", ends_on="2026-05-15")
        fa26 = semesters_repo.insert(db, name="FA26", starts_on="2026-08-20", ends_on="2026-12-15")
        houses_repo.insert(db, slug="main", display_name="Main")
        roles_repo.insert(db, slug="brother", display_name="Brother")
        roles_repo.insert(db, slug="pledge", display_name="Pledge")
        members_repo.insert(
            db, slug="colin-guenther", display_name="Colin", status_id=active.id, class_year=2027
        )
    house = houses_repo.get_by_slug(db, "main")
    brother = roles_repo.get_by_slug(db, "brother")
    pledge = roles_repo.get_by_slug(db, "pledge")
    colin = members_repo.get_by_slug(db, "colin-guenther")
    assert house and brother and pledge and colin
    return {
        "sp26": sp26,
        "fa26": fa26,
        "house": house.id,
        "brother": brother.id,
        "pledge": pledge.id,
        "colin": colin.id,
    }


# ---------- is_current enforcement ----------


def test_no_two_current_semesters(db: sqlite3.Connection) -> None:
    ids = _setup_basics(db)
    db.execute("UPDATE semesters SET is_current = 1 WHERE id = ?", (ids["sp26"],))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE semesters SET is_current = 1 WHERE id = ?", (ids["fa26"],))


def test_set_current_demotes_previous(db: sqlite3.Connection) -> None:
    _setup_basics(db)
    with transaction(db):
        semesters_repo.set_current(db, "SP26")
    with transaction(db):
        semesters_repo.set_current(db, "FA26")
    current = semesters_repo.get_current(db)
    assert current is not None
    assert current.name == "FA26"


def test_set_current_missing_raises(db: sqlite3.Connection) -> None:
    _setup_basics(db)
    with pytest.raises(LookupError), transaction(db):
        semesters_repo.set_current(db, "ZZ99")


# ---------- members + aliases ----------


def test_member_round_trip_with_aliases(db: sqlite3.Connection) -> None:
    ids = _setup_basics(db)
    with transaction(db):
        members_repo.add_alias(db, member_id=ids["colin"], alias="Colin G", source="manual")
        members_repo.add_alias(db, member_id=ids["colin"], alias="cgu", source="strike-sheet")

    by_slug = members_repo.resolve(db, "colin-guenther")
    by_id = members_repo.resolve(db, str(ids["colin"]))
    by_alias = members_repo.resolve(db, "Colin G")
    assert by_slug is not None and by_slug.id == ids["colin"]
    assert by_id is not None and by_id.id == ids["colin"]
    assert by_alias is not None and by_alias.id == ids["colin"]
    assert members_repo.resolve(db, "nonexistent") is None


def test_alias_unique_globally(db: sqlite3.Connection) -> None:
    ids = _setup_basics(db)
    active = statuses_repo.get_by_slug(db, "active")
    assert active is not None
    with transaction(db):
        members_repo.insert(db, slug="other-guy", display_name="Other", status_id=active.id)
        members_repo.add_alias(db, member_id=ids["colin"], alias="ambiguous")
    other = members_repo.get_by_slug(db, "other-guy")
    assert other is not None
    with pytest.raises(sqlite3.IntegrityError), transaction(db):
        members_repo.add_alias(db, member_id=other.id, alias="ambiguous")


def test_member_class_year_bounds(db: sqlite3.Connection) -> None:
    _setup_basics(db)
    active = statuses_repo.get_by_slug(db, "active")
    assert active is not None
    with pytest.raises(sqlite3.IntegrityError), transaction(db):
        members_repo.insert(
            db, slug="ancient", display_name="Ancient", status_id=active.id, class_year=1850
        )


def test_member_deactivated_at_ordering(db: sqlite3.Connection) -> None:
    _setup_basics(db)
    active = statuses_repo.get_by_slug(db, "active")
    assert active is not None
    with pytest.raises(sqlite3.IntegrityError), transaction(db):
        members_repo.insert(
            db,
            slug="bad-dates",
            display_name="Bad",
            status_id=active.id,
            joined_at="2026-03-01",
            notes=None,
        )
        # deactivated_at < joined_at via raw update
        db.execute("UPDATE members SET deactivated_at = '2026-01-01' WHERE slug = 'bad-dates'")


# ---------- roles scoped per semester ----------


def test_role_scoped_per_semester(db: sqlite3.Connection) -> None:
    ids = _setup_basics(db)
    with transaction(db):
        mr_repo.set_role(
            db, member_id=ids["colin"], role_id=ids["brother"], semester_id=ids["sp26"]
        )
        mr_repo.set_role(
            db, member_id=ids["colin"], role_id=ids["brother"], semester_id=ids["fa26"]
        )
    sp26_roles = mr_repo.list_for_member_in_semester(
        db, member_id=ids["colin"], semester_id=ids["sp26"]
    )
    fa26_roles = mr_repo.list_for_member_in_semester(
        db, member_id=ids["colin"], semester_id=ids["fa26"]
    )
    assert [r.role_slug for r in sp26_roles] == ["brother"]
    assert [r.role_slug for r in fa26_roles] == ["brother"]


def test_set_role_idempotent(db: sqlite3.Connection) -> None:
    ids = _setup_basics(db)
    with transaction(db):
        mr_repo.set_role(
            db, member_id=ids["colin"], role_id=ids["brother"], semester_id=ids["sp26"]
        )
    with transaction(db):
        mr_repo.set_role(
            db, member_id=ids["colin"], role_id=ids["brother"], semester_id=ids["sp26"]
        )
    rows = mr_repo.list_for_member_in_semester(db, member_id=ids["colin"], semester_id=ids["sp26"])
    assert len(rows) == 1


def test_unset_role(db: sqlite3.Connection) -> None:
    ids = _setup_basics(db)
    with transaction(db):
        mr_repo.set_role(
            db, member_id=ids["colin"], role_id=ids["brother"], semester_id=ids["sp26"]
        )
    with transaction(db):
        affected = mr_repo.unset_role(
            db, member_id=ids["colin"], role_id=ids["brother"], semester_id=ids["sp26"]
        )
    assert affected == 1
    rows = mr_repo.list_for_member_in_semester(db, member_id=ids["colin"], semester_id=ids["sp26"])
    assert rows == []


# ---------- house assignment one per member per semester ----------


def test_house_assignment_one_per_member_per_semester(db: sqlite3.Connection) -> None:
    ids = _setup_basics(db)
    with transaction(db):
        houses_repo.insert(db, slug="annex", display_name="Annex")
    annex = houses_repo.get_by_slug(db, "annex")
    assert annex is not None
    with transaction(db):
        mha_repo.set_assignment(
            db, member_id=ids["colin"], house_id=ids["house"], semester_id=ids["sp26"]
        )
        # overwrite — upsert path
        mha_repo.set_assignment(
            db, member_id=ids["colin"], house_id=annex.id, semester_id=ids["sp26"]
        )
    current = mha_repo.get_for_member_in_semester(
        db, member_id=ids["colin"], semester_id=ids["sp26"]
    )
    assert current is not None
    assert current.house_slug == "annex"
