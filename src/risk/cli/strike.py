"""``risk strike ...`` subcommands (Phase 5)."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from typing import Annotated

import typer

from risk.cli._common import mode_from_ctx, open_conn
from risk.cli.output import OutputMode, attr_table, emit_error, emit_success
from risk.db.connection import transaction
from risk.repos import members as members_repo
from risk.repos import pending_consequences as pc_repo
from risk.repos import removal_methods as rm_repo
from risk.repos import semesters as semesters_repo
from risk.repos import strikes as strikes_repo
from risk.services import policy, strike_state

app = typer.Typer(help="Issue, list, and remove strikes.")

consequences_app = typer.Typer(help="Manage threshold-consequences (extra_shift / probation / expulsion_review).")
app.add_typer(consequences_app, name="consequences")


def _resolve_semester(
    conn: sqlite3.Connection, mode: OutputMode, semester_name: str | None
) -> int:
    if semester_name is not None:
        sem = semesters_repo.get_by_name(conn, semester_name)
        if sem is None:
            emit_error("semester.not_found", f"No semester named {semester_name!r}.", mode=mode)
        else:
            return sem.id
    current = semesters_repo.get_current(conn)
    if current is None:
        emit_error(
            "semester.no_current",
            "No --semester given and no current semester set.",
            mode=mode,
        )
    assert current is not None
    return current.id


@app.command("issue")
def issue(
    ctx: typer.Context,
    member: Annotated[str, typer.Argument(help="Member slug, id, or alias.")],
    reason: Annotated[str, typer.Option("--reason", help="Why the strike was issued.")],
    on: Annotated[str, typer.Option("--on", help="ISO YYYY-MM-DD issuance date.")],
    shift_id: Annotated[
        int | None, typer.Option("--shift", help="Optional shift link (for no-show).")
    ] = None,
    semester: Annotated[
        str | None, typer.Option("--semester", help="Defaults to current semester.")
    ] = None,
) -> None:
    """Issue a new strike, emitting any threshold-consequences crossed."""
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    m = members_repo.resolve(conn, member)
    if m is None:
        emit_error("member.not_found", f"No member matching {member!r}.", mode=mode)
        return
    sem_id = _resolve_semester(conn, mode, semester)
    try:
        with transaction(conn):
            result = strike_state.issue_strike(
                conn,
                member_id=m.id,
                semester_id=sem_id,
                issued_on=on,
                reason=reason,
                shift_id=shift_id,
            )
    except sqlite3.IntegrityError as exc:
        emit_error("strike.integrity", str(exc), mode=mode)
        return

    payload = {
        "strike_id": result.strike_id,
        "strike_number": result.strike_number,
        "new_consequences": list(result.new_consequences),
        "in_bad_standing": policy.in_bad_standing(
            strikes_repo.count_active(conn, member_id=m.id, semester_id=sem_id)
        ),
    }
    emit_success(payload, mode=mode)


@app.command("standing")
def standing(
    ctx: typer.Context,
    member: Annotated[str, typer.Argument(help="Member slug, id, or alias.")],
    semester: Annotated[
        str | None, typer.Option("--semester", help="Defaults to current semester.")
    ] = None,
) -> None:
    """Show active-semester standing (active count, kinds, lifetime footer)."""
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    m = members_repo.resolve(conn, member)
    if m is None:
        emit_error("member.not_found", f"No member matching {member!r}.", mode=mode)
        return
    sem_id = _resolve_semester(conn, mode, semester)
    active_count = strikes_repo.count_active(conn, member_id=m.id, semester_id=sem_id)
    total_this_sem = strikes_repo.count_total_in_semester(
        conn, member_id=m.id, semester_id=sem_id
    )
    pending = pc_repo.list_for_member_semester(
        conn, member_id=m.id, semester_id=sem_id
    )
    lifetime_row = conn.execute(
        "SELECT COUNT(*) AS n FROM strikes WHERE member_id = ?", (m.id,)
    ).fetchone()
    payload = {
        "member": m.slug,
        "semester_id": sem_id,
        "active_strike_count": active_count,
        "total_strikes_issued_in_semester": total_this_sem,
        "in_bad_standing": policy.in_bad_standing(active_count),
        "pending_consequences": [asdict(p) for p in pending],
        "lifetime_strike_count": int(lifetime_row["n"]),
    }
    emit_success(payload, mode=mode)


@app.command("list")
def list_strikes(
    ctx: typer.Context,
    member: Annotated[
        str | None, typer.Option("--member", help="Filter by member slug/id/alias.")
    ] = None,
    semester: Annotated[
        str | None, typer.Option("--semester", help="Filter by semester (default: current).")
    ] = None,
    include_closed: Annotated[
        bool, typer.Option("--include-closed", help="Include removed strikes.")
    ] = False,
) -> None:
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    if member is None:
        emit_error("strike.list.member_required", "--member is required.", mode=mode)
        return
    m = members_repo.resolve(conn, member)
    if m is None:
        emit_error("member.not_found", f"No member matching {member!r}.", mode=mode)
        return
    sem_id = _resolve_semester(conn, mode, semester)
    if include_closed:
        rows = strikes_repo.list_for_member_semester(
            conn, member_id=m.id, semester_id=sem_id, include_closed=True
        )
        payload_rows = [asdict(r) for r in rows]
        table = attr_table(
            f"All strikes for {m.slug} (semester={sem_id})",
            rows,
            cols=(
                ("ID", "id"),
                ("Issued", "issued_on"),
                ("Reason", "reason"),
                ("Closed", "closed_at"),
            ),
        )
    else:
        rows_numbered = strikes_repo.list_numbered_for_member_semester(
            conn, member_id=m.id, semester_id=sem_id
        )
        payload_rows = [asdict(r) for r in rows_numbered]
        table = attr_table(
            f"Open strikes for {m.slug} (semester={sem_id})",
            rows_numbered,
            cols=(
                ("#", "strike_number"),
                ("ID", "id"),
                ("Issued", "issued_on"),
                ("Reason", "reason"),
            ),
        )
    emit_success({"strikes": payload_rows}, mode=mode, table=table)


def _parse_strike_ids(raw: str) -> list[int]:
    out: list[int] = []
    for piece in raw.split(","):
        piece = piece.strip()
        if not piece:
            continue
        try:
            out.append(int(piece))
        except ValueError as exc:
            raise typer.BadParameter(f"strike id {piece!r} is not an integer") from exc
    return out


@app.command("remove")
def remove(
    ctx: typer.Context,
    member: Annotated[str, typer.Argument(help="Member slug, id, or alias.")],
    method: Annotated[str, typer.Option("--method", help="Removal method slug.")],
    on: Annotated[str, typer.Option("--on", help="ISO YYYY-MM-DD performed date.")],
    strike_ids: Annotated[
        str,
        typer.Option(
            "--strikes",
            help="Comma-separated strike IDs to close (e.g. 7,12).",
        ),
    ],
    by: Annotated[
        str | None, typer.Option("--by", help="Performed-by member slug.")
    ] = None,
    notes: Annotated[str | None, typer.Option("--notes")] = None,
) -> None:
    """Record a removal, close the linked strikes, re-derive numbering."""
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    m = members_repo.resolve(conn, member)
    if m is None:
        emit_error("member.not_found", f"No member matching {member!r}.", mode=mode)
        return
    rm = rm_repo.get_active_by_slug(conn, method)
    if rm is None:
        emit_error(
            "removal_method.not_found", f"No active removal method {method!r}.", mode=mode
        )
        return
    performed_by = None
    if by is not None:
        bym = members_repo.resolve(conn, by)
        if bym is None:
            emit_error("member.not_found", f"No member matching {by!r}.", mode=mode)
            return
        performed_by = bym.id
    sids = _parse_strike_ids(strike_ids)
    if not sids:
        emit_error("strike.remove.empty", "--strikes must list at least one id.", mode=mode)
        return
    try:
        with transaction(conn):
            result = strike_state.apply_removal(
                conn,
                member_id=m.id,
                removal_method_id=rm.id,
                performed_on=on,
                strike_ids=sids,
                performed_by_member_id=performed_by,
                notes=notes,
            )
    except (LookupError, ValueError) as exc:
        emit_error("strike.remove.bad_link", str(exc), mode=mode)
        return
    except sqlite3.IntegrityError as exc:
        emit_error("strike.remove.integrity", str(exc), mode=mode)
        return

    emit_success(
        {
            "removal_id": result.removal_id,
            "closed_strike_ids": list(result.closed_strike_ids),
            "active_count_after": result.active_count_after,
            "method": rm.slug,
        },
        mode=mode,
    )


@consequences_app.command("list")
def consequences_list(
    ctx: typer.Context,
    state: Annotated[
        str | None,
        typer.Option(
            "--state", help="Filter by state (pending/served/waived/carried_forward)."
        ),
    ] = None,
) -> None:
    """List threshold-consequences across all members/semesters, optionally filtered."""
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    rows = pc_repo.list_all_with_state(conn, state=state)
    emit_success(
        {"consequences": [asdict(r) for r in rows]},
        mode=mode,
        table=attr_table(
            "Threshold-consequences" + (f" (state={state})" if state else ""),
            rows,
            cols=(
                ("ID", "id"),
                ("Member", "member_id"),
                ("Semester", "semester_id"),
                ("Kind", "kind"),
                ("State", "state"),
                ("Trig. strike", "triggering_strike_id"),
                ("Created", "created_at"),
            ),
        ),
    )


@consequences_app.command("resolve")
def consequences_resolve(
    ctx: typer.Context,
    pc_id: Annotated[int, typer.Argument(help="pending_consequences.id")],
    as_: Annotated[
        str,
        typer.Option(
            "--as", help="New state: served | waived | carried_forward."
        ),
    ],
) -> None:
    """Transition a pending threshold-consequence to served/waived/carried_forward."""
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    if as_ not in ("served", "waived", "carried_forward"):
        emit_error(
            "consequence.resolve.bad_state",
            f"--as must be served|waived|carried_forward, got {as_!r}.",
            mode=mode,
        )
        return
    try:
        with transaction(conn):
            updated = pc_repo.resolve(conn, pc_id, new_state=as_)
    except sqlite3.IntegrityError as exc:
        emit_error("consequence.resolve.integrity", str(exc), mode=mode)
        return
    if updated == 0:
        emit_error(
            "consequence.resolve.not_pending",
            f"No pending consequence with id={pc_id}.",
            mode=mode,
        )
        return
    pc = pc_repo.get_by_id(conn, pc_id)
    assert pc is not None
    emit_success({"consequence": asdict(pc)}, mode=mode)


@consequences_app.command("pending-for")
def consequences_pending_for(
    ctx: typer.Context,
    member: Annotated[str, typer.Argument(help="Member slug, id, or alias.")],
) -> None:
    """Show all pending threshold-consequences for one member, across semesters."""
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    m = members_repo.resolve(conn, member)
    if m is None:
        emit_error("member.not_found", f"No member matching {member!r}.", mode=mode)
        return
    rows = pc_repo.list_pending_for_member(conn, member_id=m.id)
    emit_success(
        {"member": m.slug, "pending": [asdict(r) for r in rows]},
        mode=mode,
        table=attr_table(
            f"Pending threshold-consequences for {m.slug}",
            rows,
            cols=(
                ("ID", "id"),
                ("Semester", "semester_id"),
                ("Kind", "kind"),
                ("Trig. strike", "triggering_strike_id"),
                ("Created", "created_at"),
            ),
        ),
    )
