"""``risk member ...`` subcommands."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from typing import Annotated, Any

import typer

from risk.cli._common import mode_from_ctx, open_conn
from risk.cli.output import attr_table, emit_error, emit_success, simple_lookup_table
from risk.db.connection import transaction
from risk.repos import houses as houses_repo
from risk.repos import member_house_assignments as mha_repo
from risk.repos import member_roles as mr_repo
from risk.repos import member_statuses as statuses_repo
from risk.repos import members as repo
from risk.repos import roles as roles_repo
from risk.repos import semesters as semesters_repo

app = typer.Typer(help="Manage chapter members.")


def _resolve_semester(conn: sqlite3.Connection, mode: Any, semester_name: str | None) -> int:
    """Resolve to a semester id. Default to current; emit_error if none and no name given."""
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
            "No --semester given and no current semester set (use `risk semester set-current`).",
            mode=mode,
        )
    assert current is not None
    return current.id


@app.command("add")
def add(
    ctx: typer.Context,
    slug: Annotated[str, typer.Argument(help="Lowercase slug, e.g. 'colin-guenther'.")],
    display_name: Annotated[str, typer.Option("--display-name")],
    status: Annotated[str, typer.Option("--status", help="Member status slug.")] = "active",
    class_year: Annotated[
        int | None, typer.Option("--class-year", help="Graduation year (e.g. 2027).")
    ] = None,
) -> None:
    """Register a new chapter member.

    Example:
        risk member add alice --display-name "Alice" --status active --class-year 2027
    """
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    status_row = statuses_repo.get_by_slug(conn, status)
    if status_row is None:
        emit_error("status.not_found", f"No member status with slug {status!r}.", mode=mode)
        return
    try:
        with transaction(conn):
            repo.insert(
                conn,
                slug=slug,
                display_name=display_name,
                status_id=status_row.id,
                class_year=class_year,
            )
    except sqlite3.IntegrityError:
        emit_error(
            "member.integrity",
            f"A member with slug {slug!r} already exists — use a different slug or "
            f"run 'risk member list' to see existing members.",
            mode=mode,
        )
        return
    member = repo.get_by_slug(conn, slug)
    assert member is not None
    emit_success(
        asdict(member),
        mode=mode,
        table=simple_lookup_table(
            "Members",
            [member],
            extra_cols=(
                ("Status", "status_slug"),
                ("Class year", "class_year"),
            ),
        ),
    )


@app.command("list")
def list_(
    ctx: typer.Context,
    status: Annotated[str | None, typer.Option("--status", help="Filter by status slug.")] = None,
    role: Annotated[
        str | None,
        typer.Option("--role", help="Filter by role slug in --semester (default current)."),
    ] = None,
    semester: Annotated[
        str | None, typer.Option("--semester", help="Semester for the --role filter.")
    ] = None,
) -> None:
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)

    if role is not None:
        sem_id = _resolve_semester(conn, mode, semester)
        role_row = roles_repo.get_by_slug(conn, role)
        if role_row is None:
            emit_error("role.not_found", f"No role with slug {role!r}.", mode=mode)
            return
        rows = mr_repo.list_for_semester(conn, sem_id)
        role_holders = [r for r in rows if r.role_slug == role]
        emit_success(
            [asdict(m) for m in role_holders],
            mode=mode,
            table=attr_table(
                f"Members with role={role} in semester {sem_id}",
                role_holders,
                cols=(
                    ("Member", "member_slug"),
                    ("Role", "role_slug"),
                    ("Semester", "semester_name"),
                ),
            ),
        )
        return

    if status is not None:
        if statuses_repo.get_by_slug(conn, status) is None:
            emit_error("status.not_found", f"No member status with slug {status!r}.", mode=mode)
            return
        members = repo.list_by_status(conn, status)
    else:
        members = repo.list_all(conn)

    emit_success(
        [asdict(m) for m in members],
        mode=mode,
        table=simple_lookup_table(
            "Members",
            members,
            extra_cols=(
                ("Status", "status_slug"),
                ("Class year", "class_year"),
            ),
        ),
    )


@app.command("show")
def show(
    ctx: typer.Context,
    member: Annotated[str, typer.Argument(help="Member id, slug, or alias.")],
    semester: Annotated[
        str | None, typer.Option("--semester", help="Restrict role/house views to this semester.")
    ] = None,
) -> None:
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    m = repo.resolve(conn, member)
    if m is None:
        emit_error("member.not_found", f"Could not resolve {member!r}.", mode=mode)
        return
    aliases = repo.list_aliases(conn, m.id)
    sem_id = _resolve_semester(conn, mode, semester) if semester is not None else None
    if sem_id is not None:
        roles = mr_repo.list_for_member_in_semester(conn, member_id=m.id, semester_id=sem_id)
        house = mha_repo.get_for_member_in_semester(conn, member_id=m.id, semester_id=sem_id)
    else:
        roles = mr_repo.list_for_member(conn, m.id)
        house = None
    emit_success(
        {
            "member": asdict(m),
            "aliases": [asdict(a) for a in aliases],
            "roles": [asdict(r) for r in roles],
            "house": asdict(house) if house else None,
        },
        mode=mode,
    )


@app.command("set-role")
def set_role(
    ctx: typer.Context,
    member: Annotated[str, typer.Argument(help="Member slug, ID, or alias.")],
    role: Annotated[
        str,
        typer.Argument(help="Role slug (e.g. exec, risk_chair, dj, pledge_chair)."),
    ],
    semester: Annotated[str | None, typer.Option("--semester")] = None,
) -> None:
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    m = repo.resolve(conn, member)
    r = roles_repo.get_by_slug(conn, role)
    if m is None:
        emit_error("member.not_found", f"Could not resolve {member!r}.", mode=mode)
        return
    if r is None:
        emit_error("role.not_found", f"No role with slug {role!r}.", mode=mode)
        return
    sem_id = _resolve_semester(conn, mode, semester)
    with transaction(conn):
        mr_repo.set_role(conn, member_id=m.id, role_id=r.id, semester_id=sem_id)
    emit_success(
        {
            "member": m.slug,
            "role": r.slug,
            "semester_id": sem_id,
            "applied": True,
        },
        mode=mode,
    )


@app.command("unset-role")
def unset_role(
    ctx: typer.Context,
    member: Annotated[str, typer.Argument(help="Member slug, ID, or alias.")],
    role: Annotated[str, typer.Argument(help="Role slug to remove.")],
    semester: Annotated[str | None, typer.Option("--semester")] = None,
) -> None:
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    m = repo.resolve(conn, member)
    r = roles_repo.get_by_slug(conn, role)
    if m is None:
        emit_error("member.not_found", f"Could not resolve {member!r}.", mode=mode)
        return
    if r is None:
        emit_error("role.not_found", f"No role with slug {role!r}.", mode=mode)
        return
    sem_id = _resolve_semester(conn, mode, semester)
    with transaction(conn):
        affected = mr_repo.unset_role(conn, member_id=m.id, role_id=r.id, semester_id=sem_id)
    emit_success({"removed": affected}, mode=mode)


@app.command("add-alias")
def add_alias(
    ctx: typer.Context,
    member: Annotated[str, typer.Argument(help="Member slug, ID, or alias.")],
    alias: Annotated[str, typer.Argument(help="Free-form alias to register.")],
    source: Annotated[str | None, typer.Option("--source")] = None,
) -> None:
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    m = repo.resolve(conn, member)
    if m is None:
        emit_error("member.not_found", f"Could not resolve {member!r}.", mode=mode)
        return
    try:
        with transaction(conn):
            repo.add_alias(conn, member_id=m.id, alias=alias, source=source)
    except sqlite3.IntegrityError:
        emit_error(
            "alias.integrity",
            "This alias is already assigned to another member.",
            mode=mode,
        )
        return
    emit_success({"member": m.slug, "alias": alias}, mode=mode)


@app.command("set-house")
def set_house(
    ctx: typer.Context,
    member: Annotated[str, typer.Argument(help="Member slug, ID, or alias.")],
    house: Annotated[str, typer.Argument(help="House slug (e.g. axid, zta, kd).")],
    semester: Annotated[str | None, typer.Option("--semester")] = None,
) -> None:
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    m = repo.resolve(conn, member)
    h = houses_repo.get_by_slug(conn, house)
    if m is None:
        emit_error("member.not_found", f"Could not resolve {member!r}.", mode=mode)
        return
    if h is None:
        emit_error("house.not_found", f"No house with slug {house!r}.", mode=mode)
        return
    sem_id = _resolve_semester(conn, mode, semester)
    with transaction(conn):
        mha_repo.set_assignment(conn, member_id=m.id, house_id=h.id, semester_id=sem_id)
    assignment = mha_repo.get_for_member_in_semester(conn, member_id=m.id, semester_id=sem_id)
    assert assignment is not None
    emit_success(asdict(assignment), mode=mode)


# Re-export for tests that want to introspect helpers.
__all__ = ["app", "_resolve_semester"]
