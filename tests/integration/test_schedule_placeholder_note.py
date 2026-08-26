"""The placeholder caption: present when it is true, absent when it is not.

Colin chose to PUBLISH the 18 Sep placeholder rather than hide it, on the
condition that the sheet says what a placeholder means — that the brothers on it
are the first cover when somebody drops a shift. That sentence is the only thing
standing between "held date" and twelve people assuming they are off.

Also pins the row it occupies. A push writes values only, so the live sheet's
fills are static and pinned to row numbers; the note must sit in the row 4
spacer and must NOT push the grid down.
"""

from __future__ import annotations

import sqlite3

import pytest

from risk.db.connection import transaction
from risk.repos import event_types as etypes_repo
from risk.repos import events as events_repo
from risk.repos import semesters as semesters_repo
from risk.services import export as export_svc
from risk.services import shift_requirements

pytestmark = pytest.mark.integration


def _world(db: sqlite3.Connection, *, with_placeholder: bool, name: str = "FA26") -> int:
    sem_id = semesters_repo.insert(
        db, name=name, starts_on="2026-08-25", ends_on="2026-12-05"
    )
    et = etypes_repo.get_by_slug(db, "mixer")
    assert et is not None
    confirmed = events_repo.insert(
        db, semester_id=sem_id, event_type_id=et.id,
        display_name="A real party", date="2026-09-12",
    )
    shift_requirements.snapshot_for_event(db, confirmed)
    with transaction(db):
        db.execute(
            "UPDATE events SET planning_status = 'confirmed' WHERE id = ?", (confirmed,)
        )
    if with_placeholder:
        held = events_repo.insert(
            db, semester_id=sem_id, event_type_id=et.id,
            display_name="Placeholder - Friday", date="2026-09-18",
        )
        shift_requirements.snapshot_for_event(db, held)
        with transaction(db):
            db.execute(
                "UPDATE events SET planning_status = 'placeholder' WHERE id = ?", (held,)
            )
    return sem_id


def test_the_note_appears_when_a_placeholder_is_published(db: sqlite3.Connection) -> None:
    data = export_svc.build(db, semester_id=_world(db, with_placeholder=True))
    assert data.schedule_note == export_svc.PLACEHOLDER_NOTE
    assert "pulled onto another night" in data.schedule_note


def test_the_note_is_absent_when_no_placeholder_is_published(
    db: sqlite3.Connection,
) -> None:
    """A block of confirmed parties must not carry a caption about held dates."""
    data = export_svc.build(db, semester_id=_world(db, with_placeholder=False))
    assert data.schedule_note == ""


def test_the_note_follows_the_published_range_not_the_term(
    db: sqlite3.Connection,
) -> None:
    """A placeholder outside the block being sent must not trigger the caption.

    This is the case that makes the flag worth computing rather than hardcoding:
    the term always has placeholders in it somewhere.
    """
    sem_id = _world(db, with_placeholder=True)
    data = export_svc.build(
        db, semester_id=sem_id, on_or_after="2026-09-01", on_or_before="2026-09-13"
    )
    assert [r.date for r in data.schedule] == ["2026-09-12"]
    assert data.schedule_note == ""


def test_the_note_sits_in_the_spacer_and_does_not_move_the_grid(
    db: sqlite3.Connection,
) -> None:
    """Row 4 is the spacer; the header must stay on rows 5 and 6.

    If the note ever gets its own row the live sheet's static fills slide out
    from under the grid — the bug the Risk Notes tab is already stuck with.
    """
    from risk.cli.export import _tab_values

    data = export_svc.build(db, semester_id=_world(db, with_placeholder=True))
    rows = _tab_values(data)["Schedule"]
    assert rows[0][0] == "RISK FA26"
    assert rows[3] == [export_svc.PLACEHOLDER_NOTE], "note belongs in the row-4 spacer"
    assert rows[4][0] == "Date", "header must still start on row 5"
    assert rows[5][0] == "", "second header row must still be row 6"


def test_the_pdf_carries_the_note_too(db: sqlite3.Connection) -> None:
    """The PDF is what the chapter actually reads."""
    from risk.cli.export import _pdf_html

    data = export_svc.build(db, semester_id=_world(db, with_placeholder=True))
    html = _pdf_html(data)
    assert "pulled onto another night" in html

    plain = export_svc.build(
        db, semester_id=_world(db, with_placeholder=False, name="SP27")
    )
    assert "pulled onto another night" not in _pdf_html(plain)
