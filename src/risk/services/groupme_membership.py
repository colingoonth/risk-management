"""Who should be in the Risk group right now, and who is done.

Membership lives on the PARENT group. Topics have none of their own — an add
aimed at a topic id 404s — so every add and remove in here targets the parent
and the day topics are read-only surfaces on top of it.

REMOVAL TIMING IS THE WHOLE POINT. "Remove people when their shifts are done"
does not mean "after the party". Cleanup is worked the NEXT MORNING and setup is
worked in the days BEFORE, so the honest end of somebody's involvement is the
end of his last SHIFT WINDOW, taken from ``shift_type_windows`` — event date plus
``offset_days_end``, at ``window_end_time``. For cleanup that is noon the
following day.

Using the event date instead kicks the cleanup crew out of the group at midnight,
hours before they are due to show up, and the first they hear of it is not being
able to read the chat that tells them when. That failure is silent, it happens to
the people doing the least popular job, and it is one subtraction away — hence
this module rather than a date comparison at each call site.

THE MEMBERSHIP HORIZON IS FORWARD-LOOKING AND BOUNDED. A scheduled man belongs
in the group when at least one shift has not genuinely ended and its PARTY date
is no later than the rolling horizon's end. There is deliberately no lower
party-date bound: yesterday's party can still have cleanup working this morning.
There is deliberately an upper bound: a shift three weeks away is not a reason
to keep somebody in the group today.

NOBODY WHOSE IDENTITY IS IN DOUBT IS EVER PROPOSED FOR REMOVAL. A link that has
drifted (the account renamed itself), collides with another link's nickname, or
carries no nickname at all is blocked — see ``services.groupme_identity``. A
GroupMe user id is stable and the human behind it is asserted by a name that is
not, so "we are no longer sure who this is" and "remove this person from the
group" must never be the same code path. Those links are reported and skipped.

NOBODY UNRECOGNISED IS EVER PROPOSED FOR REMOVAL. A GroupMe account in the group
that maps to no roster member is the chair, an exec, an alumnus helping out, or
someone whose identity mapping is still waiting on a human. Removing on a
mismatch would make an unmapped brother disappear from the group, which is
exactly the outcome the identity service refuses to risk.

NOBODY STRUCTURALLY EXEMPT FROM ASSIGNMENT IS EVER PROPOSED FOR REMOVAL. A
member holding a role marked both default-excluded and not soft-excluded cannot
normally have a shift in the window, so that absence says nothing about whether
he belongs in the parent group. Those members are reported separately with the
role that protected them; soft exclusions do not receive this protection.

NOBODY DECLARED PERMANENT FOR THIS NAMED GROUP IS EVER PROPOSED FOR REMOVAL.
Permanence is not a role and does not affect assignment eligibility: it only
says that absence from the selected group's shift window is not evidence that
the member should leave that chat. Permanent members are reported as their own
visible skip category.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date as _date
from datetime import datetime, time, timedelta

from risk.repos import events as events_repo
from risk.repos import groupme_groups as groups_repo
from risk.repos import groupme_identities as identities_repo
from risk.repos import groupme_permanent_members as permanent_repo
from risk.repos import member_roles as member_roles_repo
from risk.repos import roles as roles_repo
from risk.repos import shift_type_windows as windows_repo
from risk.repos import shifts as shifts_repo
from risk.services import groupme_identity as identity_svc
from risk.services.groupme import GroupMeMember


@dataclass(frozen=True, slots=True)
class MembershipAdd:
    member_id: int
    display_name: str
    groupme_user_id: str
    nickname: str | None


@dataclass(frozen=True, slots=True)
class MembershipRemoval:
    member_id: int
    display_name: str
    membership_id: str
    groupme_user_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class UnrecognisedPresence:
    """In the group, mapped to nobody. Reported, never removed."""

    groupme_user_id: str
    membership_id: str
    nickname: str


@dataclass(frozen=True, slots=True)
class BlockedIdentity:
    """A linked member skipped because we are not certain who he is."""

    member_id: int
    display_name: str
    groupme_user_id: str
    reason: str
    detail: str


@dataclass(frozen=True, slots=True)
class HardExcludedMember:
    """A linked member kept because his role structurally has no shifts."""

    member_id: int
    display_name: str
    groupme_user_id: str
    reason: str
    detail: str


@dataclass(frozen=True, slots=True)
class PermanentMember:
    """A linked member kept because this named group always includes him."""

    member_id: int
    display_name: str
    groupme_user_id: str
    reason: str
    detail: str


@dataclass(frozen=True, slots=True)
class MembershipPlan:
    add: tuple[MembershipAdd, ...]
    remove: tuple[MembershipRemoval, ...]
    unrecognised: tuple[UnrecognisedPresence, ...]
    blocked: tuple[BlockedIdentity, ...]
    hard_excluded: tuple[HardExcludedMember, ...]
    unlinked_workers: tuple[tuple[int, str], ...]
    """``(member_id, display_name)`` for people needed in the group who have no
    GroupMe link — they cannot be added, and the chair has to be told rather than
    left to notice."""
    permanent: tuple[PermanentMember, ...] = ()


def shift_window_end(
    conn: sqlite3.Connection, *, shift_type_slug: str, event_date: str
) -> datetime:
    """When this shift is genuinely finished.

    Raises ``LookupError`` when the shift type has no window row, matching
    ``services.availability``: guessing "it ended at midnight" here is the exact
    mistake this module exists to prevent.
    """
    window = windows_repo.get_by_slug(conn, shift_type_slug)
    if window is None:
        raise LookupError(
            f"shift type {shift_type_slug!r} has no row in shift_type_windows; "
            "cannot tell when it finishes"
        )
    day = _date.fromisoformat(event_date) + timedelta(days=window.offset_days_end)
    return datetime.combine(day, time.fromisoformat(window.window_end_time))


def last_shift_end_by_member(
    conn: sqlite3.Connection,
    *,
    semester_id: int,
    on_or_after: str | None,
    on_or_before: str | None,
) -> dict[int, datetime]:
    """For each assigned member, when their LAST shift finishes.

    The max, not the min and not the event date: a man on setup Wednesday and
    cleanup Sunday is involved until Monday noon, and any earlier answer removes
    him mid-commitment.

    Bounds are optional PARTY-date bounds. Membership deliberately omits the
    lower bound so a next-morning cleanup remains visible after its party has
    rolled behind today, while applying the upper bound so distant assignments
    do not keep the whole term's roster in the group.
    """
    dates_by_event: dict[int, str] = {}
    for event in events_repo.list_for_semester(conn, semester_id):
        if event.status == "cancelled":
            continue
        if on_or_after is not None and event.date < on_or_after:
            continue
        if on_or_before is not None and event.date > on_or_before:
            continue
        dates_by_event[event.id] = event.date

    ends: dict[int, datetime] = {}
    for shift in shifts_repo.list_filtered(conn, semester_id=semester_id):
        if shift.assigned_member_id is None:
            continue
        event_date = dates_by_event.get(shift.event_id)
        if event_date is None:
            continue
        end = shift_window_end(
            conn, shift_type_slug=shift.shift_type_slug, event_date=event_date
        )
        current = ends.get(shift.assigned_member_id)
        if current is None or end > current:
            ends[shift.assigned_member_id] = end
    return ends


def build_plan(
    conn: sqlite3.Connection,
    *,
    semester_id: int,
    on_or_after: str,
    on_or_before: str,
    present: list[GroupMeMember],
    group_slug: str = groups_repo.PARENT_SLUG,
    now: datetime | None = None,
) -> MembershipPlan:
    """Reconcile one named group's membership against the window's schedule.

    ``present`` is the live named-group member list; ``now`` is injectable so
    the removal boundary is testable to the minute instead of only at whatever
    time the suite happens to run. A permanent declaration is scoped by
    ``group_slug``; it neither protects the member in another group nor removes
    him from the assignment pool.
    """
    moment = now or datetime.now()
    # Party dates are capped at the forward horizon, but not at its lower edge:
    # yesterday's cleanup can still be unfinished this morning. The strict
    # `end > moment` checks below make the shift-window boundary authoritative.
    horizon_ends = last_shift_end_by_member(
        conn,
        semester_id=semester_id,
        on_or_after=None,
        on_or_before=on_or_before,
    )
    # Whole-term ends explain why somebody is removable; they do not protect a
    # distant worker from rolling turnover.
    ends_anywhere = last_shift_end_by_member(
        conn, semester_id=semester_id, on_or_after=None, on_or_before=None
    )

    blocks = identity_svc.blocked_identities(conn, present=present)
    # "Not in the parent group" is the whole REASON for an add, so it cannot also
    # disqualify one. The other three reasons all mean "we do not know which
    # brother this account is", and those disqualify every write.
    unsafe = {
        user_id: block
        for user_id, block in blocks.items()
        if block.reason != identity_svc.BLOCK_NOT_IN_GROUP
    }

    identities = identities_repo.list_linked(conn)
    by_member = {i.member_id: i for i in identities}
    by_user_id = {i.groupme_user_id: i for i in identities}
    hard_roles_by_member = _hard_excluded_roles_by_member(conn, semester_id)
    permanent_rows = permanent_repo.list_for_group(conn, group_slug)
    permanent_member_ids = frozenset(row.member_id for row in permanent_rows)

    # Two accounts can report the same user_id only if GroupMe repeats itself;
    # keep the first membership row so a removal targets something real.
    present_by_user: dict[str, GroupMeMember] = {}
    for account in present:
        present_by_user.setdefault(account.user_id, account)

    adds: list[MembershipAdd] = []
    blocked: list[BlockedIdentity] = []
    unlinked_workers: list[tuple[int, str]] = []
    display_names = _display_names(conn, semester_id)
    display_names.update({row.member_id: row.display_name for row in permanent_rows})

    needed_member_ids = set(horizon_ends) | set(permanent_member_ids)
    for member_id in needed_member_ids:
        end = horizon_ends.get(member_id)
        if member_id not in permanent_member_ids and end is not None and end <= moment:
            # Already finished everything in the window — adding him now would
            # only be followed by removing him.
            continue
        identity = by_member.get(member_id)
        if identity is None:
            unlinked_workers.append((member_id, display_names.get(member_id, "")))
            continue
        block = unsafe.get(identity.groupme_user_id)
        if block is not None:
            blocked.append(_as_blocked(block))
            continue
        if identity.groupme_user_id in present_by_user:
            continue
        adds.append(
            MembershipAdd(
                member_id=member_id,
                display_name=identity.display_name,
                groupme_user_id=identity.groupme_user_id,
                nickname=identity.nickname,
            )
        )

    removes: list[MembershipRemoval] = []
    unrecognised: list[UnrecognisedPresence] = []
    hard_excluded: list[HardExcludedMember] = []
    permanent: list[PermanentMember] = []
    seen_blocked = {b.groupme_user_id for b in blocked}
    for user_id, account in present_by_user.items():
        identity = by_user_id.get(user_id)
        if identity is None:
            unrecognised.append(
                UnrecognisedPresence(
                    groupme_user_id=user_id,
                    membership_id=account.membership_id,
                    nickname=account.nickname,
                )
            )
            continue
        block = blocks.get(user_id)
        if block is not None:
            # Removal is the irreversible half of this plan and it acts on a
            # named human. Not knowing which human is a hard stop.
            if user_id not in seen_blocked:
                blocked.append(_as_blocked(block))
                seen_blocked.add(user_id)
            continue
        if identity.member_id in permanent_member_ids:
            permanent.append(
                PermanentMember(
                    member_id=identity.member_id,
                    display_name=identity.display_name,
                    groupme_user_id=user_id,
                    reason="permanent member",
                    detail=f"permanent in group {group_slug!r}",
                )
            )
            continue
        hard_roles = hard_roles_by_member.get(identity.member_id)
        if hard_roles:
            role_list = ", ".join(hard_roles)
            verb = "is" if len(hard_roles) == 1 else "are"
            hard_excluded.append(
                HardExcludedMember(
                    member_id=identity.member_id,
                    display_name=identity.display_name,
                    groupme_user_id=user_id,
                    reason="hard-excluded role",
                    detail=f"{role_list} {verb} hard-excluded from assignment",
                )
            )
            continue
        horizon_end = horizon_ends.get(identity.member_id)
        if horizon_end is not None and horizon_end > moment:
            continue
        last_end = ends_anywhere.get(identity.member_id)
        if last_end is None:
            reason = "no unfinished shift"
        elif last_end <= moment:
            reason = f"all shifts finished {last_end.isoformat(sep=' ', timespec='minutes')}"
        else:
            reason = f"next shift is after membership horizon {on_or_before}"
        removes.append(
            MembershipRemoval(
                member_id=identity.member_id,
                display_name=identity.display_name,
                membership_id=account.membership_id,
                groupme_user_id=user_id,
                reason=reason,
            )
        )

    return MembershipPlan(
        add=tuple(sorted(adds, key=lambda a: (a.display_name, a.member_id))),
        remove=tuple(sorted(removes, key=lambda r: (r.display_name, r.member_id))),
        unrecognised=tuple(sorted(unrecognised, key=lambda u: u.nickname)),
        blocked=tuple(sorted(blocked, key=lambda b: (b.display_name, b.groupme_user_id))),
        hard_excluded=tuple(
            sorted(hard_excluded, key=lambda e: (e.display_name, e.groupme_user_id))
        ),
        unlinked_workers=tuple(sorted(unlinked_workers, key=lambda p: p[1])),
        permanent=tuple(sorted(permanent, key=lambda p: (p.display_name, p.groupme_user_id))),
    )


def _as_blocked(block: identity_svc.Block) -> BlockedIdentity:
    return BlockedIdentity(
        member_id=block.member_id,
        display_name=block.display_name,
        groupme_user_id=block.groupme_user_id,
        reason=block.reason,
        detail=block.detail,
    )


def _display_names(conn: sqlite3.Connection, semester_id: int) -> dict[int, str]:
    """Display names for everyone holding a shift this semester."""
    rows = conn.execute(
        """
        SELECT DISTINCT m.id, m.display_name
        FROM shifts s
        JOIN events e ON e.id = s.event_id
        JOIN members m ON m.id = s.assigned_member_id
        WHERE e.semester_id = ?
        """,
        (semester_id,),
    ).fetchall()
    return {int(r["id"]): str(r["display_name"]) for r in rows}


def _hard_excluded_roles_by_member(
    conn: sqlite3.Connection, semester_id: int
) -> dict[int, tuple[str, ...]]:
    """Hard-excluded role names by member, using the canonical role flags."""
    hard_role_names = {
        role.slug: role.display_name
        for role in roles_repo.list_all(conn)
        if role.default_excluded_from_assignment and not role.exclude_is_soft
    }
    names_by_member: dict[int, list[str]] = {}
    for held_role in member_roles_repo.list_for_semester(conn, semester_id):
        role_name = hard_role_names.get(held_role.role_slug)
        if role_name is not None:
            names_by_member.setdefault(held_role.member_id, []).append(role_name)
    return {
        member_id: tuple(sorted(role_names))
        for member_id, role_names in names_by_member.items()
    }


def summarise(plan: MembershipPlan) -> dict[str, int]:
    """Counts, for a confirmation prompt that has to fit on one line."""
    return {
        "add": len(plan.add),
        "remove": len(plan.remove),
        "unrecognised": len(plan.unrecognised),
        "blocked": len(plan.blocked),
        "hard_excluded": len(plan.hard_excluded),
        "unlinked_workers": len(plan.unlinked_workers),
        "permanent": len(plan.permanent),
    }
