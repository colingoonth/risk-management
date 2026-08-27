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
from risk.repos import member_qualifications as mq_repo
from risk.repos import member_roles as mr_repo
from risk.repos import member_shift_preferences as prefs_repo
from risk.repos import member_statuses as statuses_repo
from risk.repos import members as repo
from risk.repos import qualifications as quals_repo
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
    pledge_class: Annotated[
        str | None,
        typer.Option("--pledge-class", help="Pledge class as a Greek letter, e.g. 'Zeta'."),
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
                pledge_class=pledge_class,
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
                ("PC", "pledge_class"),
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
                ("PC", "pledge_class"),
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
        quals = mq_repo.list_for_member_in_semester(conn, member_id=m.id, semester_id=sem_id)
        house = mha_repo.get_for_member_in_semester(conn, member_id=m.id, semester_id=sem_id)
    else:
        roles = mr_repo.list_for_member(conn, m.id)
        quals = mq_repo.list_for_member(conn, m.id)
        house = None
    emit_success(
        {
            "member": asdict(m),
            "aliases": [asdict(a) for a in aliases],
            "roles": [asdict(r) for r in roles],
            "qualifications": [asdict(q) for q in quals],
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


def _resolve_qualification(conn: sqlite3.Connection, mode: Any, slug: str) -> Any:
    """Resolve a qualification slug, listing the valid ones when it misses."""
    q = quals_repo.get_by_slug(conn, slug)
    if q is None:
        known = ", ".join(x.slug for x in quals_repo.list_all(conn)) or "(none seeded)"
        emit_error(
            "qualification.not_found",
            f"No qualification with slug {slug!r}. Known qualifications: {known}.",
            mode=mode,
        )
    return q


@app.command("qualify")
def qualify(
    ctx: typer.Context,
    member: Annotated[str, typer.Argument(help="Member slug, ID, or alias.")],
    qualification: Annotated[str, typer.Argument(help="Qualification slug (e.g. over-21, dj).")],
    semester: Annotated[str | None, typer.Option("--semester")] = None,
) -> None:
    """Grant a qualification to a member for a semester.

    Semester-scoped on purpose: the DJ job changing hands mid-year does not
    rewrite who was qualified last term.

    Example:
        risk member qualify first-last dj
    """
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    m = repo.resolve(conn, member)
    if m is None:
        emit_error("member.not_found", f"Could not resolve {member!r}.", mode=mode)
        return
    q = _resolve_qualification(conn, mode, qualification)
    if q is None:
        return
    sem_id = _resolve_semester(conn, mode, semester)
    with transaction(conn):
        granted = mq_repo.grant(conn, member_id=m.id, qualification_id=q.id, semester_id=sem_id)
    emit_success(
        {
            "member": m.slug,
            "qualification": q.slug,
            "semester_id": sem_id,
            # 0 means the member already held it — a no-op, not a failure.
            "granted": bool(granted),
        },
        mode=mode,
    )


@app.command("unqualify")
def unqualify(
    ctx: typer.Context,
    member: Annotated[str, typer.Argument(help="Member slug, ID, or alias.")],
    qualification: Annotated[str, typer.Argument(help="Qualification slug to remove.")],
    semester: Annotated[str | None, typer.Option("--semester")] = None,
) -> None:
    """Revoke a qualification from a member for a semester."""
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    m = repo.resolve(conn, member)
    if m is None:
        emit_error("member.not_found", f"Could not resolve {member!r}.", mode=mode)
        return
    q = _resolve_qualification(conn, mode, qualification)
    if q is None:
        return
    sem_id = _resolve_semester(conn, mode, semester)
    with transaction(conn):
        removed = mq_repo.revoke(conn, member_id=m.id, qualification_id=q.id, semester_id=sem_id)
    emit_success({"removed": removed}, mode=mode)


@app.command("set-notes")
def set_notes(
    ctx: typer.Context,
    member: Annotated[str, typer.Argument(help="Member slug, ID, or alias.")],
    notes: Annotated[str, typer.Argument(help="Free-text note, e.g. why the member is exempt.")],
    clear: Annotated[
        bool, typer.Option("--clear", help="Clear the note instead of setting it.")
    ] = False,
) -> None:
    """Set the free-text note on a member (the written reason for an exemption)."""
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    m = repo.resolve(conn, member)
    if m is None:
        emit_error("member.not_found", f"Could not resolve {member!r}.", mode=mode)
        return
    with transaction(conn):
        repo.update_notes(conn, member_id=m.id, notes=None if clear else notes)
    updated = repo.get_by_id(conn, m.id)
    assert updated is not None
    emit_success({"member": updated.slug, "notes": updated.notes}, mode=mode)


@app.command("avoid")
def avoid(
    ctx: typer.Context,
    member: Annotated[str, typer.Argument(help="Member slug, ID, or alias.")],
    shift_type: Annotated[str, typer.Argument(help="Shift type slug, e.g. 'driver'.")],
    weekday: Annotated[
        str | None,
        typer.Option("--weekday", help="Limit to one day: mon/tue/wed/thu/fri/sat/sun."),
    ] = None,
    event_type: Annotated[
        str | None, typer.Option("--event-type", help="Limit to one event type, e.g. 'krush'.")
    ] = None,
    hard: Annotated[
        bool, typer.Option("--hard", help="Never assign it, rather than avoid it.")
    ] = False,
    reason: Annotated[str | None, typer.Option("--reason")] = None,
    semester: Annotated[str | None, typer.Option("--semester")] = None,
) -> None:
    """Steer a member off a shift type, optionally only on a day or event type.

    SOFT by default: he sorts behind everyone else for that shift and is taken
    only if the post would otherwise stand empty. That is what "try to avoid"
    means, and it can never leave a slot unstaffed. ``--hard`` removes him.

    One steer per row, so each is removable on its own as somebody's situation
    changes. "No rides, and door on Tuesdays rather than Fridays" is three rows:
    avoid driver; avoid door --weekday fri; avoid door --weekday sat.

    Examples:
        risk member avoid first-last driver --reason "asked not to drive"
        risk member avoid first-last door --weekday fri
        risk member avoid first-last door --event-type krush --hard
    """
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    m = repo.resolve(conn, member)
    if m is None:
        emit_error("member.not_found", f"Could not resolve {member!r}.", mode=mode)
        return
    st = conn.execute("SELECT id FROM shift_types WHERE slug = ?", (shift_type,)).fetchone()
    if st is None:
        emit_error("shift_type.not_found", f"No shift type {shift_type!r}.", mode=mode)
        return
    day_index = None
    if weekday is not None:
        days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        if weekday.lower()[:3] not in days:
            emit_error("member.bad_request", f"Bad weekday {weekday!r}.", mode=mode)
            return
        day_index = days.index(weekday.lower()[:3])
    et_id = None
    if event_type is not None:
        et = conn.execute("SELECT id FROM event_types WHERE slug = ?", (event_type,)).fetchone()
        if et is None:
            emit_error("event_type.not_found", f"No event type {event_type!r}.", mode=mode)
            return
        et_id = et["id"]
    sem_id = _resolve_semester(conn, mode, semester)
    with transaction(conn):
        pref_id = prefs_repo.add(
            conn,
            member_id=m.id,
            semester_id=sem_id,
            shift_type_id=st["id"],
            weekday=day_index,
            event_type_id=et_id,
            is_hard=hard,
            reason=reason,
        )
    added = next(
        p for p in prefs_repo.list_for_semester(conn, semester_id=sem_id) if p.id == pref_id
    )
    emit_success({"id": pref_id, "member": m.slug, "steer": added.describe()}, mode=mode)


@app.command("unavoid")
def unavoid(
    ctx: typer.Context,
    preference_id: Annotated[int, typer.Argument(help="Steer ID, from `member avoids`.")],
) -> None:
    """Drop one steer."""
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    with transaction(conn):
        removed = prefs_repo.remove(conn, preference_id=preference_id)
    emit_success({"removed": removed}, mode=mode)


@app.command("avoids")
def avoids(
    ctx: typer.Context,
    member: Annotated[str | None, typer.Argument(help="Member; omit for the whole chapter.")] = None,
    semester: Annotated[str | None, typer.Option("--semester")] = None,
) -> None:
    """List the per-member steers in force."""
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    member_id = None
    if member is not None:
        m = repo.resolve(conn, member)
        if m is None:
            emit_error("member.not_found", f"Could not resolve {member!r}.", mode=mode)
            return
        member_id = m.id
    sem_id = _resolve_semester(conn, mode, semester)
    rows = prefs_repo.list_for_semester(conn, semester_id=sem_id, member_id=member_id)
    emit_success(
        [
            {"id": p.id, "member": p.member_display_name, "steer": p.describe(), "reason": p.reason}
            for p in rows
        ],
        mode=mode,
    )


@app.command("set-risk-class")
def set_risk_class(
    ctx: typer.Context,
    member: Annotated[str, typer.Argument(help="Member slug, ID, or alias.")],
    year: Annotated[
        int | None,
        typer.Option("--year", help="Graduation year to hold him to for QUOTA purposes."),
    ] = None,
    clear: Annotated[
        bool, typer.Option("--clear", help="Drop the override; go back to his real class year.")
    ] = False,
) -> None:
    """Hold a member to a different class year's QUOTA than his own.

    The roster is not rewritten. ``class_year`` still says what it said, the
    Tally still prints his real class, and only the TARGET he is measured
    against changes — along with his place in the younger-first tiebreak, which
    reads the same effective year so the two cannot disagree.

    It also moves him in the DENOMINATOR. Targets are solved so they sum to
    exactly the work that exists, so a member given a junior target while still
    counted among the sophomores would break that and quietly wrong every
    published percentage. One value, read everywhere.

    Say WHY in `risk member set-notes` — the Tally prints it beside his name,
    and a target that differs from his classmates' with no reason next to it is
    the cell that starts an argument.

    Example:
        risk member set-risk-class first-last --year 2028
        risk member set-risk-class first-last --clear
    """
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    m = repo.resolve(conn, member)
    if m is None:
        emit_error("member.not_found", f"Could not resolve {member!r}.", mode=mode)
        return
    if not clear and year is None:
        emit_error("member.bad_request", "Pass --year YYYY or --clear.", mode=mode)
        return
    with transaction(conn):
        conn.execute(
            "UPDATE members SET risk_class_year = ? WHERE id = ?",
            (None if clear else year, m.id),
        )
    row = conn.execute(
        "SELECT class_year, risk_class_year FROM members WHERE id = ?", (m.id,)
    ).fetchone()
    emit_success(
        {
            "member": m.slug,
            "class_year": row["class_year"],
            "risk_class_year": row["risk_class_year"],
        },
        mode=mode,
    )


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
