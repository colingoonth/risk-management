"""``risk groupme ...`` — seed the chat ids, map the roster, announce the schedule.

**--dry-run is ON by default on every command that could send anything.** You
turn it off with ``--no-dry-run``, and the announce command additionally wants
``--confirm``. Two words rather than one because the two failure modes are
different sizes: a wrong membership change is repairable by re-adding somebody,
and a wrong announcement is eleven @s in front of the chapter that cannot be
unsent.

Ids never appear in this file, in a test, or in shell history if you use
``--stdin``: ``risk groupme seed risk-friday --label "Friday" --stdin`` reads the
id off a pipe. They live in the chair's local database and nowhere else, because
this repository is public.
"""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Annotated

import typer

from risk.cli._common import mode_from_ctx, open_conn
from risk.cli.output import OutputMode, emit_error, emit_success
from risk.db.connection import transaction
from risk.repos import groupme_groups as groups_repo
from risk.repos import groupme_identities as identities_repo
from risk.repos import groupme_outbound as ledger_repo
from risk.repos import members as members_repo
from risk.repos import semesters as semesters_repo
from risk.services import groupme_announce as announce_svc
from risk.services import groupme_confirm as confirm_svc
from risk.services import groupme_identity as identity_svc
from risk.services import groupme_membership as membership_svc
from risk.services import groupme_outbound as outbound_svc
from risk.services.groupme import GroupMeClient, GroupMeError

app = typer.Typer(help="GroupMe: chat ids, identity mapping, and the schedule announcement.")

_WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _resolve_semester(conn: sqlite3.Connection, mode: OutputMode, name: str | None) -> int:
    if name is not None:
        sem = semesters_repo.get_by_name(conn, name)
        if sem is None:
            emit_error("semester.not_found", f"No semester named {name!r}.", mode=mode)
        else:
            return sem.id
    current = semesters_repo.get_current(conn)
    if current is None:
        emit_error(
            "semester.no_current", "No --semester given and no current semester set.", mode=mode
        )
    assert current is not None
    return current.id


def _client() -> GroupMeClient:
    """Built lazily so every read-only command works with no token present."""
    return GroupMeClient()


# ---------------------------------------------------------------------------
# seed
# ---------------------------------------------------------------------------


@app.command("seed")
def seed(
    ctx: typer.Context,
    slug: Annotated[
        str,
        typer.Argument(
            help=(
                "risk-parent | risk-tuesday | risk-friday | risk-saturday | "
                "setup-cleanup | roster-source"
            )
        ),
    ],
    label: Annotated[str, typer.Option("--label", help="Human name for this chat.")],
    groupme_id: Annotated[
        str | None,
        typer.Option("--groupme-id", help="The GroupMe id. Prefer --stdin to keep it out of history."),
    ] = None,
    read_stdin: Annotated[
        bool, typer.Option("--stdin", help="Read the GroupMe id from stdin instead.")
    ] = False,
    parent: Annotated[
        str | None,
        typer.Option("--parent", help="Parent slug for a day topic. Omit for a top-level group."),
    ] = None,
    weekday: Annotated[
        int | None, typer.Option("--weekday", help="1=Mon .. 7=Sun, for a day topic.")
    ] = None,
) -> None:
    """Register one chat id under a slug.

    Example:
        printf '%s' "$ID" | risk groupme seed risk-friday --label "Risk · Friday" \\
            --parent risk-parent --weekday 5 --stdin
    """
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    if read_stdin:
        groupme_id = sys.stdin.read().strip()
    if not groupme_id:
        emit_error(
            "groupme.missing_id",
            "Give the id with --groupme-id, or pipe it in with --stdin.",
            mode=mode,
        )
        return
    try:
        with transaction(conn):
            groups_repo.upsert(
                conn,
                slug=slug,
                groupme_id=groupme_id,
                label=label,
                parent_slug=parent,
                weekday=weekday,
            )
    except ValueError as exc:
        emit_error("groupme.invalid_group", str(exc), mode=mode)
        return
    stored = groups_repo.get_by_slug(conn, slug)
    assert stored is not None
    # The id itself is NOT echoed — the point of --stdin is that it never lands
    # anywhere a person can scroll back to.
    emit_success(
        {
            "slug": stored.slug,
            "label": stored.label,
            "parent_slug": stored.parent_slug,
            "weekday": stored.weekday,
            "groupme_id_set": True,
        },
        mode=mode,
    )


@app.command("groups")
def list_groups(ctx: typer.Context) -> None:
    """Show which chats are registered, without printing their ids."""
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    rows = groups_repo.list_all(conn)
    emit_success(
        {
            "groups": [
                {
                    "slug": g.slug,
                    "label": g.label,
                    "parent_slug": g.parent_slug,
                    "weekday": None if g.weekday is None else _WEEKDAY_NAMES[g.weekday - 1],
                }
                for g in rows
            ]
        },
        mode=mode,
    )


# ---------------------------------------------------------------------------
# map
# ---------------------------------------------------------------------------


@app.command("map")
def map_identities(
    ctx: typer.Context,
    source: Annotated[
        str,
        typer.Option("--source", help="Slug of the chat to read the roster from."),
    ] = groups_repo.ROSTER_SOURCE_SLUG,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run/--no-dry-run", help="Default ON: show the plan, write nothing."),
    ] = True,
    confirm_pair: Annotated[
        list[str] | None,
        typer.Option(
            "--confirm",
            help="MEMBER=GROUPME_USER_ID. Records a link a HUMAN decided. Repeatable.",
        ),
    ] = None,
) -> None:
    """Match the roster to GroupMe accounts.

    Only exact display-name and registered-alias matches are applied. Anything
    that merely LOOKS like a person is listed under `ambiguous` with its
    candidates and waits for you to run `--confirm slug=user_id`.

    Example:
        risk groupme map
        risk groupme map --no-dry-run
        risk groupme map --confirm test-alpha=<groupme-user-id> --no-dry-run
    """
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)

    if confirm_pair:
        _apply_confirmations(conn, mode, confirm_pair, dry_run=dry_run, source=source)
        return

    group = groups_repo.get_by_slug(conn, source)
    if group is None:
        emit_error(
            "groupme.group_not_found",
            f"No chat registered as {source!r}. Seed it with `risk groupme seed`.",
            mode=mode,
        )
        return
    try:
        accounts = _client().list_members(group.groupme_id)
    except GroupMeError as exc:
        emit_error("groupme.api", str(exc), mode=mode)
        return

    plan = identity_svc.plan_for_roster(conn, accounts)
    applied = 0
    if not dry_run:
        with transaction(conn):
            # Exact and alias equalities only. Ambiguities wait for --confirm,
            # and a renamed account is never re-catalogued on our own say-so.
            applied = identity_svc.apply_proposals(conn, plan.proposals)

    emit_success(
        {
            "dry_run": dry_run,
            "applied": applied,
            "proposals": [asdict(p) for p in plan.proposals],
            "ambiguous": [asdict(a) for a in plan.ambiguous],
            "unmatched_members": [asdict(c) for c in plan.unmatched_members],
            "unmatched_nicknames": [
                {"groupme_user_id": uid, "nickname": nick}
                for uid, nick in plan.unmatched_nicknames
            ],
            "drift": [asdict(d) for d in plan.drift],
        },
        mode=mode,
    )


def _apply_confirmations(
    conn: sqlite3.Connection, mode: OutputMode, pairs: list[str], *, dry_run: bool, source: str
) -> None:
    """``--confirm slug=user_id``: the human-decided path into the identity table.

    The nickname is READ BACK FROM THE SOURCE CHAT rather than left null. A link
    with no nickname satisfies the identity table but is unusable downstream: a
    mention has to slice to ``@`` + the CURRENT nickname to pass verification, so
    a null one is blocked as ``no-nickname`` and the man is silently printed
    without a tag. That is a schedule that looks published and tags nobody.

    Confirming an id that is not in the source chat is refused rather than
    stored, because the only evidence that the id is the person the chair meant
    is that it belongs to an account they can actually see.
    """
    # Argument validation FIRST, before any network call: a typo should cost
    # nothing, and a malformed pair must not depend on a chat being reachable.
    wanted: list[tuple[int, str, str]] = []
    for pair in pairs:
        member_key, _, user_id = pair.partition("=")
        if not user_id:
            emit_error(
                "groupme.bad_confirm", f"Expected MEMBER=USER_ID, got {pair!r}.", mode=mode
            )
            return
        member = members_repo.resolve(conn, member_key.strip())
        if member is None:
            emit_error("member.not_found", f"No member matching {member_key!r}.", mode=mode)
            return
        wanted.append((member.id, member.display_name, user_id.strip()))

    # The nickname is only needed to WRITE, so a dry run stays offline — every
    # read-only path here works with no token present, and that is worth keeping.
    nick_by_id: dict[str, str] = {}
    if not dry_run:
        group = groups_repo.get_by_slug(conn, source)
        if group is None:
            emit_error(
                "groupme.group_not_found",
                f"No chat registered as {source!r}, so a nickname cannot be read back. "
                "Seed it with `risk groupme seed`.",
                mode=mode,
            )
            return
        try:
            nick_by_id = {a.user_id: a.nickname for a in _client().list_members(group.groupme_id)}
        except GroupMeError as exc:
            emit_error("groupme.api", str(exc), mode=mode)
            return

    resolved: list[tuple[int, str, str]] = []
    for member_id, display_name, uid in wanted:
        if not dry_run and uid not in nick_by_id:
            emit_error(
                "groupme.account_not_in_source",
                f"No account with that id is in {source!r}, so there is nothing to "
                f"confirm {display_name} against.",
                mode=mode,
            )
            return
        resolved.append((member_id, display_name, uid))

    if not dry_run:
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        try:
            with transaction(conn):
                for member_id, _, user_id in resolved:
                    identity_svc.confirm_link(
                        conn,
                        member_id=member_id,
                        groupme_user_id=user_id,
                        nickname=nick_by_id[user_id],
                        linked_at=stamp,
                    )
        except sqlite3.IntegrityError:
            emit_error(
                "groupme.already_linked",
                "That GroupMe account is already linked to a different member. "
                "Unlink it first — moving an account silently rewrites two people's history.",
                mode=mode,
            )
            return

    emit_success(
        {
            "dry_run": dry_run,
            "confirmed": [
                {"member_id": mid, "display_name": name, "groupme_user_id": uid}
                for mid, name, uid in resolved
            ],
        },
        mode=mode,
    )


@app.command("identities")
def list_identities(ctx: typer.Context) -> None:
    """Who is linked, who is not."""
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    linked = identities_repo.list_linked(conn)
    unlinked = identities_repo.list_unlinked_members(conn)
    emit_success(
        {
            "linked": [
                {
                    "member_id": link.member_id,
                    "display_name": link.display_name,
                    "nickname": link.nickname,
                    "confidence": link.confidence,
                }
                for link in linked
            ],
            "unlinked": [{"member_id": mid, "display_name": name} for mid, name in unlinked],
        },
        mode=mode,
    )


# ---------------------------------------------------------------------------
# announce
# ---------------------------------------------------------------------------


@app.command("announce")
def announce(
    ctx: typer.Context,
    on_or_after: Annotated[str, typer.Option("--from", help="First event date (YYYY-MM-DD).")],
    on_or_before: Annotated[str, typer.Option("--to", help="Last event date (YYYY-MM-DD).")],
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run/--no-dry-run", help="Default ON: print the posts, send nothing."),
    ] = True,
    confirm: Annotated[
        bool,
        typer.Option("--confirm", help="Required, with --no-dry-run, to actually post."),
    ] = False,
    semester: Annotated[
        str | None, typer.Option("--semester", help="Defaults to the current semester.")
    ] = None,
) -> None:
    """Build one post per event and, if you insist twice, send them.

    Example:
        risk groupme announce --from 2026-09-01 --to 2026-09-14
        risk groupme announce --from 2026-09-01 --to 2026-09-14 --no-dry-run --confirm
    """
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    if not dry_run and not confirm:
        # Checked BEFORE anything reaches the network. Otherwise a missing
        # --confirm surfaces as whatever the API happened to say first, and the
        # operator is told the token is wrong when the real answer is "you left
        # a flag off".
        emit_error(
            "groupme.unconfirmed",
            "--no-dry-run also needs --confirm. Nothing was sent.",
            mode=mode,
        )
        return
    semester_id = _resolve_semester(conn, mode, semester)
    present = None
    parent = groups_repo.get_by_slug(conn, groups_repo.PARENT_SLUG)
    if parent is not None and not dry_run:
        # A real send checks drift and departures against the live group; a dry
        # run does not need the network and says which check it made.
        try:
            present = _client().list_members(parent.groupme_id)
        except GroupMeError as exc:
            emit_error("groupme.api", str(exc), mode=mode)
            return
    plan = announce_svc.build_plan(
        conn,
        semester_id=semester_id,
        on_or_after=on_or_after,
        on_or_before=on_or_before,
        present=present,
    )

    payload = {
        "dry_run": dry_run,
        "posted": [],
        "identity_check": plan.identity_check,
        "oversize": [
            {
                "event_date": p.event_date,
                "event_name": p.event_name,
                "char_count": p.char_count,
            }
            for p in plan.oversize
        ],
        "posts": [
            {
                "group_slug": p.group_slug,
                "label": p.label,
                "event_date": p.event_date,
                "event_name": p.event_name,
                "text": p.text,
                "mentions": [asdict(m) for m in p.mentions],
                "unlinked": [asdict(u) for u in p.unlinked],
                "char_count": p.char_count,
            }
            for p in plan.posts
        ],
        "unroutable": [asdict(u) for u in plan.unroutable],
    }

    if dry_run:
        emit_success(payload, mode=mode)
        return
    try:
        confirm_svc.check_volume(
            post_count=len(plan.posts), on_or_after=on_or_after, on_or_before=on_or_before
        )
    except ValueError as exc:
        emit_error("groupme.too_many", str(exc), mode=mode)
        return
    try:
        sent = outbound_svc.post_announcements(conn, _client(), plan.posts, confirm=True)
    except GroupMeError as exc:
        emit_error("groupme.api", str(exc), mode=mode)
        return
    except ValueError as exc:
        emit_error("groupme.refused", str(exc), mode=mode)
        return
    payload["posted"] = [asdict(s) for s in sent]
    emit_success(payload, mode=mode)


# ---------------------------------------------------------------------------
# membership
# ---------------------------------------------------------------------------


@app.command("membership")
def membership(
    ctx: typer.Context,
    on_or_after: Annotated[str, typer.Option("--from", help="First event date (YYYY-MM-DD).")],
    on_or_before: Annotated[str, typer.Option("--to", help="Last event date (YYYY-MM-DD).")],
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run/--no-dry-run", help="Default ON: show the plan, change nothing."),
    ] = True,
    confirm: Annotated[
        bool, typer.Option("--confirm", help="Required, with --no-dry-run, to actually apply.")
    ] = False,
    semester: Annotated[
        str | None, typer.Option("--semester", help="Defaults to the current semester.")
    ] = None,
) -> None:
    """Who to add to the Risk group, and who has finished every shift in the window."""
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    if not dry_run and not confirm:
        emit_error(
            "groupme.unconfirmed",
            "--no-dry-run also needs --confirm. Nothing was changed.",
            mode=mode,
        )
        return
    semester_id = _resolve_semester(conn, mode, semester)
    parent = groups_repo.get_by_slug(conn, groups_repo.PARENT_SLUG)
    if parent is None:
        emit_error(
            "groupme.group_not_found",
            f"No chat registered as {groups_repo.PARENT_SLUG!r}. Seed it first.",
            mode=mode,
        )
        return
    client = _client()
    try:
        present = client.list_members(parent.groupme_id)
    except GroupMeError as exc:
        emit_error("groupme.api", str(exc), mode=mode)
        return

    plan = membership_svc.build_plan(
        conn,
        semester_id=semester_id,
        on_or_after=on_or_after,
        on_or_before=on_or_before,
        present=present,
    )
    payload = {
        "dry_run": dry_run,
        "add": [asdict(a) for a in plan.add],
        "remove": [asdict(r) for r in plan.remove],
        "unrecognised": [asdict(u) for u in plan.unrecognised],
        "blocked": [asdict(b) for b in plan.blocked],
        "hard_excluded": [asdict(e) for e in plan.hard_excluded],
        "permanent": [asdict(member) for member in plan.permanent],
        "unlinked_workers": [
            {"member_id": mid, "display_name": name} for mid, name in plan.unlinked_workers
        ],
        "applied": False,
    }
    if dry_run:
        emit_success(payload, mode=mode)
        return
    try:
        outbound_svc.apply_membership(conn, client, plan, confirm=True)
    except GroupMeError as exc:
        emit_error("groupme.api", str(exc), mode=mode)
        return
    payload["applied"] = True
    emit_success(payload, mode=mode)


# ---------------------------------------------------------------------------
# reconcile
# ---------------------------------------------------------------------------


@app.command("outbound")
def outbound(ctx: typer.Context) -> None:
    """Show the send ledger's unsettled rows — anything awaiting reconciliation."""
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    rows = ledger_repo.list_unsettled(conn)
    emit_success(
        {
            "unsettled": [
                {
                    "event_id": r.event_id,
                    "destination_slug": r.destination_slug,
                    "state": r.state,
                    "attempts": r.attempts,
                    "reserved_at": r.reserved_at,
                    "last_error": r.last_error,
                }
                for r in rows
            ]
        },
        mode=mode,
    )


@app.command("reconcile")
def reconcile(
    ctx: typer.Context,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run/--no-dry-run", help="Default ON: show what would be settled."),
    ] = True,
) -> None:
    """Settle `unknown` sends by reading the topic back.

    An ambiguous failure — a timeout, a reset connection — leaves a row saying
    "this may or may not have posted", and NOTHING retries it on its own. This
    command answers the question with evidence: GroupMe echoes the source_guid on
    every message it stored, so the guid is either in the topic or it is not.

    Example:
        risk groupme reconcile
        risk groupme reconcile --no-dry-run
    """
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    pending = ledger_repo.list_unsettled(conn)
    if dry_run:
        emit_success(
            {
                "dry_run": True,
                "would_check": [
                    {
                        "event_id": r.event_id,
                        "destination_slug": r.destination_slug,
                        "state": r.state,
                        "reserved_at": r.reserved_at,
                    }
                    for r in pending
                ],
            },
            mode=mode,
        )
        return
    try:
        resolved = outbound_svc.reconcile(conn, _client())
    except GroupMeError as exc:
        emit_error("groupme.api", str(exc), mode=mode)
        return
    emit_success(
        {"dry_run": False, "resolved": [asdict(r) for r in resolved]},
        mode=mode,
    )


@app.command("confirm-rename")
def confirm_rename(
    ctx: typer.Context,
    member: Annotated[str, typer.Argument(help="Member slug, id or alias.")],
    nickname: Annotated[str, typer.Argument(help="The nickname GroupMe shows now.")],
    dry_run: Annotated[
        bool, typer.Option("--dry-run/--no-dry-run", help="Default ON: change nothing.")
    ] = True,
) -> None:
    """Accept that a linked account renamed itself — as YOUR decision.

    A renamed account is blocked from every write until this runs, and there is
    no bulk "accept all drift". The rename is evidence that the name the link was
    made on has moved, not evidence about who the account belongs to, so somebody
    has to look. Re-confirming stores the link as `confirmed`.
    """
    mode = mode_from_ctx(ctx)
    conn = open_conn(ctx)
    found = members_repo.resolve(conn, member)
    if found is None:
        emit_error("member.not_found", f"No member matching {member!r}.", mode=mode)
        return
    if dry_run:
        emit_success(
            {
                "dry_run": True,
                "member_id": found.id,
                "display_name": found.display_name,
                "new_nickname": nickname,
            },
            mode=mode,
        )
        return
    try:
        with transaction(conn):
            identity_svc.confirm_rename(conn, member_id=found.id, new_nickname=nickname)
    except (LookupError, ValueError) as exc:
        emit_error("groupme.no_link", str(exc), mode=mode)
        return
    emit_success(
        {
            "dry_run": False,
            "member_id": found.id,
            "display_name": found.display_name,
            "new_nickname": nickname,
            "confidence": "confirmed",
        },
        mode=mode,
    )
