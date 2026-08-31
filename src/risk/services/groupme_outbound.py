"""The only functions in this codebase that change something in GroupMe.

Everything else builds plans. These send them, and they are deliberately small,
deliberately separate, and deliberately paranoid: a plan can be printed, diffed,
reviewed and thrown away, and the moment a plan becomes a message in the
chapter's chat is a call somebody had to make on purpose.

THE SEND PROTOCOL, in order, per post:

1. Verify the mention loci against the finished text
   (:func:`groupme_announce.verify_mentions`). Fails → nothing is sent.
2. RESERVE a ledger row in its own committed transaction, BEFORE the HTTP call.
   Already ``sent`` → skip. ``pending`` or ``unknown`` → refuse and send the
   operator to ``reconcile``. Never a blind retry.
3. Make the call.
4. Settle the row: ``sent`` with the message id, ``failed`` on a definite
   rejection, ``unknown`` on an ambiguous one.

Step 2 before step 3 is the entire design. The failure being defended against is
the gap between "GroupMe has it" and "we recorded that GroupMe has it", and a row
written after the call lives inside that gap — which is where a crash, a
LaunchAgent restart, a double click and two racing processes all put you.

``confirm`` is a REQUIRED keyword on every sending function, with no default. An
omitted argument is a TypeError; a defaulted one is a thing somebody forgets.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from risk.db.connection import transaction
from risk.repos import groupme_groups as groups_repo
from risk.repos import groupme_outbound as ledger_repo
from risk.services.groupme import (
    GroupMeAmbiguousError,
    GroupMeClient,
    GroupMeError,
    Mention,
)
from risk.services.groupme_announce import AnnouncePost, verify_mentions
from risk.services.groupme_membership import MembershipPlan


class OutboundBlockedError(RuntimeError):
    """This message cannot be sent until somebody resolves its ledger row."""


@dataclass(frozen=True, slots=True)
class PostedMessage:
    group_slug: str
    label: str
    event_id: int
    event_date: str
    event_name: str
    message_id: str | None
    mention_count: int
    outcome: str
    """``sent`` | ``already_sent`` | ``unknown`` | ``blocked``. Reported per post
    rather than collapsed into one status, because a batch that half-succeeded
    is the case a chair most needs the detail of."""
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class MembershipResult:
    added: tuple[str, ...]
    """Display names QUEUED for addition. GroupMe's add is asynchronous and
    returns a results_id, so "queued" is the strongest true word available."""
    removed: tuple[str, ...]
    results_id: str | None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def post_announcements(
    conn: sqlite3.Connection,
    client: GroupMeClient,
    posts: Sequence[AnnouncePost],
    *,
    confirm: bool,
) -> list[PostedMessage]:
    """Send each rendered post to its topic, at most once, ever.

    Posts go to the TOPIC id — topics accept messages at their own id even
    though they accept nothing else there.

    Every post is verified before ANY post is sent. A batch that fails
    verification halfway would leave the weekend half-announced, and the half
    that went out is the half nobody can take back.
    """
    if not confirm:
        raise ValueError("refusing to post without confirm=True")
    for post in posts:
        if post.too_long:
            raise ValueError(
                f"{post.event_name} on {post.event_date} is {post.char_count} characters; "
                "GroupMe's limit is 1000. This should have been caught in the preview."
            )
        verify_mentions(post.text, post.mentions)

    sent: list[PostedMessage] = []
    for post in posts:
        row, claimed = _reserve(conn, post)
        if row.state == "sent":
            sent.append(
                _result(
                    post,
                    row.message_id,
                    "already_sent",
                    "the ledger already records this exact message as delivered",
                )
            )
            continue
        if row.state != "pending" or not claimed:
            sent.append(
                _result(
                    post,
                    row.message_id,
                    "blocked",
                    f"ledger state is {row.state!r}; run `risk groupme reconcile` first",
                )
            )
            continue

        try:
            message_id = client.post_message(
                post.groupme_id,
                post.text,
                mentions=[
                    Mention(user_id=m.user_id, offset=m.offset, length=m.length)
                    for m in post.mentions
                ],
                source_guid=row.source_guid,
            )
        except GroupMeAmbiguousError as exc:
            with transaction(conn):
                ledger_repo.mark_unknown(conn, row_id=row.id, error=str(exc))
            sent.append(_result(post, None, "unknown", str(exc)))
            continue
        except GroupMeError as exc:
            # A definite rejection. Settled as `failed`, which makes exactly this
            # one post retryable, and then re-raised: the rest of the batch is
            # abandoned rather than pushed through an API that just said no, and
            # a re-run skips whatever already went out.
            with transaction(conn):
                ledger_repo.mark_failed(conn, row_id=row.id, error=str(exc), settled_at=_now())
            raise

        with transaction(conn):
            ledger_repo.mark_sent(
                conn, row_id=row.id, message_id=message_id, settled_at=_now()
            )
        sent.append(_result(post, message_id, "sent", None))
    return sent


def _reserve(conn: sqlite3.Connection, post: AnnouncePost) -> tuple[ledger_repo.OutboundRow, bool]:
    """Claim this exact message in its own committed transaction.

    Its OWN transaction, not the caller's: the reservation has to be durable
    before the request leaves, and a reservation that rolls back with the send is
    no reservation at all.

    The boolean says whether this call acquired the right to send. A pre-existing
    ``pending`` row has the same stored state as a newly inserted one, so the
    caller cannot safely infer ownership from the row alone.
    """
    with transaction(conn):
        existing = ledger_repo.get(
            conn,
            event_id=post.event_id,
            destination_slug=post.group_slug,
            content_version=post.content_version,
        )
        row = ledger_repo.reserve(
            conn,
            event_id=post.event_id,
            destination_slug=post.group_slug,
            content_version=post.content_version,
            reserved_at=_now(),
        )
        return row, existing is None or existing.state == "failed"


def _result(
    post: AnnouncePost, message_id: str | None, outcome: str, detail: str | None
) -> PostedMessage:
    return PostedMessage(
        group_slug=post.group_slug,
        label=post.label,
        event_id=post.event_id,
        event_date=post.event_date,
        event_name=post.event_name,
        message_id=message_id,
        mention_count=len(post.mentions),
        outcome=outcome,
        detail=detail,
    )


@dataclass(frozen=True, slots=True)
class Reconciliation:
    event_id: int
    destination_slug: str
    source_guid: str
    resolved_to: str
    message_id: str | None


def reconcile(
    conn: sqlite3.Connection,
    client: GroupMeClient,
    *,
    lookback: int = 100,
) -> list[Reconciliation]:
    """Resolve ``unknown`` and stale ``pending`` rows by READING THE CHAT BACK.

    GroupMe echoes ``source_guid`` on every message it stored, so "did this
    post?" has an evidential answer that needs no timeout heuristic. A guid found
    in the topic settles the row as ``sent``; one absent from the last
    ``lookback`` messages settles it as ``failed``, and therefore retryable.

    Deliberately operator-invoked rather than automatic. It reads live data and
    then changes what a later run is allowed to send, so an automatic version
    running at the wrong moment — mid-outage, or against a busy topic that has
    pushed the message past the lookback — would mark a delivered announcement
    retryable and post it a second time.
    """
    resolved: list[Reconciliation] = []
    # One fetch per destination, reused across that destination's rows.
    seen: dict[str, dict[str, str]] = {}
    for row in ledger_repo.list_unsettled(conn):
        group = groups_repo.get_by_slug(conn, row.destination_slug)
        if group is None:
            continue
        if row.destination_slug not in seen:
            messages = client.list_messages(group.groupme_id, limit=lookback)
            seen[row.destination_slug] = {
                m.source_guid: m.message_id for m in messages if m.source_guid
            }
        found = seen[row.destination_slug]
        if row.source_guid in found:
            message_id = found[row.source_guid]
            outcome = "sent"
            with transaction(conn):
                ledger_repo.mark_sent(
                    conn, row_id=row.id, message_id=message_id, settled_at=_now()
                )
        else:
            message_id = None
            outcome = "failed"
            with transaction(conn):
                ledger_repo.mark_failed(
                    conn,
                    row_id=row.id,
                    error=f"not found in the last {lookback} messages",
                    settled_at=_now(),
                )
        resolved.append(
            Reconciliation(
                event_id=row.event_id,
                destination_slug=row.destination_slug,
                source_guid=row.source_guid,
                resolved_to=outcome,
                message_id=message_id,
            )
        )
    return resolved


def apply_membership(
    conn: sqlite3.Connection,
    client: GroupMeClient,
    plan: MembershipPlan,
    *,
    confirm: bool,
    parent_slug: str = groups_repo.PARENT_SLUG,
) -> MembershipResult:
    """Add and remove on the parent group.

    Adds first, then removes. The order matters on a re-run near a boundary: if
    a remove failed halfway last time, doing the adds first means the group is
    never briefly missing somebody who is about to work.

    Not ledgered, unlike announcements, and that asymmetry is deliberate. Adds
    and removes are IDEMPOTENT against GroupMe — adding somebody already in the
    group is a no-op, removing somebody already gone is a no-op — so a repeat
    costs nothing. A repeated announcement notifies sixty people twice.
    """
    if not confirm:
        raise ValueError("refusing to change group membership without confirm=True")
    parent = groups_repo.get_by_slug(conn, parent_slug)
    if parent is None:
        raise LookupError(
            f"no GroupMe group registered as {parent_slug!r} — seed it with `risk groupme seed`"
        )

    results_id = client.add_members(
        parent.groupme_id,
        [(a.groupme_user_id, a.nickname or a.display_name) for a in plan.add],
    )
    for removal in plan.remove:
        client.remove_member(parent.groupme_id, removal.membership_id)

    return MembershipResult(
        added=tuple(a.display_name for a in plan.add),
        removed=tuple(r.display_name for r in plan.remove),
        results_id=results_id,
    )


def post_reply(
    conn: sqlite3.Connection,
    client: GroupMeClient,
    *,
    group_slug: str,
    text: str,
    confirm: bool,
) -> str | None:
    """Send one free-text message to a registered group or topic.

    Not ledgered: this is a human typing a sentence and pressing send, so there
    is no plan to have drifted and no batch to half-complete. It still requires
    ``confirm``, because every outbound action does.
    """
    if not confirm:
        raise ValueError("refusing to post without confirm=True")
    if not text.strip():
        raise ValueError("a reply needs text")
    group = groups_repo.get_by_slug(conn, group_slug)
    if group is None:
        raise LookupError(f"no GroupMe group registered as {group_slug!r}")
    return client.post_message(group.groupme_id, text)
