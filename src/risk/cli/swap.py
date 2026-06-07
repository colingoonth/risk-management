"""``risk swap ...`` subcommands (Phase 6)."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from typing import Annotated

import typer

from risk.cli._common import mode_from_ctx, open_conn
from risk.cli.output import attr_table, emit_error, emit_success
from risk.db.connection import transaction
from risk.repos import events as events_repo
from risk.repos import members as members_repo
from risk.repos import shifts as shifts_repo
from risk.repos import swap_requests as sr_repo
from risk.services import swaps

app = typer.Typer(help="Request and resolve shift swaps.")


@app.command("request")
def request(
    ctx: typer.Context,
    from_shift: Annotated[int, typer.Option("--from-shift", help="Shift the initiator is currently assigned to.")],
    to_shift: Annotated[
        int | None, typer.Option("--to-shift", help="Optional target open or assigned shift.")
    ] = None,
    counterparty: Annotated[
        str | None,
        typer.Option(
            "--counterparty",
            help="Member slug/id to take over the from-shift (if no --to-shift).",
        ),
    ] = None,
) -> None:
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    fs = shifts_repo.get_by_id(conn, from_shift)
    if fs is None:
        emit_error("swap.from_shift_not_found", f"shift {from_shift} not found.", mode=mode)
        return
    if fs.assigned_member_id is None:
        emit_error(
            "swap.from_shift_open",
            f"shift {from_shift} is not assigned — cannot initiate swap.",
            mode=mode,
        )
        return
    event = events_repo.get_by_id(conn, fs.event_id)
    assert event is not None
    cp_id: int | None = None
    if counterparty is not None:
        cp = members_repo.resolve(conn, counterparty)
        if cp is None:
            emit_error(
                "member.not_found", f"No member matching {counterparty!r}.", mode=mode
            )
            return
        cp_id = cp.id
    try:
        with transaction(conn):
            req_id = swaps.request_swap(
                conn,
                semester_id=event.semester_id,
                from_shift_id=fs.id,
                initiator_member_id=fs.assigned_member_id,
                to_shift_id=to_shift,
                counterparty_member_id=cp_id,
            )
    except (LookupError, ValueError) as exc:
        # Wrap the raw service exception with chair-friendly slug context +
        # a discovery hint pointing at `risk shift list` so the chair knows
        # how to find valid shift IDs.
        hint = (
            f"Initiator {fs.assigned_member_slug or fs.assigned_member_id} holds "
            f"shift {fs.id} ({fs.shift_type_slug}). "
            "Try `risk shift list --member <slug>` to see their other assignments."
        )
        emit_error("swap.request_invalid", f"{exc} — {hint}", mode=mode)
        return
    except sqlite3.IntegrityError as exc:
        emit_error("swap.request_integrity", str(exc), mode=mode)
        return
    row = sr_repo.get_by_id(conn, req_id)
    assert row is not None
    emit_success({"swap_request": asdict(row)}, mode=mode)


@app.command("accept")
def accept(
    ctx: typer.Context,
    req_id: Annotated[int, typer.Argument(help="swap_requests.id")],
) -> None:
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    req = sr_repo.get_by_id(conn, req_id)
    if req is None:
        emit_error("swap.not_found", f"swap request {req_id} not found.", mode=mode)
        return
    fs = shifts_repo.get_by_id(conn, req.from_shift_id)
    if fs is None:
        emit_error(
            "swap.from_shift_missing",
            f"shift {req.from_shift_id} (the swap's from_shift) no longer exists — "
            "the underlying shift may have been deleted or the swap is stale. "
            "Try `risk swap list` for current swap state.",
            mode=mode,
        )
        return
    ev = events_repo.get_by_id(conn, fs.event_id)
    assert ev is not None
    try:
        with transaction(conn):
            result = swaps.accept_swap(conn, request_id=req_id, assigned_at=ev.date)
    except (LookupError, ValueError, RuntimeError) as exc:
        emit_error("swap.accept_invalid", str(exc), mode=mode)
        return
    except sqlite3.IntegrityError as exc:
        emit_error("swap.accept_integrity", str(exc), mode=mode)
        return
    emit_success({"swap_result": asdict(result)}, mode=mode)


@app.command("reject")
def reject(
    ctx: typer.Context,
    req_id: Annotated[int, typer.Argument(help="swap_requests.id")],
) -> None:
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    try:
        with transaction(conn):
            swaps.reject_swap(conn, request_id=req_id)
    except (LookupError, ValueError) as exc:
        emit_error("swap.reject_invalid", str(exc), mode=mode)
        return
    emit_success({"request_id": req_id, "state": "rejected"}, mode=mode)


@app.command("cancel")
def cancel(
    ctx: typer.Context,
    req_id: Annotated[int, typer.Argument(help="swap_requests.id")],
) -> None:
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    try:
        with transaction(conn):
            swaps.cancel_swap(conn, request_id=req_id)
    except (LookupError, ValueError) as exc:
        emit_error("swap.cancel_invalid", str(exc), mode=mode)
        return
    emit_success({"request_id": req_id, "state": "cancelled"}, mode=mode)


@app.command("list")
def list_requests(
    ctx: typer.Context,
    state: Annotated[
        str | None,
        typer.Option("--state", help="Filter by state (open/accepted/rejected/cancelled)."),
    ] = None,
) -> None:
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    rows = sr_repo.list_all(conn, state=state)
    emit_success(
        {"swap_requests": [asdict(r) for r in rows]},
        mode=mode,
        table=attr_table(
            "Swap requests" + (f" (state={state})" if state else ""),
            rows,
            cols=(
                ("ID", "id"),
                ("State", "state"),
                ("From shift", "from_shift_id"),
                ("To shift", "to_shift_id"),
                ("Initiator", "initiator_slug"),
                ("Counterparty", "counterparty_slug"),
                ("Created", "created_at"),
            ),
        ),
    )
