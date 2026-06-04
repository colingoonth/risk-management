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
    with transaction(db):
        repo.insert(
            db, slug="dj", display_name="DJ", automation_key="dj", default_excluded=True, soft=True
        )
        repo.insert(db, slug="senior", display_name="Senior", default_excluded=True, soft=False)
        repo.insert(db, slug="brother", display_name="Brother")
    soft = repo.get_soft_excluded(db)
    assert len(soft) == 1
    assert soft[0].slug == "dj"
