"""Role schema invariants: reserved-word blocklist, slug GLOB, automation_key GLOB."""

from __future__ import annotations

import sqlite3

import pytest

from risk.db.connection import transaction
from risk.repos import roles as repo

RESERVED_KEYS = (
    "help",
    "version",
    "verbose",
    "quiet",
    "dry-run",
    "db",
    "config",
    "json",
    "json-raw",
)


@pytest.mark.parametrize("automation_key", RESERVED_KEYS)
def test_reserved_automation_key_rejected(db: sqlite3.Connection, automation_key: str) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="automation_key"), transaction(db):
        repo.insert(
            db,
            slug="custom-role",
            display_name="Custom",
            automation_key=automation_key,
            default_excluded=True,
            soft=True,
        )


@pytest.mark.parametrize(
    "bad_slug",
    [
        "Role-With-Caps",
        "1starts-with-digit",
        "-leading-dash",
        "has space",
        "has.dot",
    ],
)
def test_invalid_slug_rejected(db: sqlite3.Connection, bad_slug: str) -> None:
    with pytest.raises(sqlite3.IntegrityError), transaction(db):
        repo.insert(db, slug=bad_slug, display_name="X")


@pytest.mark.parametrize(
    "bad_key",
    ["UPPER", "1lead", "has space", "has.dot"],
)
def test_invalid_automation_key_rejected(db: sqlite3.Connection, bad_key: str) -> None:
    with pytest.raises(sqlite3.IntegrityError), transaction(db):
        repo.insert(
            db,
            slug="role-x",
            display_name="X",
            automation_key=bad_key,
            default_excluded=True,
            soft=True,
        )


def test_soft_excluded_requires_automation_key(db: sqlite3.Connection) -> None:
    """If a role is both default_excluded AND soft, it must have an automation_key."""
    with pytest.raises(sqlite3.IntegrityError), transaction(db):
        repo.insert(
            db,
            slug="incomplete",
            display_name="X",
            automation_key=None,
            default_excluded=True,
            soft=True,
        )


def test_hard_exclude_without_automation_key_ok(db: sqlite3.Connection) -> None:
    """Hard-excluded roles don't need an automation_key (no override flag possible)."""
    with transaction(db):
        repo.insert(
            db,
            slug="senior",
            display_name="Senior",
            automation_key=None,
            default_excluded=True,
            soft=False,
        )
    assert repo.get_by_slug(db, "senior") is not None


def test_get_soft_excluded(db: sqlite3.Connection) -> None:
    # Use fixture-prefixed slugs/keys to avoid collisions with seeded roles
    # from migration 0010 (exec, risk_chair, dj, pledge_chair).
    soft_slugs = [r.slug for r in repo.get_soft_excluded(db)]
    with transaction(db):
        repo.insert(
            db,
            slug="test-dj",
            display_name="Test DJ",
            automation_key="test-dj",
            default_excluded=True,
            soft=True,
        )
        repo.insert(
            db, slug="test-senior", display_name="Test Senior", default_excluded=True, soft=False
        )
        repo.insert(db, slug="test-brother", display_name="Test Brother")
    soft_after = [r.slug for r in repo.get_soft_excluded(db)]
    new_soft = set(soft_after) - set(soft_slugs)
    assert new_soft == {"test-dj"}
