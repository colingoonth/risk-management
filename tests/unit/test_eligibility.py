"""Unit tests for ``services.eligibility``.

Table-driven: per scenario, build a minimal world, run eligibility, assert
the eligible slugs match the expected set.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import pytest

from risk.repos import events as events_repo
from risk.repos import houses as houses_repo
from risk.repos import member_house_assignments as mha_repo
from risk.repos import member_roles as mr_repo
from risk.repos import member_statuses as statuses_repo
from risk.repos import members as members_repo
from risk.repos import roles as roles_repo
from risk.repos import semesters as semesters_repo
from risk.services import eligibility

pytestmark = pytest.mark.integration


@dataclass(frozen=True, slots=True)
class World:
    sem_id: int
    zta_id: int
    other_house_id: int
    event_id: int
    members: dict[str, int]
    roles: dict[str, int]


def _build_world(db: sqlite3.Connection) -> World:
    sem_id = semesters_repo.insert(db, name="SP26", starts_on="2026-01-15", ends_on="2026-05-15")
    zta_id = houses_repo.insert(db, slug="zta", display_name="ZTA")
    other_house_id = houses_repo.insert(db, slug="other", display_name="Other")

    from risk.repos import event_types as etypes_repo

    et = etypes_repo.get_by_slug(db, "mixer")
    assert et is not None
    event_id = events_repo.insert(
        db,
        semester_id=sem_id,
        event_type_id=et.id,
        display_name="ZTA mixer",
        date="2026-02-14",
        host_house_id=zta_id,
    )

    active = statuses_repo.get_by_slug(db, "active")
    alumni = statuses_repo.get_by_slug(db, "alumni")
    assert active is not None and alumni is not None

    members: dict[str, int] = {}
    for slug, status in [
        ("brother-a", active),
        ("brother-b", active),
        ("pledge-c", active),
        ("alumnus-d", alumni),
        ("ec-e", active),
        ("risk-chair-f", active),
        ("zta-member-g", active),
    ]:
        members[slug] = members_repo.insert(db, slug=slug, display_name=slug, status_id=status.id)

    # Member 'zta-member-g' is in the host house — should be hard-excluded.
    mha_repo.set_assignment(
        db, member_id=members["zta-member-g"], house_id=zta_id, semester_id=sem_id
    )
    # Member 'brother-a' is in a different house — should be OK.
    mha_repo.set_assignment(
        db, member_id=members["brother-a"], house_id=other_house_id, semester_id=sem_id
    )

    # Use fixture-prefixed automation_keys/slugs to avoid collisions with
    # seeded roles from migration 0010 (exec, risk_chair, dj, pledge_chair).
    roles: dict[str, int] = {}
    roles["pledge"] = roles_repo.insert(db, slug="pledge", display_name="Pledge")
    roles["ec"] = roles_repo.insert(
        db,
        slug="test-ec",
        display_name="Test EC",
        automation_key="test-ec",
        default_excluded=True,
    )
    roles["risk_chair"] = roles_repo.insert(
        db,
        slug="test-risk-chair",
        display_name="Test Risk chair",
        automation_key="test-risk-chair",
        default_excluded=True,
        soft=True,
    )

    mr_repo.set_role(db, member_id=members["pledge-c"], role_id=roles["pledge"], semester_id=sem_id)
    mr_repo.set_role(db, member_id=members["ec-e"], role_id=roles["ec"], semester_id=sem_id)
    mr_repo.set_role(
        db, member_id=members["risk-chair-f"], role_id=roles["risk_chair"], semester_id=sem_id
    )

    return World(
        sem_id=sem_id,
        zta_id=zta_id,
        other_house_id=other_house_id,
        event_id=event_id,
        members=members,
        roles=roles,
    )


def test_eligibility_baseline(db: sqlite3.Connection) -> None:
    w = _build_world(db)
    result = eligibility.eligible_for(
        db,
        event_id=w.event_id,
        semester_id=w.sem_id,
        host_house_id=w.zta_id,
    )
    eligible_slugs = {m.member_slug for m in result.eligible}
    # Includes: brother-a, brother-b, pledge-c
    # Excludes: alumnus-d (status), zta-member-g (host house), ec-e (hard role),
    #           risk-chair-f (soft role, not in --allow)
    assert eligible_slugs == {"brother-a", "brother-b", "pledge-c"}
    assert result.excluded_by_status == 1
    assert result.excluded_by_host_house == 1
    assert result.excluded_by_hard_role == 1
    assert result.excluded_by_soft_role == 1


def test_eligibility_pledge_flag_set(db: sqlite3.Connection) -> None:
    w = _build_world(db)
    result = eligibility.eligible_for(
        db, event_id=w.event_id, semester_id=w.sem_id, host_house_id=w.zta_id
    )
    by_slug = {m.member_slug: m for m in result.eligible}
    assert by_slug["pledge-c"].is_pledge is True
    assert by_slug["brother-a"].is_pledge is False


def test_allow_includes_soft_excluded_role(db: sqlite3.Connection) -> None:
    w = _build_world(db)
    result = eligibility.eligible_for(
        db,
        event_id=w.event_id,
        semester_id=w.sem_id,
        host_house_id=w.zta_id,
        allowed_keys=frozenset({"test-risk-chair"}),
    )
    eligible_slugs = {m.member_slug for m in result.eligible}
    assert "risk-chair-f" in eligible_slugs
    assert result.excluded_by_soft_role == 0


def test_off_site_event_skips_host_house_filter(db: sqlite3.Connection) -> None:
    w = _build_world(db)
    result = eligibility.eligible_for(
        db, event_id=w.event_id, semester_id=w.sem_id, host_house_id=None
    )
    eligible_slugs = {m.member_slug for m in result.eligible}
    # zta-member-g is no longer host-excluded since there's no host.
    assert "zta-member-g" in eligible_slugs
    assert result.excluded_by_host_house == 0
