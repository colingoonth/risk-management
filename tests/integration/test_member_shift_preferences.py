"""Per-member steers: "not rides", "door on a Tuesday", "not at a Krush".

These replaced the blanket sports steer the chair reversed on 2026-08-26, which
pinned eleven men to setup for a whole term and had to be withdrawn. The
replacement is per person, per shift type, and individually removable.

The load-bearing property is that a SOFT steer can never leave a post empty. It
is a sort tier, not a filter: the man is taken when the alternative is nobody.
An unstaffed door is the one outcome a risk schedule cannot produce, and every
avoidance mechanism in this codebase has to respect that.
"""

from __future__ import annotations

import sqlite3

import pytest

from risk.db.connection import transaction
from risk.repos import event_types as etypes_repo
from risk.repos import events as events_repo
from risk.repos import member_shift_preferences as prefs_repo
from risk.repos import member_statuses as statuses_repo
from risk.repos import members as members_repo
from risk.repos import semesters as semesters_repo
from risk.services import assignment, shift_requirements

pytestmark = pytest.mark.integration

TUESDAY = "2026-09-01"
FRIDAY = "2026-09-04"


def _world(db: sqlite3.Connection, *, headcount: int = 14, etype: str = "mixer"):  # noqa: ANN202
    sem_id = semesters_repo.insert(
        db, name="FA26", starts_on="2026-08-25", ends_on="2026-12-05"
    )
    et = etypes_repo.get_by_slug(db, etype)
    active = statuses_repo.get_by_slug(db, "active")
    assert et is not None and active is not None
    subject = members_repo.insert(
        db, slug="the-subject", display_name="The Subject",
        status_id=active.id, class_year=2029,
    )
    for i in range(headcount):
        members_repo.insert(db, slug=f"b{i:02d}", display_name=f"B {i:02d}",
                            status_id=active.id, class_year=2029)
    events: dict[str, int] = {}
    for d in (TUESDAY, FRIDAY):
        eid = events_repo.insert(db, semester_id=sem_id, event_type_id=et.id,
                                 display_name=f"Party {d}", date=d)
        shift_requirements.snapshot_for_event(db, eid)
        events[d] = eid
    return sem_id, subject, events


def _worked(db: sqlite3.Connection, event_id: int, member_id: int) -> set[str]:
    return {
        r["slug"]
        for r in db.execute(
            """SELECT st.slug FROM shifts s JOIN shift_types st ON st.id = s.shift_type_id
               WHERE s.event_id = ? AND s.assigned_member_id = ?""",
            (event_id, member_id),
        )
    }


def _fill(db: sqlite3.Connection, event_id: int) -> None:
    with transaction(db):
        assignment.auto_assign(db, event_id=event_id, seed=1, commit=True)


def test_a_soft_steer_keeps_him_off_that_shift(db: sqlite3.Connection) -> None:
    sem_id, subject, events = _world(db)
    st = db.execute("SELECT id FROM shift_types WHERE slug='driver'").fetchone()["id"]
    with transaction(db):
        prefs_repo.add(db, member_id=subject, semester_id=sem_id, shift_type_id=st)
    _fill(db, events[TUESDAY])
    assert "driver" not in _worked(db, events[TUESDAY], subject)


def test_a_soft_steer_yields_rather_than_leave_the_post_empty(db: sqlite3.Connection) -> None:
    """The property that makes a soft steer safe: with nobody else, he takes it.

    The event is stripped to a single driver slot on purpose. Left with the full
    profile the subject is seated on whichever type the fill reaches first and
    the one-per-event rule then keeps him off driver, so the slot goes unstaffed
    for a reason that has nothing to do with the steer — and the test would pass
    while proving nothing.

    Coverage is asserted as REQUIREMENTS vs seated, never as a count of open
    rows: shift rows are created lazily, so an unfillable slot leaves no row at
    all and an open-row count returns a reassuring zero.
    """
    sem_id, subject, events = _world(db, headcount=0)
    driver = db.execute("SELECT id FROM shift_types WHERE slug='driver'").fetchone()["id"]
    with transaction(db):
        db.execute(
            "DELETE FROM event_shift_requirements WHERE event_id = ? AND shift_type_id <> ?",
            (events[TUESDAY], driver),
        )
        db.execute(
            """UPDATE event_shift_requirements SET target_count = 1, min_count = 1
               WHERE event_id = ? AND shift_type_id = ?""",
            (events[TUESDAY], driver),
        )
        prefs_repo.add(db, member_id=subject, semester_id=sem_id, shift_type_id=driver)
    _fill(db, events[TUESDAY])

    required = db.execute(
        "SELECT COALESCE(SUM(target_count), 0) n FROM event_shift_requirements WHERE event_id = ?",
        (events[TUESDAY],),
    ).fetchone()["n"]
    seated = db.execute(
        "SELECT COUNT(*) n FROM shifts WHERE event_id = ? AND assigned_member_id IS NOT NULL",
        (events[TUESDAY],),
    ).fetchone()["n"]
    assert (required, seated) == (1, 1), "a soft steer must never cost a staffed post"
    assert "driver" in _worked(db, events[TUESDAY], subject)


def test_a_hard_steer_removes_him_outright(db: sqlite3.Connection) -> None:
    sem_id, subject, events = _world(db)
    st = db.execute("SELECT id FROM shift_types WHERE slug='driver'").fetchone()["id"]
    with transaction(db):
        prefs_repo.add(db, member_id=subject, semester_id=sem_id, shift_type_id=st, is_hard=True)
    _fill(db, events[TUESDAY])
    assert "driver" not in _worked(db, events[TUESDAY], subject)


def test_a_weekday_steer_binds_only_on_that_day(db: sqlite3.Connection) -> None:
    """"Door on a Tuesday, not a Friday" — the Friday steer must not touch Tuesday."""
    sem_id, subject, events = _world(db)
    door = db.execute("SELECT id FROM shift_types WHERE slug='door'").fetchone()["id"]
    with transaction(db):
        # Friday is weekday 4 in Python's Monday=0 scheme.
        prefs_repo.add(db, member_id=subject, semester_id=sem_id,
                       shift_type_id=door, weekday=4, is_hard=True)
    _fill(db, events[FRIDAY])
    _fill(db, events[TUESDAY])
    assert "door" not in _worked(db, events[FRIDAY], subject), "the steer should bind on Friday"
    # Tuesday is untouched by the rule; whether he is picked is fairness's call,
    # so assert the RULE did not remove him rather than that he was chosen.
    from risk.repos import member_shift_preferences as r
    soft, hard = r.matching(db, semester_id=sem_id, shift_type_id=door,
                            weekday=1, event_type_id=1)
    assert subject not in soft and subject not in hard, "no steer applies on a Tuesday"


def test_an_event_type_steer_binds_only_at_that_event_type(db: sqlite3.Connection) -> None:
    sem_id, subject, events = _world(db)
    door = db.execute("SELECT id FROM shift_types WHERE slug='door'").fetchone()["id"]
    krush = etypes_repo.get_by_slug(db, "krush")
    mixer = etypes_repo.get_by_slug(db, "mixer")
    assert krush is not None and mixer is not None
    with transaction(db):
        prefs_repo.add(db, member_id=subject, semester_id=sem_id,
                       shift_type_id=door, event_type_id=krush.id, is_hard=True)
    from risk.repos import member_shift_preferences as r
    _, hard_krush = r.matching(db, semester_id=sem_id, shift_type_id=door,
                               weekday=1, event_type_id=krush.id)
    _, hard_mixer = r.matching(db, semester_id=sem_id, shift_type_id=door,
                               weekday=1, event_type_id=mixer.id)
    assert subject in hard_krush
    assert subject not in hard_mixer, "a krush steer must not bind at a mixer"


def test_narrowings_are_anded(db: sqlite3.Connection) -> None:
    """A row carrying both a weekday and an event type binds only when both hold."""
    sem_id, subject, events = _world(db)
    door = db.execute("SELECT id FROM shift_types WHERE slug='door'").fetchone()["id"]
    krush = etypes_repo.get_by_slug(db, "krush")
    assert krush is not None
    with transaction(db):
        prefs_repo.add(db, member_id=subject, semester_id=sem_id, shift_type_id=door,
                       weekday=4, event_type_id=krush.id, is_hard=True)
    from risk.repos import member_shift_preferences as r
    both = r.matching(db, semester_id=sem_id, shift_type_id=door, weekday=4,
                      event_type_id=krush.id)[1]
    wrong_day = r.matching(db, semester_id=sem_id, shift_type_id=door, weekday=1,
                           event_type_id=krush.id)[1]
    assert subject in both
    assert subject not in wrong_day


def test_a_steer_reads_back_in_plain_english(db: sqlite3.Connection) -> None:
    """The chair has to be able to audit these without reading SQL."""
    sem_id, subject, events = _world(db)
    door = db.execute("SELECT id FROM shift_types WHERE slug='door'").fetchone()["id"]
    krush = etypes_repo.get_by_slug(db, "krush")
    assert krush is not None
    with transaction(db):
        prefs_repo.add(db, member_id=subject, semester_id=sem_id, shift_type_id=door,
                       weekday=4, event_type_id=krush.id, is_hard=True)
    p = prefs_repo.list_for_semester(db, semester_id=sem_id)[0]
    assert p.describe() == "no door on Fri at a krush"
