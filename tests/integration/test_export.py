"""The export has to reconcile, because the sheet it replaces did not.

The SP26 tracker's TRACKER tab claimed 149 shifts against 310 filled cells in
its own grid. Twenty-one people who worked were absent from it. That is the
specific failure this export exists to end, so the tests here are mostly
arithmetic: every slot appears exactly once, the tabs agree with each other, and
nothing that is not work is counted as work.
"""

from __future__ import annotations

import sqlite3

import pytest

from risk.db.connection import transaction
from risk.repos import event_types as etypes_repo
from risk.repos import events as events_repo
from risk.repos import member_qualifications as mq_repo
from risk.repos import member_roles as mroles_repo
from risk.repos import member_statuses as statuses_repo
from risk.repos import members as members_repo
from risk.repos import qualifications as quals_repo
from risk.repos import roles as roles_repo
from risk.repos import semesters as semesters_repo
from risk.repos import strikes as strikes_repo
from risk.services import assignment
from risk.services import export as export_svc
from risk.services import shift_requirements as reqs_svc

pytestmark = pytest.mark.integration


@pytest.fixture()
def exported(db: sqlite3.Connection) -> export_svc.SemesterExport:
    sem_id = semesters_repo.insert(db, name="FA26", starts_on="2026-08-20", ends_on="2026-12-19")
    active = statuses_repo.get_by_slug(db, "active")
    assert active is not None
    ids = [
        members_repo.insert(
            db,
            slug=f"brother-{i:02d}",
            display_name=f"Brother {i:02d}",
            status_id=active.id,
            class_year=(2027, 2028, 2029)[i % 3],
            pledge_class=("Beta", "Zeta", "Eta")[i % 3],
        )
        for i in range(40)
    ]
    role_id = roles_repo.insert(
        db,
        slug="fixture-hard-exempt",
        display_name="Fixture Hard Exempt",
        default_excluded=True,
        soft=False,
    )
    mroles_repo.set_role(db, member_id=ids[0], role_id=role_id, semester_id=sem_id)
    members_repo.update_notes(db, member_id=ids[0], notes="President — exempt.")
    dj_qual = quals_repo.get_by_slug(db, "dj")
    assert dj_qual is not None
    mq_repo.grant(db, member_id=ids[1], qualification_id=dj_qual.id, semester_id=sem_id)
    mq_repo.grant(db, member_id=ids[2], qualification_id=dj_qual.id, semester_id=sem_id)
    strikes_repo.insert(
        db,
        member_id=ids[5],
        semester_id=sem_id,
        issued_on="2026-04-19",
        reason="Social Risk (carried from SP26)",
    )
    for date, status in (
        ("2026-08-25", "placeholder"),
        ("2026-08-28", "confirmed"),
        ("2026-09-04", "confirmed"),
    ):
        etype = etypes_repo.get_by_slug(db, "mixer")
        assert etype is not None
        event_id = events_repo.insert(
            db,
            semester_id=sem_id,
            event_type_id=etype.id,
            display_name=f"Party {date}",
            date=date,
            planning_status=status,
        )
        reqs_svc.snapshot_for_event(db, event_id)
    with transaction(db):
        assignment.auto_assign_semester(db, semester_id=sem_id, seed=1, commit=True)
    return export_svc.build(db, semester_id=sem_id)


def test_every_assigned_slot_appears_exactly_once_in_the_grid(
    exported: export_svc.SemesterExport, db: sqlite3.Connection
) -> None:
    """The failure the old tracker shipped: a grid and a total that disagree."""
    in_grid = sum(
        1
        for row in exported.schedule
        for value in row.cells.values()
        if value and value != export_svc.UNFILLED
    )
    in_db = db.execute(
        "SELECT COUNT(*) AS n FROM shifts WHERE assigned_member_id IS NOT NULL"
    ).fetchone()["n"]
    assert in_grid == in_db


def test_the_tally_reconciles_with_the_grid(exported: export_svc.SemesterExport) -> None:
    """counted + dj + strike must equal every name in the schedule.

    The three buckets are deliberately separate columns rather than one number,
    because two of them are not rotation work — but all three are nights the
    member is expected to turn up, and a brother checking his own row needs to
    see all of them.
    """
    grid = sum(
        1
        for row in exported.schedule
        for value in row.cells.values()
        if value and value != export_svc.UNFILLED
    )
    tally = sum(t.counted_total + t.dj_shifts + t.strike_shifts for t in exported.tally)
    assert grid == tally
    assert len(exported.by_brother) == grid


def test_a_strike_make_up_is_marked_in_the_grid_and_excluded_from_the_total(
    exported: export_svc.SemesterExport,
) -> None:
    """Colin's ``(strike)`` marker, and why it has to be there.

    A make-up does not count toward the member's total. Without the marker the
    Schedule tab and the Tally tab look like they contradict each other: the
    brother finds his name on Aug 28 and a total that does not include it.
    """
    marked = [
        v
        for row in exported.schedule
        for v in row.cells.values()
        if v.endswith(export_svc.STRIKE_MARKER)
    ]
    assert len(marked) == 1
    server = next(t for t in exported.tally if t.strike_shifts)
    assert marked[0].startswith(server.display_name)
    assert server.strike_shifts == 1
    assert sum(server.per_type.values()) == server.counted_total, (
        "a make-up must not appear in any per-job column either"
    )


def test_dj_nights_are_a_separate_column_from_the_total(
    exported: export_svc.SemesterExport,
) -> None:
    djs = [t for t in exported.tally if t.dj_shifts]
    assert djs, "the fixture qualifies two DJs and every party has a dj slot"
    for t in djs:
        assert "dj" not in t.per_type
        assert t.counted_total == sum(t.per_type.values())


def test_exempt_officers_appear_at_zero_with_their_reason(
    exported: export_svc.SemesterExport,
) -> None:
    """The proof-of-zero row.

    HANDOFF keeps exempt officers on the roster rather than omitting them
    precisely so the chair can point at a line reading zero. Omitting them would
    make "why does the president never work" unanswerable from the sheet.
    """
    exempt = [t for t in exported.tally if t.exempt]
    assert len(exempt) == 1
    row = exempt[0]
    assert row.counted_total == 0
    assert row.note, "an exemption with no written reason cannot be defended"
    assert row.target == 0.0, "an exempt member has no quota, so vs-target is meaningless"


def test_cleanup_is_reported_on_the_morning_after_the_party(
    exported: export_svc.SemesterExport,
) -> None:
    """The single highest-value cell in the export.

    ``shift_type_windows`` puts cleanup at offset +1, 00:00-12:00 — the next
    morning. Every other text surface in this app prints shifts against the
    event date, which quietly tells four people to turn up a day early. A missed
    shift is a strike, so getting this wrong manufactures discipline problems.
    """
    cleanups = [r for r in exported.by_brother if r.shift_type == "CLEANUP"]
    assert cleanups
    for r in cleanups:
        assert r.worked_on_date > r.date, "cleanup is worked AFTER the party"
        # And it has to SAY so. The date alone is not enough: a brother reading
        # a Friday row does not stop to notice the date in a neighbouring column
        # is a Saturday. "before 12:00" is the other half — the window is
        # 00:00-12:00, which is a deadline, and rendering it literally invites
        # somebody to read "00:00" as "be there at midnight".
        assert "before 12:00" in r.worked_on, r.worked_on
        assert r.worked_on.startswith(r.worked_on_date), (
            "the ISO date must lead so the column still sorts"
        )
    setups = [r for r in exported.by_brother if r.shift_type == "SETUP"]
    assert setups
    for r in setups:
        assert r.worked_on_date <= r.date, "setup is worked before or on the day"
        assert "any 2h block" in r.worked_on, r.worked_on


def test_the_cleanup_column_header_names_the_day(
    exported: export_svc.SemesterExport,
) -> None:
    """The grid is what gets printed and pinned up.

    A reader scanning down the CLEANUP block will not look back across seven
    columns to work out which day it means, so the day rides in the heading. The
    phrasing is derived from shift_type_windows rather than hardcoded — move
    cleanup to noon-to-four and the header follows.
    """
    assert exported.column_headers["cleanup"] == "CLEANUP (NEXT MORNING, before 12:00)"
    assert exported.column_headers["setup"] == "SETUP (BEFORE the party)"
    # Jobs worked on the night of the party keep their bare label; qualifying
    # every column would make the two that matter stop standing out.
    assert exported.column_headers["door"] == "DOOR"
    assert exported.column_headers["driver"] == "RIDES"


def test_column_widths_come_from_the_data(exported: export_svc.SemesterExport) -> None:
    """Not from the old sheet's constants.

    The SP26 layout ran RIDES x3 and DOOR x4. FA26 mixers need 2 and 2.
    Hardcoding either produces permanently empty columns or, worse, silently
    truncates a crew when the chapter scales a party up.
    """
    widths = dict(exported.shift_type_columns)
    assert widths["driver"] == 2, "a mixer takes two drivers in the seeded defaults"
    assert widths["setup"] == 4
    assert widths["dj"] == 1
    # And in the order a reader expects, not the order the solver fills.
    assert [s for s, _ in exported.shift_type_columns][:3] == ["driver", "door", "setup"]


def test_the_dedupe_suffix_never_reaches_the_reader(
    exported: export_svc.SemesterExport,
) -> None:
    """`` (YYYY-MM-DD)`` exists to satisfy UNIQUE(semester_id, display_name).

    It is a database concern. The reader already has the date in column A, and
    "KD Mixer (2026-08-29)" in a column headed Event is noise that makes two
    genuinely different parties look like a data-entry error.
    """
    for row in exported.schedule:
        assert " (20" not in row.display_name


def test_notes_are_mirrored_from_the_database(db: sqlite3.Connection) -> None:
    """The sheet reflects the app, rather than being a second place to write.

    The old tracker's grid and its TRACKER tab disagreed by 161 shifts because
    each was maintained by hand. One source, mirrored outward, is how that stops
    being possible.
    """
    from risk.repos import chair_notes as notes_repo

    sem_id = semesters_repo.insert(db, name="FA26", starts_on="2026-08-20", ends_on="2026-12-19")
    with transaction(db):
        notes_repo.insert(db, semester_id=sem_id, body="Nico out Oct 10")
        standing = notes_repo.insert(
            db, semester_id=sem_id, body="Never two Etas on setup", kind="standing"
        )
        asked = notes_repo.insert(
            db, semester_id=sem_id, body="How big is the PC?", author="claude"
        )
        notes_repo.close(
            db, note_id=asked, closed_at="2026-08-21T10:00:00", closed_note="~18 expected"
        )

    data = export_svc.build(db, semester_id=sem_id)
    bodies = {n.body: n for n in data.notes}
    assert set(bodies) == {"Nico out Oct 10", "Never two Etas on setup", "How big is the PC?"}
    assert bodies["Never two Etas on setup"].kind == "standing"
    assert bodies["How big is the PC?"].author == "claude"
    assert bodies["How big is the PC?"].closed_note == "~18 expected"
    # Still-applies first: a retired note at the top of the list is the one a
    # chair skims past and then acts on.
    assert data.notes[0].closed_at is None
    assert data.notes[-1].body == "How big is the PC?"
    _ = standing


def test_a_standing_rule_closes_as_retired_not_done(db: sqlite3.Connection) -> None:
    """Closing a standing rule must stop it applying, not mark it complete.

    The distinction is the whole reason the two kinds are separate. A rule that
    can be "finished" silently drops out of the next rebuild, and nothing in the
    output would show that it had.
    """
    from risk.repos import chair_notes as notes_repo

    sem_id = semesters_repo.insert(db, name="FA26", starts_on="2026-08-20", ends_on="2026-12-19")
    with transaction(db):
        rule = notes_repo.insert(db, semester_id=sem_id, body="No freshmen on bar", kind="standing")
    assert notes_repo.list_for_semester(db, sem_id, open_only=True, kind="standing")

    with transaction(db):
        notes_repo.close(db, note_id=rule, closed_at="2026-10-01T00:00:00")
    assert notes_repo.list_for_semester(db, sem_id, open_only=True, kind="standing") == []

    # Re-closing is a no-op rather than overwriting what was recorded the first
    # time — the outcome note is the audit trail.
    with transaction(db):
        assert notes_repo.close(db, note_id=rule, closed_at="2026-11-01T00:00:00") == 0
    note = notes_repo.get_by_id(db, rule)
    assert note is not None and note.closed_at == "2026-10-01T00:00:00"

    with transaction(db):
        notes_repo.reopen(db, note_id=rule)
    reopened = notes_repo.get_by_id(db, rule)
    assert reopened is not None
    assert reopened.closed_at is None
    assert reopened.closed_note is None, "a reopened note must not still assert how it was resolved"


def test_setup_costs_less_so_a_setup_heavy_member_works_more_turns(
    db: sqlite3.Connection,
) -> None:
    """Effort weighting, stated as the outcome Colin asked for.

    "Allocate them to setups and give them extra to make it fair, since setup is
    the easiest one." That is only expressible if a setup is worth less than a
    party night — otherwise the quota caps everyone at the same headcount and
    there is no "extra" to give.

    Weighted by the JOB, never by the person. The chapter can be told "setup
    counts 0.7 because it is two hours in daylight that you pick"; it cannot be
    told "these eleven get a bigger quota because they play sports", which is
    the same schedule and an argument at chapter.
    """
    from risk.repos import shift_types as stypes_repo
    from risk.repos import shifts as shifts_repo

    setup = stypes_repo.get_by_slug(db, "setup")
    door = stypes_repo.get_by_slug(db, "door")
    assert setup is not None and door is not None
    assert setup.effort_weight == pytest.approx(0.7)
    assert door.effort_weight == pytest.approx(1.0)

    sem_id = semesters_repo.insert(db, name="FA26", starts_on="2026-08-20", ends_on="2026-12-19")
    active = statuses_repo.get_by_slug(db, "active")
    assert active is not None
    runner = members_repo.insert(
        db, slug="setup-only", display_name="Setup Only", status_id=active.id, class_year=2029
    )
    regular = members_repo.insert(
        db, slug="night-only", display_name="Night Only", status_id=active.id, class_year=2029
    )
    etype = etypes_repo.get_by_slug(db, "mixer")
    assert etype is not None
    from risk.repos import pledge_modes as pmodes_repo

    pm = pmodes_repo.get(db, "normal")
    assert pm is not None
    with transaction(db):
        for i in range(4):
            ev = events_repo.insert(
                db,
                semester_id=sem_id,
                event_type_id=etype.id,
                display_name=f"P{i}",
                date=f"2026-09-0{i + 1}",
            )
            for member, stype in ((runner, setup), (regular, door)):
                sid = shifts_repo.insert_open(db, event_id=ev, shift_type_id=stype.id, slot_index=0)
                shifts_repo.assign(
                    db,
                    shift_id=sid,
                    member_id=member,
                    effective_pledge_mode_id=pm.id,
                    assigned_at=f"2026-09-0{i + 1}",
                )

    setup_effort = shifts_repo.rotation_effort_in_semester_through_date(
        db, member_id=runner, semester_id=sem_id, on_or_before="2026-12-31"
    )
    night_effort = shifts_repo.rotation_effort_in_semester_through_date(
        db, member_id=regular, semester_id=sem_id, on_or_before="2026-12-31"
    )
    # Same number of turns, different effort — which is what lets the fill hand
    # the setup member more of them before he reaches quota.
    assert setup_effort == pytest.approx(4 * 0.7)
    assert night_effort == pytest.approx(4 * 1.0)
    assert setup_effort < night_effort
