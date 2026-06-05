"""Semester archive: pre-archive validator + carry-forward executor (Phase 7).

Per canonical plan M4: archive must force explicit disposition of pending
consequences, active strikes, future events, and open swap requests. Nothing
implicit — if blockers exist the validator surfaces them; the caller (CLI)
either resolves them or runs ``archive(force=True, carry_to=...)`` to apply
the bulk disposition described below.

Bulk disposition rule when forced:
  - Open strikes  → close at archived_at (no carry) unless carry_to set, in
                    which case they get a fresh strike row in carry_to with
                    `carried_from_strike_id` link and the original is closed.
  - Pending consequences in state='pending' → carried_forward state.
  - Open swap requests → cancelled.
  - Future events (date > archived_at) → BLOCK. Chair must cancel or move
                                          them first. We refuse to archive
                                          while future events dangle in the
                                          semester being archived.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from risk.repos import semesters as semesters_repo
from risk.repos import strikes as strikes_repo


@dataclass(frozen=True, slots=True)
class ArchiveReport:
    semester_id: int
    semester_name: str
    open_strike_count: int
    pending_consequence_count: int
    future_event_count: int
    open_swap_count: int
    blockers: tuple[str, ...]

    @property
    def is_blocked(self) -> bool:
        return bool(self.blockers)


@dataclass(frozen=True, slots=True)
class ArchiveResult:
    semester_id: int
    archived_at: str
    strikes_closed: int
    strikes_carried_forward: int
    consequences_carried_forward: int
    swaps_cancelled: int
    carry_to_semester_id: int | None


def validate(conn: sqlite3.Connection, *, semester_id: int) -> ArchiveReport:
    sem = _semester_or_raise(conn, semester_id)
    open_strikes = conn.execute(
        """
        SELECT COUNT(*) AS n FROM strikes
        WHERE semester_id = ? AND closed_at IS NULL
        """,
        (semester_id,),
    ).fetchone()["n"]
    pending_pcs = conn.execute(
        """
        SELECT COUNT(*) AS n FROM pending_consequences
        WHERE semester_id = ? AND state = 'pending'
        """,
        (semester_id,),
    ).fetchone()["n"]
    future_events = conn.execute(
        """
        SELECT COUNT(*) AS n FROM events
        WHERE semester_id = ? AND status NOT IN ('completed', 'cancelled')
        """,
        (semester_id,),
    ).fetchone()["n"]
    open_swaps = conn.execute(
        """
        SELECT COUNT(*) AS n FROM swap_requests
        WHERE semester_id = ? AND state = 'open'
        """,
        (semester_id,),
    ).fetchone()["n"]

    blockers: list[str] = []
    if open_strikes:
        blockers.append(f"{open_strikes} open strike(s)")
    if pending_pcs:
        blockers.append(f"{pending_pcs} pending threshold-consequence(s)")
    if future_events:
        blockers.append(f"{future_events} non-terminal event(s)")
    if open_swaps:
        blockers.append(f"{open_swaps} open swap request(s)")

    return ArchiveReport(
        semester_id=semester_id,
        semester_name=sem.name,
        open_strike_count=int(open_strikes),
        pending_consequence_count=int(pending_pcs),
        future_event_count=int(future_events),
        open_swap_count=int(open_swaps),
        blockers=tuple(blockers),
    )


def archive(
    conn: sqlite3.Connection,
    *,
    semester_id: int,
    archived_at: str,
    force: bool = False,
    carry_to_semester_id: int | None = None,
) -> ArchiveResult:
    """Apply bulk disposition then archive.

    Non-forced path requires a clean validate() — no blockers at all.
    Forced path bulk-disposes strikes/pcs/swaps but still refuses to archive
    if any non-terminal events exist (those need explicit chair action).
    """
    report = validate(conn, semester_id=semester_id)
    if report.future_event_count > 0:
        raise ValueError(
            f"semester has {report.future_event_count} non-terminal event(s) — "
            "complete or cancel them before archiving"
        )
    if report.is_blocked and not force:
        raise ValueError(
            f"semester has blockers: {', '.join(report.blockers)}. "
            "Re-run with force=True (and carry_to to preserve strikes)."
        )

    strikes_closed = 0
    strikes_carried = 0
    pcs_carried = 0
    swaps_cancelled = 0

    if force:
        # Open strikes.
        open_ids = [
            int(r["id"])
            for r in conn.execute(
                """
                SELECT id FROM strikes
                WHERE semester_id = ? AND closed_at IS NULL
                ORDER BY issued_on, id
                """,
                (semester_id,),
            ).fetchall()
        ]
        for sid in open_ids:
            strike = strikes_repo.get_by_id(conn, sid)
            assert strike is not None
            if carry_to_semester_id is not None:
                new_id = strikes_repo.insert(
                    conn,
                    member_id=strike.member_id,
                    semester_id=carry_to_semester_id,
                    issued_on=_earliest_open_date(conn, carry_to_semester_id),
                    reason=f"carried from {report.semester_name}: {strike.reason}",
                    carried_from_strike_id=sid,
                )
                _ = new_id
                strikes_carried += 1
            strikes_repo.close(conn, sid, closed_at=archived_at)
            if carry_to_semester_id is None:
                strikes_closed += 1

        # Pending consequences → carried_forward state.
        cur_pcs = conn.execute(
            """
            UPDATE pending_consequences
            SET state = 'carried_forward', resolved_at = ?
            WHERE semester_id = ? AND state = 'pending'
            """,
            (archived_at, semester_id),
        )
        pcs_carried = cur_pcs.rowcount

        # Open swap requests → cancelled.
        cur_sr = conn.execute(
            """
            UPDATE swap_requests
            SET state = 'cancelled', resolved_at = ?
            WHERE semester_id = ? AND state = 'open'
            """,
            (archived_at, semester_id),
        )
        swaps_cancelled = cur_sr.rowcount

    marked = semesters_repo.mark_archived(
        conn, semester_id=semester_id, archived_at=archived_at
    )
    if marked == 0:
        raise RuntimeError("semester already archived")

    return ArchiveResult(
        semester_id=semester_id,
        archived_at=archived_at,
        strikes_closed=strikes_closed,
        strikes_carried_forward=strikes_carried,
        consequences_carried_forward=pcs_carried,
        swaps_cancelled=swaps_cancelled,
        carry_to_semester_id=carry_to_semester_id,
    )


def unarchive(conn: sqlite3.Connection, *, semester_id: int) -> None:
    """Clear archived_at. Strikes that were carried-forward stay carried;
    chair must manually reverse if that's wanted."""
    sem = _semester_or_raise(conn, semester_id)
    if sem.archived_at is None:
        raise ValueError(f"semester {sem.name!r} is not archived")
    semesters_repo.unarchive(conn, semester_id=semester_id)


def _semester_or_raise(
    conn: sqlite3.Connection, semester_id: int
) -> semesters_repo.Semester:
    row = conn.execute(
        "SELECT * FROM semesters WHERE id = ?", (semester_id,)
    ).fetchone()
    if row is None:
        raise LookupError(f"semester {semester_id} not found")
    return semesters_repo._row_to_semester(row)


def _earliest_open_date(conn: sqlite3.Connection, semester_id: int) -> str:
    row = conn.execute(
        "SELECT starts_on FROM semesters WHERE id = ?", (semester_id,)
    ).fetchone()
    if row is None:
        raise LookupError(f"carry-to semester {semester_id} not found")
    return str(row["starts_on"])
