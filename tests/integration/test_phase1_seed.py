"""Phase 1 seed-data validation: lookups, allow-list, and default shift counts."""

from __future__ import annotations

import sqlite3

import pytest

from risk.repos import event_type_shift_defaults as defaults_repo
from risk.repos import event_types as etypes_repo
from risk.repos import pledge_modes as pmode_repo
from risk.repos import removal_methods as rm_repo
from risk.repos import shift_types as stypes_repo

pytestmark = pytest.mark.integration


def test_pledge_modes_seeded(db: sqlite3.Connection) -> None:
    modes = {m.slug for m in pmode_repo.list_all_modes(db)}
    assert modes == {"normal", "pledge_takeover_full", "pledge_takeover_partial"}


def test_shift_types_seeded(db: sqlite3.Connection) -> None:
    types = {t.slug for t in stypes_repo.list_all(db)}
    assert types == {"driver", "door", "setup", "cleanup", "bar"}


def test_event_types_seeded(db: sqlite3.Connection) -> None:
    types = {t.slug for t in etypes_repo.list_all(db)}
    assert types == {"mixer", "krush", "other_party", "philanthropy"}


def test_allow_list_mixer_excludes_bar(db: sqlite3.Connection) -> None:
    mixer = etypes_repo.get_by_slug(db, "mixer")
    assert mixer is not None
    allowed = etypes_repo.list_allowed_shift_types(db, mixer.id)
    assert "bar" not in allowed
    assert {"driver", "door", "setup", "cleanup"}.issubset(allowed)


def test_allow_list_krush_includes_bar(db: sqlite3.Connection) -> None:
    krush = etypes_repo.get_by_slug(db, "krush")
    assert krush is not None
    allowed = etypes_repo.list_allowed_shift_types(db, krush.id)
    assert "bar" in allowed


def test_removal_methods_seeded(db: sqlite3.Connection) -> None:
    methods = {m.slug for m in rm_repo.list_active(db)}
    expected = {"leadership_conf", "donation", "ec_event", "voluntary_social_risk"}
    assert expected.issubset(methods)


def test_mixer_driver_default_is_2_3(db: sqlite3.Connection) -> None:
    mixer = etypes_repo.get_by_slug(db, "mixer")
    assert mixer is not None
    defaults = {
        d.shift_type_slug: (d.min_count, d.target_count)
        for d in defaults_repo.list_for_event_type(db, mixer.id)
    }
    assert defaults["driver"] == (2, 3)
    assert defaults["door"] == (2, 3)
    assert defaults["setup"] == (4, 4)
    assert defaults["cleanup"] == (4, 4)
    assert "bar" not in defaults


def test_krush_includes_bar_2_2(db: sqlite3.Connection) -> None:
    krush = etypes_repo.get_by_slug(db, "krush")
    assert krush is not None
    defaults = {
        d.shift_type_slug: (d.min_count, d.target_count)
        for d in defaults_repo.list_for_event_type(db, krush.id)
    }
    assert defaults["bar"] == (2, 2)
    assert defaults["driver"] == (3, 3)
