"""GroupMe: identities, membership, and the announcement.

Every route that can change something in GroupMe is a POST with a ``confirm``
field that defaults to FALSE and is REQUIRED to be true. The GET routes are the
same computation with the sending removed, so the dashboard can show the chair
exactly what would be sent before he decides — the preview and the post are the
same plan object, not two implementations that agree until they don't.

The GroupMe client is a FastAPI dependency (``get_groupme_client``) so tests
override it with a stub. Nothing in the test suite touches the live chapter chat,
and the override point is one function rather than a monkeypatch per test.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from risk.api.deps import get_conn
from risk.api.errors import service_errors
from risk.api.routers._util import resolve_semester
from risk.api.schemas import (
    GroupMeAnnouncePostOut,
    GroupMeAnnouncePreviewOut,
    GroupMeAnnounceResultOut,
    GroupMeBlockedOut,
    GroupMeConfirmIn,
    GroupMeDriftOut,
    GroupMeHardExcludedOut,
    GroupMeIdentitiesOut,
    GroupMeIdentityRowOut,
    GroupMeMembershipAddOut,
    GroupMeMembershipApplyOut,
    GroupMeMembershipPlanOut,
    GroupMeMembershipRemoveOut,
    GroupMeMentionOut,
    GroupMeOkOut,
    GroupMePermanentOut,
    GroupMePostedOut,
    GroupMeReplyIn,
    GroupMeUnlinkedOut,
    GroupMeUnrecognisedOut,
    GroupMeUnroutableOut,
)
from risk.db.connection import transaction
from risk.repos import groupme_groups as groups_repo
from risk.repos import groupme_identities as identities_repo
from risk.services import groupme_announce as announce_svc
from risk.services import groupme_confirm as confirm_svc
from risk.services import groupme_identity as identity_svc
from risk.services import groupme_membership as membership_svc
from risk.services import groupme_outbound as outbound_svc
from risk.services.groupme import GroupMeClient, GroupMeError

router = APIRouter(prefix="/groupme", tags=["groupme"])


def get_groupme_client() -> GroupMeClient:
    """Construct the API client. Overridden wholesale in tests."""
    return GroupMeClient()


def _require_confirm(confirm: bool, what: str) -> None:
    """The boolean half of the gate. Necessary, and nowhere near sufficient —
    see :func:`_require_preview` and ``services.groupme_confirm``."""
    if not confirm:
        raise ValueError(f"refusing to {what} without confirm=true")


def _require_preview(body: GroupMeConfirmIn, current_digest: str) -> None:
    """The half that actually means something.

    Recomputes nothing itself — the caller has already rebuilt the plan from
    live data — and compares that plan's digest against the one the chair was
    looking at. Raises ``ValueError`` (→ 400) on drift or expiry, which is the
    right status: the request is not wrong about the server, it is wrong about
    the world.
    """
    confirm_svc.verify(
        preview_id=body.preview_id,
        submitted_digest=body.digest,
        current_digest=current_digest,
    )


def _parent(conn: sqlite3.Connection) -> groups_repo.GroupMeGroup:
    parent = groups_repo.get_by_slug(conn, groups_repo.PARENT_SLUG)
    if parent is None:
        raise LookupError(
            f"no GroupMe group registered as {groups_repo.PARENT_SLUG!r} — "
            "seed it with `risk groupme seed`"
        )
    return parent


# ---------------------------------------------------------------------------
# Identities
# ---------------------------------------------------------------------------


@router.get("/identities", response_model=GroupMeIdentitiesOut)
def identities(
    conn: sqlite3.Connection = Depends(get_conn),
    client: GroupMeClient = Depends(get_groupme_client),
) -> GroupMeIdentitiesOut:
    """Who is mapped, who is not, and whose link has stopped being usable.

    ``drift`` is the subset of ``blocked`` a chair acts on most often — an
    account that renamed itself — and it is reported rather than absorbed. The
    nickname is NOT refreshed here or anywhere automatic: a rename is evidence
    that the thing the link was made on has moved, and quietly re-cataloguing it
    is how a stable user id ends up permanently attached to the wrong brother.

    Blocks are computed against the live parent group when it is reachable and
    from the database alone when it is not. A GroupMe outage must not 500 this
    page — the linked/unlinked halves are database facts and stay useful offline.
    """
    linked = identities_repo.list_linked(conn)
    unlinked = identities_repo.list_unlinked_members(conn)

    present = None
    parent = groups_repo.get_by_slug(conn, groups_repo.PARENT_SLUG)
    if parent is not None:
        try:
            present = client.list_members(parent.groupme_id)
        except GroupMeError:
            present = None

    blocks = identity_svc.blocked_identities(conn, present=present)
    return GroupMeIdentitiesOut(
        linked=len(linked),
        unlinked=[
            GroupMeUnlinkedOut(member_id=mid, display_name=name) for mid, name in unlinked
        ],
        drift=[
            GroupMeDriftOut(
                groupme_user_id=b.groupme_user_id,
                nickname=b.current_nickname or "",
            )
            for b in blocks.values()
            if b.reason == identity_svc.BLOCK_DRIFTED
        ],
        blocked=[
            GroupMeBlockedOut(
                groupme_user_id=b.groupme_user_id,
                member_id=b.member_id,
                display_name=b.display_name,
                reason=b.reason,
                detail=b.detail,
            )
            for b in sorted(blocks.values(), key=lambda b: b.display_name)
        ],
        rows=[
            GroupMeIdentityRowOut(
                member_id=link.member_id,
                display_name=link.display_name,
                nickname=link.nickname,
                confidence=link.confidence,
            )
            for link in linked
        ],
    )


# ---------------------------------------------------------------------------
# Membership
# ---------------------------------------------------------------------------


def _membership_plan(
    conn: sqlite3.Connection,
    client: GroupMeClient,
    *,
    on_or_after: str,
    on_or_before: str,
    semester: str | None,
) -> membership_svc.MembershipPlan:
    sem = resolve_semester(conn, semester)
    present = client.list_members(_parent(conn).groupme_id)
    return membership_svc.build_plan(
        conn,
        semester_id=sem.id,
        on_or_after=on_or_after,
        on_or_before=on_or_before,
        present=present,
    )


def _plan_dto(plan: membership_svc.MembershipPlan) -> GroupMeMembershipPlanOut:
    digest = confirm_svc.membership_digest(plan)
    preview = confirm_svc.issue(digest, post_count=len(plan.add) + len(plan.remove))
    return GroupMeMembershipPlanOut(
        preview_id=preview.preview_id,
        digest=preview.digest,
        add=[
            GroupMeMembershipAddOut(
                member_id=a.member_id,
                display_name=a.display_name,
                groupme_user_id=a.groupme_user_id,
            )
            for a in plan.add
        ],
        remove=[
            GroupMeMembershipRemoveOut(
                member_id=r.member_id,
                display_name=r.display_name,
                membership_id=r.membership_id,
                reason=r.reason,
            )
            for r in plan.remove
        ],
        unrecognised=[
            GroupMeUnrecognisedOut(groupme_user_id=u.groupme_user_id, nickname=u.nickname)
            for u in plan.unrecognised
        ],
        blocked=[
            GroupMeBlockedOut(
                groupme_user_id=b.groupme_user_id,
                member_id=b.member_id,
                display_name=b.display_name,
                reason=b.reason,
                detail=b.detail,
            )
            for b in plan.blocked
        ],
        hard_excluded=[
            GroupMeHardExcludedOut(
                groupme_user_id=e.groupme_user_id,
                member_id=e.member_id,
                display_name=e.display_name,
                reason=e.reason,
                detail=e.detail,
            )
            for e in plan.hard_excluded
        ],
        permanent=[
            GroupMePermanentOut(
                groupme_user_id=member.groupme_user_id,
                member_id=member.member_id,
                display_name=member.display_name,
                reason=member.reason,
                detail=member.detail,
            )
            for member in plan.permanent
        ],
        unlinked_workers=[
            GroupMeUnlinkedOut(member_id=mid, display_name=name)
            for mid, name in plan.unlinked_workers
        ],
    )


@router.get("/membership-plan", response_model=GroupMeMembershipPlanOut)
def membership_plan(
    on_or_after: str,
    on_or_before: str,
    semester: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
    client: GroupMeClient = Depends(get_groupme_client),
) -> GroupMeMembershipPlanOut:
    with service_errors():
        plan = _membership_plan(
            conn,
            client,
            on_or_after=on_or_after,
            on_or_before=on_or_before,
            semester=semester,
        )
    return _plan_dto(plan)


@router.post("/membership/apply", response_model=GroupMeMembershipApplyOut)
def apply_membership(
    body: GroupMeConfirmIn,
    conn: sqlite3.Connection = Depends(get_conn),
    client: GroupMeClient = Depends(get_groupme_client),
) -> GroupMeMembershipApplyOut:
    with service_errors():
        _require_confirm(body.confirm, "change group membership")
        plan = _membership_plan(
            conn,
            client,
            on_or_after=body.on_or_after,
            on_or_before=body.on_or_before,
            semester=body.semester,
        )
        _require_preview(body, confirm_svc.membership_digest(plan))
        confirm_svc.check_volume(
            post_count=len(plan.add) + len(plan.remove),
            on_or_after=body.on_or_after,
            on_or_before=body.on_or_before,
        )
        result = outbound_svc.apply_membership(conn, client, plan, confirm=True)
    return GroupMeMembershipApplyOut(
        added=list(result.added), removed=list(result.removed), results_id=result.results_id
    )


# ---------------------------------------------------------------------------
# Announcements
# ---------------------------------------------------------------------------


def _announce_plan(
    conn: sqlite3.Connection,
    *,
    on_or_after: str,
    on_or_before: str,
    semester: str | None,
    client: GroupMeClient | None = None,
) -> announce_svc.AnnouncePlan:
    """Build the plan, consulting live membership when a client is supplied.

    The preview route passes a client and tolerates it failing; the send route
    passes one and does not. A preview that cannot reach GroupMe still renders
    the messages from the database and labels itself ``database-only``, because a
    chair reading his own schedule offline is a reasonable thing to do and an
    outage should not turn it into a 500.
    """
    sem = resolve_semester(conn, semester)
    present = None
    if client is not None:
        parent = groups_repo.get_by_slug(conn, groups_repo.PARENT_SLUG)
        if parent is not None:
            present = client.list_members(parent.groupme_id)
    return announce_svc.build_plan(
        conn,
        semester_id=sem.id,
        on_or_after=on_or_after,
        on_or_before=on_or_before,
        present=present,
    )


def _post_dto(post: announce_svc.AnnouncePost) -> GroupMeAnnouncePostOut:
    return GroupMeAnnouncePostOut(
        group_slug=post.group_slug,
        label=post.label,
        event_date=post.event_date,
        event_name=post.event_name,
        text=post.text,
        mentions=[
            GroupMeMentionOut(
                user_id=m.user_id,
                display_name=m.display_name,
                offset=m.offset,
                length=m.length,
            )
            for m in post.mentions
        ],
        unlinked=[
            GroupMeUnlinkedOut(
                member_id=u.member_id, display_name=u.display_name, reason=u.reason
            )
            for u in post.unlinked
        ],
        char_count=post.char_count,
        too_long=post.too_long,
    )


@router.get("/announce-preview", response_model=GroupMeAnnouncePreviewOut)
def announce_preview(
    on_or_after: str,
    on_or_before: str,
    semester: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
    client: GroupMeClient = Depends(get_groupme_client),
) -> GroupMeAnnouncePreviewOut:
    """Exactly what would be posted, including the mention offsets.

    Returns a ``preview_id`` and a ``digest`` over the destinations, bodies,
    mention user ids, loci and post count. ``POST /announce`` requires both back
    and rebuilds the plan to check them, so what the chair reads here is the
    only thing that can be sent.

    Over-length posts (GroupMe's limit is 1000 characters) are separated into
    ``oversize`` HERE, while there is still time to shorten an event name — not
    at send time, halfway through a batch he has already approved.
    """
    with service_errors():
        try:
            plan = _announce_plan(
                conn,
                on_or_after=on_or_after,
                on_or_before=on_or_before,
                semester=semester,
                client=client,
            )
        except GroupMeError:
            # Degraded, and it says so: identity_check stays "database-only".
            plan = _announce_plan(
                conn,
                on_or_after=on_or_after,
                on_or_before=on_or_before,
                semester=semester,
            )
    digest = confirm_svc.announce_digest(plan)
    preview = confirm_svc.issue(digest, post_count=len(plan.posts))
    return GroupMeAnnouncePreviewOut(
        preview_id=preview.preview_id,
        digest=preview.digest,
        posts=[_post_dto(p) for p in plan.posts],
        oversize=[_post_dto(p) for p in plan.oversize],
        unroutable=[
            GroupMeUnroutableOut(
                event_date=u.event_date,
                event_name=u.event_name,
                weekday=u.weekday,
                reason=u.reason,
            )
            for u in plan.unroutable
        ],
        identity_check=plan.identity_check,
    )


@router.post("/announce", response_model=GroupMeAnnounceResultOut)
def announce(
    body: GroupMeConfirmIn,
    conn: sqlite3.Connection = Depends(get_conn),
    client: GroupMeClient = Depends(get_groupme_client),
) -> GroupMeAnnounceResultOut:
    """Send the previewed posts, once each, if nothing has moved.

    Four gates, in order, and every one of them refuses BEFORE anything is sent:
    ``confirm``, the preview digest, the volume cap, and the outbound ledger.
    """
    with service_errors():
        _require_confirm(body.confirm, "post announcements")
        plan = _announce_plan(
            conn,
            on_or_after=body.on_or_after,
            on_or_before=body.on_or_before,
            semester=body.semester,
            client=client,
        )
        _require_preview(body, confirm_svc.announce_digest(plan))
        confirm_svc.check_volume(
            post_count=len(plan.posts),
            on_or_after=body.on_or_after,
            on_or_before=body.on_or_before,
        )
        sent = outbound_svc.post_announcements(conn, client, plan.posts, confirm=True)
    return GroupMeAnnounceResultOut(
        posted=[
            GroupMePostedOut(
                group_slug=s.group_slug,
                label=s.label,
                event_date=s.event_date,
                event_name=s.event_name,
                message_id=s.message_id,
                mention_count=s.mention_count,
                outcome=s.outcome,
                detail=s.detail,
            )
            for s in sent
        ]
    )


@router.post("/reply", response_model=GroupMeOkOut)
def reply(
    body: GroupMeReplyIn,
    conn: sqlite3.Connection = Depends(get_conn),
    client: GroupMeClient = Depends(get_groupme_client),
) -> GroupMeOkOut:
    """Answer a topic from the dashboard, without opening GroupMe."""
    with service_errors():
        _require_confirm(body.confirm, "post a reply")
        outbound_svc.post_reply(
            conn, client, group_slug=body.group_slug, text=body.text, confirm=True
        )
    return GroupMeOkOut(ok=True)


# ---------------------------------------------------------------------------
# Identity mapping (write)
# ---------------------------------------------------------------------------


@router.post("/identities/map", response_model=GroupMeIdentitiesOut)
def map_identities(
    confirm: bool = False,
    conn: sqlite3.Connection = Depends(get_conn),
    client: GroupMeClient = Depends(get_groupme_client),
) -> GroupMeIdentitiesOut:
    """Apply the exact and alias matches. Ambiguous ones are left alone, always.

    ``confirm`` guards this like the outbound routes even though it writes only
    to the local database: a link decides who gets @-ed, so a wrong one is an
    outbound mistake with a delay on it.
    """
    with service_errors():
        _require_confirm(confirm, "write identity links")
        source = groups_repo.get_by_slug(conn, groups_repo.ROSTER_SOURCE_SLUG)
        if source is None:
            raise LookupError(
                f"no GroupMe group registered as {groups_repo.ROSTER_SOURCE_SLUG!r}"
            )
        accounts = client.list_members(source.groupme_id)
        plan = identity_svc.plan_for_roster(conn, accounts)
        with transaction(conn):
            # ONLY the exact and alias equalities. Ambiguities are left in the
            # plan for a human, and renamed accounts are NOT quietly refreshed —
            # both would be this route deciding something it cannot know.
            identity_svc.apply_proposals(conn, plan.proposals)
    return identities(conn=conn, client=client)
