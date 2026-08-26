"""Semester-scale end-to-end invariants for the FA26 fill.

Every other test in this suite builds a toy world — six members, one event — and
asserts a local property. That is how four real defects reached a green suite:
the interesting failures are all emergent, and none of them can arise in a world
too small to contain them. Nothing here builds a full term and asserts that the
resulting *schedule* is correct.

This module does. It seeds FA26's actual shape (67 members split 35/21/11 by
graduation year, 10 hard-exempt officers, 2 DJ-qualified, 43 events across the
real event types with their seeded slot profiles), runs the whole term through
the same path the API's bulk fill uses, and asserts the properties the chair
would be asked to defend.

Every assertion here was verified true against the real FA26 database before it
was written, so a red test means this harness drifted from the app, not that the
app regressed. Its job is to stay green while ``fairness.score_member`` is
rewritten underneath it.
"""

from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict

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
from risk.services import assignment
from risk.services import shift_requirements as reqs_svc

pytestmark = pytest.mark.integration


# FA26's real shape, from `~/Desktop/risk data/HANDOFF.md` "Verified data".
# 67 members; class_year is the GRADUATION year, so in a fall-2026 term
# 2027 = senior, 2028 = junior, 2029 = sophomore.
CLASS_SIZES = {2027: 35, 2028: 21, 2029: 11}

# Ten officers carry a hard-exclude role and must appear in the roll at zero —
# the chair's proof they were never in the pool. Five exec, three risk chairs,
# two social chairs; the split does not matter here, only that they are hard.
N_HARD_EXEMPT = 10

# 43 events. The dates and types are the real calendar, transcribed from
# `generated/quota4.py`. Slot counts come from the seeded event-type defaults,
# not from this file, so a change to those defaults surfaces here.
CALENDAR: tuple[tuple[str, str, str], ...] = (
    ("2026-08-25", "mixer", "placeholder"),
    ("2026-08-28", "open", "confirmed"),
    ("2026-08-29", "mixer", "confirmed"),
    ("2026-09-01", "mixer", "confirmed"),
    ("2026-09-04", "mixer", "confirmed"),
    ("2026-09-05", "open", "potential"),
    ("2026-09-07", "dage", "confirmed"),
    ("2026-09-08", "mixer", "confirmed"),
    ("2026-09-11", "mixer", "confirmed"),
    ("2026-09-12", "mixer", "confirmed"),
    ("2026-09-15", "quad", "confirmed"),
    ("2026-09-18", "mixer", "placeholder"),
    ("2026-09-19", "krush", "confirmed"),
    ("2026-09-22", "mixer", "confirmed"),
    ("2026-09-25", "mixer", "confirmed"),
    ("2026-09-26", "mixer", "confirmed"),
    ("2026-09-29", "mixer", "confirmed"),
    ("2026-10-06", "mixer", "placeholder"),
    ("2026-10-08", "rush", "confirmed"),
    ("2026-10-09", "mixer", "confirmed"),
    ("2026-10-10", "mixer", "placeholder"),
    ("2026-10-13", "mixer", "confirmed"),
    ("2026-10-16", "mixer", "confirmed"),
    ("2026-10-17", "mixer", "placeholder"),
    ("2026-10-20", "mixer", "placeholder"),
    ("2026-10-23", "mixer", "placeholder"),
    ("2026-10-24", "krush", "confirmed"),
    ("2026-10-25", "dage", "confirmed"),
    ("2026-10-27", "mixer", "confirmed"),
    ("2026-10-30", "mixer", "confirmed"),
    ("2026-10-31", "krush", "confirmed"),
    ("2026-11-03", "mixer", "confirmed"),
    ("2026-11-06", "mixer", "confirmed"),
    ("2026-11-07", "mixer", "placeholder"),
    ("2026-11-10", "mixer", "confirmed"),
    ("2026-11-13", "mixer", "confirmed"),
    ("2026-11-14", "quad", "confirmed"),
    ("2026-11-17", "mixer", "confirmed"),
    ("2026-11-20", "mixer", "confirmed"),
    ("2026-11-21", "open", "confirmed"),
    ("2026-11-24", "mixer", "placeholder"),
    ("2026-12-01", "mixer", "placeholder"),
    ("2026-12-05", "mixer", "placeholder"),
)


def _build_fa26_world(db: sqlite3.Connection) -> tuple[int, list[int], list[int]]:
    """Seed a semester shaped like FA26. Returns (semester_id, exempt, dj_qualified).

    Slugs are deliberately unlike anything a migration would ever claim
    (``brother-2027-00``, ``fixture-hard-exempt``). Fixtures using plausible
    slugs — ``dage``, ``social_chair`` — have broken twice when a later
    migration promoted them to real, so this file will not add a third.
    """
    sem_id = semesters_repo.insert(db, name="FA26", starts_on="2026-08-20", ends_on="2026-12-19")
    active = statuses_repo.get_by_slug(db, "active")
    assert active is not None

    member_ids: list[int] = []
    for class_year, size in CLASS_SIZES.items():
        for i in range(size):
            member_ids.append(
                members_repo.insert(
                    db,
                    slug=f"brother-{class_year}-{i:02d}",
                    display_name=f"Brother {class_year}-{i:02d}",
                    status_id=active.id,
                    class_year=class_year,
                    # Spread pledge classes so the PC tiebreaker has real work
                    # to do rather than tying on every comparison.
                    pledge_class=("Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta")[i % 6],
                )
            )

    # Ten hard exemptions. Taken off the front of the senior block because that
    # is where the real ones sit — the chair, the president and the social
    # chairs are all upperclassmen — and because it makes the eligible split
    # 30/17/10, which is what the quota targets are derived from.
    exempt_role_id = roles_repo.insert(
        db,
        slug="fixture-hard-exempt",
        display_name="Fixture Hard Exempt",
        default_excluded=True,
        soft=False,
    )
    exempt = member_ids[:N_HARD_EXEMPT]
    for member_id in exempt:
        mroles_repo.set_role(db, member_id=member_id, role_id=exempt_role_id, semester_id=sem_id)
        members_repo.update_notes(
            db, member_id=member_id, notes="Fixture exemption — proof-of-zero row."
        )

    # Two DJs, matching the chapter. The qualification itself is seeded by
    # migration 0012; only the grant is ours.
    dj_qual = quals_repo.get_by_slug(db, "dj")
    assert dj_qual is not None, "migration 0012 should seed the dj qualification"
    dj_qualified = member_ids[N_HARD_EXEMPT : N_HARD_EXEMPT + 2]
    for member_id in dj_qualified:
        mq_repo.grant(db, member_id=member_id, qualification_id=dj_qual.id, semester_id=sem_id)

    for date, type_slug, planning_status in CALENDAR:
        etype = etypes_repo.get_by_slug(db, type_slug)
        assert etype is not None, f"event type {type_slug!r} should be seeded"
        event_id = events_repo.insert(
            db,
            semester_id=sem_id,
            event_type_id=etype.id,
            display_name=f"{type_slug} {date}",
            date=date,
            planning_status=planning_status,
            notes=f"status={planning_status}",
        )
        reqs_svc.snapshot_for_event(db, event_id)

    return sem_id, exempt, dj_qualified


def _run_semester(db: sqlite3.Connection, semester_id: int) -> None:
    """Fill the whole term through the same service both shipped surfaces use.

    This began as a hand-rolled copy of the loop inside
    ``POST /api/events/auto-assign-bulk``, because that was the only place the
    loop existed. It now delegates to the service that loop moved into, and
    every assertion in this module stayed green across the move — which is why
    they were written before the move rather than after.
    """
    with transaction(db):
        assignment.auto_assign_semester(db, semester_id=semester_id, seed=1, commit=True)


@pytest.fixture()
def filled_semester(db: sqlite3.Connection) -> tuple[sqlite3.Connection, int, list[int], list[int]]:
    sem_id, exempt, dj_qualified = _build_fa26_world(db)
    _run_semester(db, sem_id)
    return db, sem_id, exempt, dj_qualified


def _assigned_rows(db: sqlite3.Connection, semester_id: int) -> list[sqlite3.Row]:
    return db.execute(
        """
        SELECT s.id, s.event_id, s.slot_index, s.assigned_member_id,
               st.slug AS shift_slug, st.counts_toward_tally, e.date, e.host_house_id
        FROM shifts s
        JOIN events e ON e.id = s.event_id
        JOIN shift_types st ON st.id = s.shift_type_id
        WHERE e.semester_id = ? AND s.assigned_member_id IS NOT NULL
        """,
        (semester_id,),
    ).fetchall()


def test_every_slot_is_filled(filled_semester: tuple) -> None:
    """No open slots anywhere. A hole in the calendar is an unstaffed party."""
    db, sem_id, _, _ = filled_semester
    open_slots = db.execute(
        """
        SELECT COUNT(*) AS n FROM shifts s
        JOIN events e ON e.id = s.event_id
        WHERE e.semester_id = ? AND s.assigned_member_id IS NULL
        """,
        (sem_id,),
    ).fetchone()["n"]
    assert open_slots == 0

    required = db.execute(
        """
        SELECT COALESCE(SUM(r.target_count), 0) AS n
        FROM event_shift_requirements r
        JOIN events e ON e.id = r.event_id
        WHERE e.semester_id = ?
        """,
        (sem_id,),
    ).fetchone()["n"]
    assert len(_assigned_rows(db, sem_id)) == required


def test_nobody_works_two_counted_shifts_at_one_event(filled_semester: tuple) -> None:
    """One body cannot take two TURNS at the same party.

    The partial unique index only forbids the same member twice on the same
    *shift type*; door + setup for one person is legal at the schema level and
    is prevented in ``assignment`` instead. That makes this an assertion about
    the service, not the database.

    COUNTED shifts specifically, since 2026-08-26. A dj night counts toward no
    tally, so a DJ who also takes setup still has exactly one turn — the same as
    everybody else — and blocking him was charging him a whole event for a shift
    the ledger does not record. What must never happen is two shifts that both
    count. See ``test_nobody_stands_two_posts_on_the_party_night`` for the other
    half of the rule.
    """
    db, sem_id, _, _ = filled_semester
    per_event: dict[int, Counter[int]] = defaultdict(Counter)
    for row in _assigned_rows(db, sem_id):
        if row["counts_toward_tally"]:
            per_event[row["event_id"]][row["assigned_member_id"]] += 1
    doubles = {
        event_id: [m for m, n in counts.items() if n > 1]
        for event_id, counts in per_event.items()
        if any(n > 1 for n in counts.values())
    }
    assert doubles == {}


def test_nobody_stands_two_posts_on_the_party_night(filled_semester: tuple) -> None:
    """The physical half of the rule: one man, one place, 20:00-23:59.

    Relaxing the flat one-per-event rule to let a DJ also work setup is only
    safe because setup and cleanup happen on OTHER DAYS. Two shifts that both
    carry ``occupies_event_night`` are the same hours, and no relaxation may
    ever permit that pair — a DJ on the door is one man at two posts.

    Keyed on the ``occupies_event_night`` FLAG rather than on comparing times,
    for the reason migration 0012 gives: setup's window is ~72h wide and would
    look like it overlaps the party if you only compared clocks.
    """
    db, sem_id, _, _ = filled_semester
    per_event: dict[int, Counter[int]] = defaultdict(Counter)
    for row in db.execute(
        """
        SELECT s.assigned_member_id, s.event_id
        FROM shifts s
        JOIN events e ON e.id = s.event_id
        JOIN shift_types st ON st.id = s.shift_type_id
        LEFT JOIN shift_type_windows w ON w.shift_type_id = st.id
        WHERE e.semester_id = ?
          AND s.assigned_member_id IS NOT NULL
          AND COALESCE(w.occupies_event_night, 1) = 1
        """,
        (sem_id,),
    ):
        per_event[row["event_id"]][row["assigned_member_id"]] += 1
    doubles = {
        event_id: [m for m, n in counts.items() if n > 1]
        for event_id, counts in per_event.items()
        if any(n > 1 for n in counts.values())
    }
    assert doubles == {}, "somebody is standing two posts at once on a party night"


def test_hard_exempt_officers_hold_zero_shifts(filled_semester: tuple) -> None:
    """The proof-of-zero property.

    HANDOFF keeps exempt officers on the roster as members rather than omitting
    them, precisely so the chair can point at a row reading zero. A test that
    only checked they were absent from ``shifts`` would pass if they had been
    deleted from ``members`` instead, which is the failure this guards.
    """
    db, sem_id, exempt, _ = filled_semester
    assigned = {row["assigned_member_id"] for row in _assigned_rows(db, sem_id)}
    assert assigned.isdisjoint(exempt)
    for member_id in exempt:
        member = members_repo.get_by_id(db, member_id)
        assert member is not None, "exempt members must survive as roster rows"
        assert member.notes, "an exemption without a written reason cannot be defended"


def test_dj_slots_only_go_to_qualified_members(filled_semester: tuple) -> None:
    """The one gated shift type actually gates.

    ``dj`` was declared in migration 0012 and unenforced in code until commit
    02a4f04; nothing in the suite would have caught the regression.
    """
    db, sem_id, _, dj_qualified = filled_semester
    dj_holders = {
        row["assigned_member_id"] for row in _assigned_rows(db, sem_id) if row["shift_slug"] == "dj"
    }
    assert dj_holders, "the calendar has a dj slot at every event"
    assert dj_holders <= set(dj_qualified)


def test_refill_reproduces_the_same_schedule(filled_semester: tuple) -> None:
    """The schedule is a function of the data, not of when it was run.

    This is what lets the chair answer a challenge by re-deriving the pick
    rather than appealing to a stored blob. Wall-clock, insertion order and the
    seed must all be irrelevant; fairness is pinned to ``event.date`` for
    exactly this reason (ADR-009).
    """
    db, sem_id, _, _ = filled_semester
    first = sorted(
        (row["event_id"], row["shift_slug"], row["slot_index"], row["assigned_member_id"])
        for row in _assigned_rows(db, sem_id)
    )

    with transaction(db):
        db.execute(
            "DELETE FROM shifts WHERE event_id IN (SELECT id FROM events WHERE semester_id = ?)",
            (sem_id,),
        )
        db.execute(
            """
            DELETE FROM auto_assign_runs
            WHERE event_id IN (SELECT id FROM events WHERE semester_id = ?)
            """,
            (sem_id,),
        )
    _run_semester(db, sem_id)

    second = sorted(
        (row["event_id"], row["shift_slug"], row["slot_index"], row["assigned_member_id"])
        for row in _assigned_rows(db, sem_id)
    )
    assert first == second


def test_placeholders_are_staffed_last(filled_semester: tuple) -> None:
    """Held dates are filled after every confirmed party, not in date order.

    Aug 25 is the first event on the calendar and a placeholder; Aug 28 is the
    first confirmed one. If the fill ran in plain date order, Aug 25 would
    consume the freshest twelve members and shift every score after it.
    """
    db, sem_id, _, _ = filled_semester
    events = events_repo.list_for_semester(db, sem_id)
    ordered = assignment.semester_fill_order(events)
    statuses = [e.planning_status for e in ordered]
    first_placeholder = statuses.index("placeholder")
    assert "confirmed" not in statuses[first_placeholder:], (
        "a confirmed party is being filled after a placeholder"
    )
    # And within each pass, still chronological.
    confirmed_dates = [e.date for e in ordered[:first_placeholder]]
    placeholder_dates = [e.date for e in ordered[first_placeholder:]]
    assert confirmed_dates == sorted(confirmed_dates)
    assert placeholder_dates == sorted(placeholder_dates)


def test_cancelling_a_placeholder_does_not_disturb_the_confirmed_calendar(
    db: sqlite3.Connection,
) -> None:
    """The whole reason for the two-pass order, stated as the property it buys.

    Roughly half of the eleven held dates will not become parties. If a
    placeholder can influence anyone's fairness score, then cancelling one means
    the entire confirmed term was balanced around a party that never happened —
    and re-running to fix it changes every brother's assignment, after they have
    been told what they are working.

    Built twice: once with the placeholders present, once with them deleted
    outright. The confirmed half must come out identical, member for member.
    """
    sem_id, _, _ = _build_fa26_world(db)
    _run_semester(db, sem_id)
    with_placeholders = sorted(
        (row["date"], row["shift_slug"], row["slot_index"], row["assigned_member_id"])
        for row in _assigned_rows(db, sem_id)
        if row["date"]
        not in {d for d, _, s in CALENDAR if s == "placeholder"}  # confirmed half only
    )

    # Same world, placeholders never entered at all.
    with transaction(db):
        db.execute(
            "DELETE FROM shifts WHERE event_id IN (SELECT id FROM events WHERE semester_id = ?)",
            (sem_id,),
        )
        db.execute(
            """
            DELETE FROM auto_assign_runs
            WHERE event_id IN (SELECT id FROM events WHERE semester_id = ?)
            """,
            (sem_id,),
        )
        db.execute(
            """
            DELETE FROM event_shift_requirements
            WHERE event_id IN (
              SELECT id FROM events WHERE semester_id = ? AND planning_status = 'placeholder'
            )
            """,
            (sem_id,),
        )
        db.execute(
            "DELETE FROM events WHERE semester_id = ? AND planning_status = 'placeholder'",
            (sem_id,),
        )
    _run_semester(db, sem_id)
    without_placeholders = sorted(
        (row["date"], row["shift_slug"], row["slot_index"], row["assigned_member_id"])
        for row in _assigned_rows(db, sem_id)
    )

    assert with_placeholders == without_placeholders
