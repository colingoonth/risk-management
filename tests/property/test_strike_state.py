"""Write-boundary property tests for the strike state machine (Phase 5).

Anchored at the WRITE boundary per council session-01: the bug surface is the
issue → remove → re-issue sequence, not the `v_strike_numbers` view alone.

Properties:
  P1 (monotonicity). For a fixed (member, semester), strike `issued_on` is
     monotone non-decreasing in insertion order. The DB trigger
     `trg_strikes_monotonic` MUST raise on a back-dated insert.
  P2 (numbering coherence). After N consecutive open strikes, the open set's
     `strike_number` values are exactly {1, ..., N}.
  P3 (threshold-emission idempotency). Calling `derive_for_member_semester`
     after `issue_strike` is a no-op — re-derivation produces zero new rows.
  P4 (threshold-emission monotonicity over removal). Removing strikes that
     drop the active count below a previously-crossed threshold does NOT
     delete the pending_consequences row for that threshold.
  P5 (per-semester scope). A member's strike count in semester A is never
     affected by strikes in semester B.
"""

from __future__ import annotations

import sqlite3

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from risk.db.connection import connect, transaction
from risk.db.schema import ensure_schema
from risk.repos import member_statuses as statuses_repo
from risk.repos import members as members_repo
from risk.repos import pending_consequences as pc_repo
from risk.repos import removal_methods as rm_repo
from risk.repos import semesters as semesters_repo
from risk.repos import strikes as strikes_repo
from risk.services import strike_state

pytestmark = pytest.mark.property


def _fresh_world(tmp_path) -> tuple[sqlite3.Connection, int, int]:  # type: ignore[no-untyped-def]
    """Spin up a DB, one semester, one active member. Return (conn, member_id, semester_id)."""
    conn = connect(tmp_path / "p.db")
    ensure_schema(conn)
    sem_id = semesters_repo.insert(
        conn, name="SP26", starts_on="2026-01-15", ends_on="2026-05-15"
    )
    active = statuses_repo.get_by_slug(conn, "active")
    assert active is not None
    m_id = members_repo.insert(
        conn,
        slug="m-test",
        display_name="M Test",
        status_id=active.id,
    )
    return conn, m_id, sem_id


# --- P1: monotonicity trigger blocks back-dating ---


def test_monotonic_trigger_blocks_back_dated_insert(tmp_path) -> None:  # type: ignore[no-untyped-def]
    conn, mid, sid = _fresh_world(tmp_path)
    with transaction(conn):
        strike_state.issue_strike(
            conn, member_id=mid, semester_id=sid, issued_on="2026-03-10", reason="A"
        )
    with pytest.raises(sqlite3.IntegrityError) as exc, transaction(conn):
        strike_state.issue_strike(
            conn,
            member_id=mid,
            semester_id=sid,
            issued_on="2026-02-15",  # before existing
            reason="back-dated",
        )
    assert "precedes" in str(exc.value).lower()


# --- P2: numbering matches insertion order ---


@given(n=st.integers(min_value=1, max_value=10))
@settings(max_examples=15, deadline=None)
def test_numbering_is_1_through_n(tmp_path_factory, n: int) -> None:  # type: ignore[no-untyped-def]
    tmp_path = tmp_path_factory.mktemp(f"n{n}")
    conn, mid, sid = _fresh_world(tmp_path)
    issued_dates = [f"2026-02-{1 + i:02d}" for i in range(n)]
    with transaction(conn):
        for i, d in enumerate(issued_dates):
            strike_state.issue_strike(
                conn,
                member_id=mid,
                semester_id=sid,
                issued_on=d,
                reason=f"strike-{i + 1}",
            )
    rows = strikes_repo.list_numbered_for_member_semester(
        conn, member_id=mid, semester_id=sid
    )
    assert [r.strike_number for r in rows] == list(range(1, n + 1))


# --- P3: threshold-emission idempotency at the write boundary ---


@given(n=st.integers(min_value=0, max_value=8))
@settings(max_examples=12, deadline=None)
def test_derive_is_idempotent_after_issue(tmp_path_factory, n: int) -> None:  # type: ignore[no-untyped-def]
    tmp_path = tmp_path_factory.mktemp(f"idem{n}")
    conn, mid, sid = _fresh_world(tmp_path)
    with transaction(conn):
        for i in range(n):
            strike_state.issue_strike(
                conn,
                member_id=mid,
                semester_id=sid,
                issued_on=f"2026-02-{1 + i:02d}",
                reason=f"r{i}",
            )
    with transaction(conn):
        again = strike_state.derive_for_member_semester(
            conn, member_id=mid, semester_id=sid
        )
    assert again == (), f"second derive produced new kinds {again} at n={n}"


def test_issue_remove_reissue_does_not_duplicate_consequence(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The premortem failure surface: issue → remove → re-issue must NOT
    emit a duplicate probation row. UNIQUE(member, semester, kind) backs the
    invariant; this test asserts the write-boundary respects it."""
    conn, mid, sid = _fresh_world(tmp_path)
    voluntary = rm_repo.get_active_by_slug(conn, "voluntary_social_risk")
    assert voluntary is not None

    # Issue 4 strikes — crosses probation threshold (4).
    strike_ids: list[int] = []
    with transaction(conn):
        for i in range(4):
            r = strike_state.issue_strike(
                conn,
                member_id=mid,
                semester_id=sid,
                issued_on=f"2026-02-{1 + i:02d}",
                reason=f"r{i}",
            )
            strike_ids.append(r.strike_id)

    pcs_after_issue = pc_repo.list_for_member_semester(
        conn, member_id=mid, semester_id=sid
    )
    kinds_after_issue = sorted(p.kind for p in pcs_after_issue)
    assert kinds_after_issue == ["extra_shift", "probation"]

    # Remove all four strikes.
    with transaction(conn):
        strike_state.apply_removal(
            conn,
            member_id=mid,
            removal_method_id=voluntary.id,
            performed_on="2026-03-01",
            strike_ids=strike_ids,
        )

    pcs_after_removal = pc_repo.list_for_member_semester(
        conn, member_id=mid, semester_id=sid
    )
    # P4: removal does NOT delete the threshold consequence rows.
    assert sorted(p.kind for p in pcs_after_removal) == ["extra_shift", "probation"]
    assert strikes_repo.count_active(conn, member_id=mid, semester_id=sid) == 0

    # Re-issue 4 more strikes — must NOT duplicate the kind rows.
    with transaction(conn):
        for i in range(4):
            strike_state.issue_strike(
                conn,
                member_id=mid,
                semester_id=sid,
                issued_on=f"2026-04-{1 + i:02d}",
                reason=f"r{i}b",
            )

    pcs_final = pc_repo.list_for_member_semester(
        conn, member_id=mid, semester_id=sid
    )
    assert sorted(p.kind for p in pcs_final) == ["extra_shift", "probation"]
    assert len(pcs_final) == 2  # not 4 — UNIQUE(member, sem, kind) holds


# --- P5: per-semester scope (ADR-002) ---


def test_strikes_in_other_semester_do_not_affect_count(tmp_path) -> None:  # type: ignore[no-untyped-def]
    conn, mid, sem_a = _fresh_world(tmp_path)
    sem_b = semesters_repo.insert(
        conn, name="FA26", starts_on="2026-08-15", ends_on="2026-12-15"
    )
    with transaction(conn):
        for i in range(3):
            strike_state.issue_strike(
                conn,
                member_id=mid,
                semester_id=sem_a,
                issued_on=f"2026-02-{1 + i:02d}",
                reason=f"a{i}",
            )
        strike_state.issue_strike(
            conn,
            member_id=mid,
            semester_id=sem_b,
            issued_on="2026-09-01",
            reason="b1",
        )
    assert strikes_repo.count_active(conn, member_id=mid, semester_id=sem_a) == 3
    assert strikes_repo.count_active(conn, member_id=mid, semester_id=sem_b) == 1


def test_threshold_crossing_emits_exactly_at_threshold(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Strike N=1 emits nothing; strike N=2 emits extra_shift only."""
    conn, mid, sid = _fresh_world(tmp_path)
    with transaction(conn):
        r1 = strike_state.issue_strike(
            conn,
            member_id=mid,
            semester_id=sid,
            issued_on="2026-02-01",
            reason="r1",
        )
    assert r1.new_consequences == ()
    with transaction(conn):
        r2 = strike_state.issue_strike(
            conn,
            member_id=mid,
            semester_id=sid,
            issued_on="2026-02-02",
            reason="r2",
        )
    assert r2.new_consequences == ("extra_shift",)
    with transaction(conn):
        r3 = strike_state.issue_strike(
            conn,
            member_id=mid,
            semester_id=sid,
            issued_on="2026-02-03",
            reason="r3",
        )
    assert r3.new_consequences == ()  # not a threshold
    with transaction(conn):
        r4 = strike_state.issue_strike(
            conn,
            member_id=mid,
            semester_id=sid,
            issued_on="2026-02-04",
            reason="r4",
        )
    assert r4.new_consequences == ("probation",)
    with transaction(conn):
        r5 = strike_state.issue_strike(
            conn,
            member_id=mid,
            semester_id=sid,
            issued_on="2026-02-05",
            reason="r5",
        )
    assert r5.new_consequences == ("expulsion_review",)
