"""Snapshot test for the ``v_strike_numbers`` view (Phase 5).

Per council session-01: the view gets a single snapshot test, not deep
property coverage — testing `ROW_NUMBER()` would be testing SQLite, not us.
The view's contract: open strikes only, partitioned by (member, semester),
ordered by (issued_on, id). This test pins exactly that.
"""

from __future__ import annotations

import pytest

from risk.db.connection import connect, transaction
from risk.db.schema import ensure_schema
from risk.repos import member_statuses as statuses_repo
from risk.repos import members as members_repo
from risk.repos import removal_methods as rm_repo
from risk.repos import semesters as semesters_repo
from risk.repos import strikes as strikes_repo
from risk.services import strike_state

pytestmark = pytest.mark.golden


def test_view_snapshot_after_issue_remove_reissue(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Open strikes only; closed rows disappear from numbering; reissue
    fills the gap and renumbers cleanly."""
    conn = connect(tmp_path / "v.db")
    ensure_schema(conn)
    sem_id = semesters_repo.insert(conn, name="SP26", starts_on="2026-01-15", ends_on="2026-05-15")
    active = statuses_repo.get_by_slug(conn, "active")
    assert active is not None
    mid = members_repo.insert(conn, slug="alice", display_name="Alice", status_id=active.id)
    voluntary = rm_repo.get_active_by_slug(conn, "voluntary_social_risk")
    assert voluntary is not None

    sids: list[int] = []
    with transaction(conn):
        for i in range(3):
            r = strike_state.issue_strike(
                conn,
                member_id=mid,
                semester_id=sem_id,
                issued_on=f"2026-02-{1 + i:02d}",
                reason=f"strike-{i + 1}",
            )
            sids.append(r.strike_id)

    snap_after_issue = [
        (r.strike_number, r.issued_on, r.reason)
        for r in strikes_repo.list_numbered_for_member_semester(
            conn, member_id=mid, semester_id=sem_id
        )
    ]
    assert snap_after_issue == [
        (1, "2026-02-01", "strike-1"),
        (2, "2026-02-02", "strike-2"),
        (3, "2026-02-03", "strike-3"),
    ]

    # Remove the middle strike — renumbering: 1 stays, 3 becomes 2.
    with transaction(conn):
        strike_state.apply_removal(
            conn,
            member_id=mid,
            removal_method_id=voluntary.id,
            performed_on="2026-02-15",
            strike_ids=[sids[1]],
        )
    snap_after_remove = [
        (r.strike_number, r.issued_on, r.reason)
        for r in strikes_repo.list_numbered_for_member_semester(
            conn, member_id=mid, semester_id=sem_id
        )
    ]
    assert snap_after_remove == [
        (1, "2026-02-01", "strike-1"),
        (2, "2026-02-03", "strike-3"),
    ]

    # Re-issue — slot 3 reappears with new strike.
    with transaction(conn):
        strike_state.issue_strike(
            conn,
            member_id=mid,
            semester_id=sem_id,
            issued_on="2026-03-01",
            reason="strike-4",
        )
    snap_after_reissue = [
        (r.strike_number, r.issued_on, r.reason)
        for r in strikes_repo.list_numbered_for_member_semester(
            conn, member_id=mid, semester_id=sem_id
        )
    ]
    assert snap_after_reissue == [
        (1, "2026-02-01", "strike-1"),
        (2, "2026-02-03", "strike-3"),
        (3, "2026-03-01", "strike-4"),
    ]
