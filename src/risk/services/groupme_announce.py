"""Build each party's posts and work out which chats receive them.

The message the chapter reads is three lines of nothing much::

    Here's who works next week's Tuesday function
    @Test Alpha: door
    @Test Bravo: rides
    @Test Charlie: bar

and one number per @ that has to be exactly right.

GROUPME MENTIONS ARE OFFSETS, NOT MARKUP. The attachment carries
``user_ids: [...]`` and ``loci: [[start, length], ...]`` as positional partners,
and the text carries no marker at all — GroupMe finds the highlight by counting
characters. Get the count wrong and the app underlines the wrong span, pings the
wrong person, or silently drops the mention. So the offsets are computed by the
same function that builds the string, from the string itself, and the invariant
``text[offset:offset + length] == "@" + nickname`` is asserted in the tests
rather than reasoned about.

Two consequences worth stating:

* Offsets are CODE POINTS (Python's own ``len``). An accented or emoji nickname
  is longer in bytes than in characters, and the byte count is not what GroupMe
  wants. Nicknames are never normalised, trimmed or re-cased for the message —
  the text must contain what GroupMe holds, character for character, or the
  count describes a string nobody has.
* The mention always begins a line, so the offset is the line's own start. That
  is not an accident of the format; it is why the format is one person per line.

ROUTING and LABELLING are both anchored to the PARTY's date. Assignments whose
``shift_type_windows.occupies_event_night`` flag is true go to that party
weekday's topic. Assignments whose flag is false go to the standalone
setup/cleanup group, even though setup may be worked two days before the party
and cleanup the next morning. An unavailable destination makes only that crew
unroutable; it is never silently dropped or redirected to another chat.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date as _date

from risk.repos import events as events_repo
from risk.repos import groupme_groups as groups_repo
from risk.repos import groupme_identities as identities_repo
from risk.repos import groupme_outbound as outbound_repo
from risk.repos import shift_type_windows as windows_repo
from risk.repos import shifts as shifts_repo
from risk.services import groupme_identity as identity_svc
from risk.services.groupme import GroupMeMember

SHIFT_LABELS: dict[str, str] = {
    "driver": "rides",
    "door": "door",
    "bar": "bar",
    "setup": "setup",
    "cleanup": "cleanup",
    "dj": "DJ",
}
"""What the chapter calls each post. ``driver`` is the database's word and
``rides`` is the chapter's; the message uses theirs. ``DJ`` is capitalised
because it is an acronym and "dj on dj" reads as a typo."""

SHIFT_ORDER: tuple[str, ...] = ("door", "driver", "bar", "dj", "setup", "cleanup")
"""Line order. Night posts first, in the order the chair reads them out, then the
two that are worked off the night. Fixed rather than alphabetical so a preview
diffed against the previous one shows what changed rather than what re-sorted."""

MAX_MESSAGE_CHARS = 1000
"""GroupMe's per-message limit, checked at PREVIEW time.

Checked here rather than at send time on purpose. A long nickname or a large crew
is a property of the rendered plan, not of the network, so it is knowable before
the chair reads the preview — and discovering it afterwards means a message he
already approved fails, in the middle of a batch, with some of the weekend
announced and some not."""

_DAY_NAMES: tuple[str, ...] = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
# Spelled out rather than taken from strftime: %A follows the process locale,
# and the chair's lead-in must not change language on somebody else's machine.


@dataclass(frozen=True, slots=True)
class Assignment:
    """One person on one post at one event."""

    member_id: int
    display_name: str
    shift_type_slug: str
    slot_index: int
    groupme_user_id: str | None
    nickname: str | None
    unlinked_reason: str = "no linked GroupMe identity"


@dataclass(frozen=True, slots=True)
class PreviewMention:
    user_id: str
    display_name: str
    offset: int
    length: int
    nickname: str
    """Carried so :func:`verify_mentions` can re-derive the expected span from
    the text immediately before sending, instead of trusting arithmetic done
    earlier by a different function."""


@dataclass(frozen=True, slots=True)
class UnlinkedMember:
    member_id: int
    display_name: str
    reason: str = "no linked GroupMe identity"
    """Why there is no ``@``. A blocked link and a missing one look identical in
    the message and must not look identical in the preview — one is a mapping
    the chair has never done, the other is one that has stopped being safe."""


@dataclass(frozen=True, slots=True)
class RenderedPost:
    text: str
    mentions: tuple[PreviewMention, ...]
    unlinked: tuple[UnlinkedMember, ...]


@dataclass(frozen=True, slots=True)
class AnnouncePost:
    group_slug: str
    label: str
    groupme_id: str
    event_id: int
    event_date: str
    event_name: str
    text: str
    mentions: tuple[PreviewMention, ...]
    unlinked: tuple[UnlinkedMember, ...]
    content_version: str
    """Hash of the text plus the mention tuples — the outbound ledger's key."""
    char_count: int
    too_long: bool


@dataclass(frozen=True, slots=True)
class UnroutableEvent:
    event_id: int
    event_date: str
    event_name: str
    weekday: int
    reason: str


@dataclass(frozen=True, slots=True)
class AnnouncePlan:
    posts: tuple[AnnouncePost, ...]
    unroutable: tuple[UnroutableEvent, ...]
    oversize: tuple[AnnouncePost, ...] = ()
    """Posts over :data:`MAX_MESSAGE_CHARS`. Kept OUT of ``posts`` so no send
    path can reach them, and surfaced separately so the chair sees the problem
    while he can still fix it."""
    identity_check: str = "database-only"
    """``full`` when the live parent-group membership was consulted, so drift and
    departures were checked; ``database-only`` when it was not. Stated rather
    than implied: a preview built offline cannot know that somebody renamed
    himself, and pretending otherwise is worse than saying so."""


# ---------------------------------------------------------------------------
# Pure rendering
# ---------------------------------------------------------------------------


def format_function_lead_in(event_date: str | _date) -> str:
    """Name the upcoming function in the chair's own phrasing."""
    day = _date.fromisoformat(event_date) if isinstance(event_date, str) else event_date
    return f"Here's who works next week's {_DAY_NAMES[day.weekday()]} function"


def _sort_key(assignment: Assignment) -> tuple[int, str, int]:
    try:
        rank = SHIFT_ORDER.index(assignment.shift_type_slug)
    except ValueError:
        rank = len(SHIFT_ORDER)
    return (rank, assignment.display_name, assignment.slot_index)


def render_post(
    *, event_name: str, event_date: str | _date, assignments: list[Assignment]
) -> RenderedPost:
    """Build the message text and its mention loci together.

    ONE LINE PER PERSON, so a brother holding two posts at one party gets
    ``@Test Alpha: setup and door`` rather than two lines and two pings. Two
    lines would mean two loci for one user id in one attachment, which GroupMe
    accepts and clients render as two separate highlights of the same name — it
    reads as a mistake, and it doubles the notification.
    """
    header = format_function_lead_in(event_date)

    grouped: dict[int, list[Assignment]] = defaultdict(list)
    for assignment in assignments:
        grouped[assignment.member_id].append(assignment)

    people = sorted(
        (sorted(rows, key=_sort_key) for rows in grouped.values()),
        key=lambda rows: _sort_key(rows[0]),
    )

    lines = [header]
    mentions: list[PreviewMention] = []
    unlinked: list[UnlinkedMember] = []
    cursor = len(header) + 1  # +1 for the newline that will join this to the next line

    for rows in people:
        first = rows[0]
        posts = _join_labels([r.shift_type_slug for r in rows])
        if first.groupme_user_id and first.nickname:
            handle = f"@{first.nickname}"
            mentions.append(
                PreviewMention(
                    user_id=first.groupme_user_id,
                    display_name=first.display_name,
                    offset=cursor,
                    length=len(handle),
                    nickname=first.nickname,
                )
            )
            line = f"{handle}: {posts}"
        else:
            # No link means no guess: his name goes in plain, and he is reported
            # so somebody tells him in person.
            unlinked.append(
                UnlinkedMember(
                    member_id=first.member_id,
                    display_name=first.display_name,
                    reason=first.unlinked_reason,
                )
            )
            line = f"{first.display_name}: {posts}"
        lines.append(line)
        cursor += len(line) + 1

    return RenderedPost(
        text="\n".join(lines),
        mentions=tuple(mentions),
        unlinked=tuple(unlinked),
    )


def _join_labels(slugs: list[str]) -> str:
    """``door`` / ``door and setup`` / ``door, setup and cleanup``.

    De-duplicated: two door slots for one man is a scheduling error, not
    something to announce twice.
    """
    seen: list[str] = []
    for slug in slugs:
        label = SHIFT_LABELS.get(slug, slug)
        if label not in seen:
            seen.append(label)
    if len(seen) == 1:
        return seen[0]
    return f"{', '.join(seen[:-1])} and {seen[-1]}"


class MentionMismatchError(ValueError):
    """The loci do not describe the text they are about to be sent with."""


def verify_mentions(text: str, mentions: Sequence[PreviewMention]) -> None:
    """Re-derive every mention from the finished text. Raise rather than send.

    Called immediately before the HTTP call, on the exact string that will go on
    the wire, and it checks all of:

    * one locus per user id (the attachment pairs them POSITIONALLY, so a
      length mismatch silently shifts every mention after the gap onto the wrong
      person);
    * loci are ordered and non-overlapping (two overlapping spans are rendered
      unpredictably by GroupMe clients and are never what was intended);
    * every span, sliced out of the text, is exactly ``"@" + that member's
      current nickname`` — the one check that catches an offset computed against
      a string that was subsequently edited.

    Everything here is redundant if :func:`render_post` is correct, and that is
    the point. It costs microseconds and the failure it guards against is
    @-ing the wrong brother in front of the chapter, which is not undoable.
    """
    offsets = [m.offset for m in mentions]
    if len(offsets) != len({(m.offset, m.length, m.user_id) for m in mentions}):
        raise MentionMismatchError("duplicate mention locus — refusing to send")
    if offsets != sorted(offsets):
        raise MentionMismatchError("mention loci are out of order — refusing to send")
    previous_end = -1
    for mention in mentions:
        if mention.offset < 0 or mention.length <= 0:
            raise MentionMismatchError(
                f"mention for {mention.display_name!r} has a nonsensical locus "
                f"({mention.offset}, {mention.length}) — refusing to send"
            )
        if mention.offset < previous_end:
            raise MentionMismatchError(
                f"mention for {mention.display_name!r} overlaps the one before it "
                "— refusing to send"
            )
        end = mention.offset + mention.length
        if end > len(text):
            raise MentionMismatchError(
                f"mention for {mention.display_name!r} runs past the end of the "
                "message — refusing to send"
            )
        expected = f"@{mention.nickname}"
        actual = text[mention.offset : end]
        if actual != expected:
            raise MentionMismatchError(
                f"mention locus for {mention.display_name!r} covers {actual!r}, "
                f"not {expected!r} — refusing to send"
            )
        previous_end = end


# ---------------------------------------------------------------------------
# Database-facing layer
# ---------------------------------------------------------------------------


def assignments_for_event(
    conn: sqlite3.Connection,
    event_id: int,
    *,
    blocked: dict[str, identity_svc.Block] | None = None,
) -> list[Assignment]:
    """Everyone assigned at an event, with their GroupMe link if it is USABLE.

    A blocked link (renamed account, duplicate nickname, no longer in the group)
    is dropped to ``None`` right here, at the point the assignment is built, so
    no later code has to remember to check. The man is then rendered without an
    ``@`` and reported in the preview, carrying the reason — which is the
    contract's rule for an unlinked member, and a blocked link is exactly a link
    we are no longer entitled to use.
    """
    blocks = blocked or {}
    out: list[Assignment] = []
    for shift in shifts_repo.list_for_event(conn, event_id):
        if shift.assigned_member_id is None:
            continue
        identity = identities_repo.get_for_member(conn, shift.assigned_member_id)
        user_id = identity.groupme_user_id if identity else None
        nickname = identity.nickname if identity else None
        reason = "no linked GroupMe identity"
        if user_id is not None and user_id in blocks:
            block = blocks[user_id]
            user_id = None
            nickname = None
            reason = f"{block.reason}: {block.detail}"
        elif user_id is not None and not nickname:
            user_id = None
            reason = "linked, but no GroupMe nickname stored"
        out.append(
            Assignment(
                member_id=shift.assigned_member_id,
                display_name=shift.assigned_member_display_name or shift.assigned_member_slug or "",
                shift_type_slug=shift.shift_type_slug,
                slot_index=shift.slot_index,
                groupme_user_id=user_id,
                nickname=nickname,
                unlinked_reason=reason,
            )
        )
    return out


def _split_by_event_night(
    conn: sqlite3.Connection, assignments: list[Assignment]
) -> tuple[list[Assignment], list[Assignment]]:
    """Partition using the stored routing flag, never shift-type names."""
    event_night: list[Assignment] = []
    setup_cleanup: list[Assignment] = []
    for assignment in assignments:
        window = windows_repo.get_by_slug(conn, assignment.shift_type_slug)
        if window is None:
            raise LookupError(
                f"shift type {assignment.shift_type_slug!r} has no row in "
                "shift_type_windows; cannot route its announcement"
            )
        destination = event_night if window.occupies_event_night else setup_cleanup
        destination.append(assignment)
    return event_night, setup_cleanup


def _post_for_assignments(
    *,
    event_id: int,
    event_date: str,
    event_name: str,
    group: groups_repo.GroupMeGroup,
    assignments: list[Assignment],
) -> AnnouncePost:
    """Render one non-empty destination's post using the party's own date."""
    rendered = render_post(
        event_name=event_name,
        event_date=event_date,
        assignments=assignments,
    )
    version = outbound_repo.content_version(
        rendered.text,
        tuple((mention.user_id, mention.offset, mention.length) for mention in rendered.mentions),
    )
    char_count = len(rendered.text)
    return AnnouncePost(
        group_slug=group.slug,
        label=group.label,
        groupme_id=group.groupme_id,
        event_id=event_id,
        event_date=event_date,
        event_name=event_name,
        text=rendered.text,
        mentions=rendered.mentions,
        unlinked=rendered.unlinked,
        content_version=version,
        char_count=char_count,
        too_long=char_count > MAX_MESSAGE_CHARS,
    )


def build_plan(
    conn: sqlite3.Connection,
    *,
    semester_id: int,
    on_or_after: str,
    on_or_before: str,
    parent_slug: str = groups_repo.PARENT_SLUG,
    present: Sequence[GroupMeMember] | None = None,
) -> AnnouncePlan:
    """Up to two posts per event, with every route keyed by the party date.

    ``present`` is the live parent-group member list. Supply it and the plan can
    additionally block renamed accounts and people who have left the group;
    omit it and the plan says so in ``identity_check`` rather than implying a
    check it did not make. The preview endpoint deliberately works either way —
    a GroupMe outage must not stop the chair reading his own schedule.

    Event-night assignments route to the party weekday's topic. Non-event-night
    assignments route to the standalone setup/cleanup group. Either empty half
    is skipped entirely: an empty post reads as "nobody is working", which is
    worse than no post.
    """
    blocks = identity_svc.blocked_identities(conn, present=present)
    posts: list[AnnouncePost] = []
    oversize: list[AnnouncePost] = []
    unroutable: list[UnroutableEvent] = []

    for event in events_repo.list_for_semester(conn, semester_id):
        if event.status == "cancelled":
            continue
        if not on_or_after <= event.date <= on_or_before:
            continue
        assignments = assignments_for_event(conn, event.id, blocked=blocks)
        if not assignments:
            continue

        event_night, setup_cleanup = _split_by_event_night(conn, assignments)
        weekday = _date.fromisoformat(event.date).isoweekday()
        destinations: list[tuple[groups_repo.GroupMeGroup, list[Assignment]]] = []

        if event_night:
            topic = groups_repo.get_topic_for_weekday(
                conn, weekday=weekday, parent_slug=parent_slug
            )
            if topic is None:
                unroutable.append(
                    UnroutableEvent(
                        event_id=event.id,
                        event_date=event.date,
                        event_name=event.display_name,
                        weekday=weekday,
                        reason=(
                            f"no topic registered for {_DAY_NAMES[weekday - 1]} under "
                            f"{parent_slug!r} — seed one or announce this event by hand"
                        ),
                    )
                )
            else:
                destinations.append((topic, event_night))

        if setup_cleanup:
            setup_group = groups_repo.get_by_slug(conn, groups_repo.SETUP_GROUP_SLUG)
            if setup_group is None:
                reason = (
                    "no standalone setup/cleanup group registered as "
                    f"{groups_repo.SETUP_GROUP_SLUG!r} — seed that slug with no parent "
                    "and no weekday"
                )
                unroutable.append(
                    UnroutableEvent(
                        event_id=event.id,
                        event_date=event.date,
                        event_name=event.display_name,
                        weekday=weekday,
                        reason=reason,
                    )
                )
            elif setup_group.parent_slug is not None or setup_group.weekday is not None:
                unroutable.append(
                    UnroutableEvent(
                        event_id=event.id,
                        event_date=event.date,
                        event_name=event.display_name,
                        weekday=weekday,
                        reason=(
                            f"{groups_repo.SETUP_GROUP_SLUG!r} is not standalone — seed that "
                            "slug with no parent and no weekday"
                        ),
                    )
                )
            else:
                destinations.append((setup_group, setup_cleanup))

        for group, routed_assignments in destinations:
            post = _post_for_assignments(
                event_id=event.id,
                event_date=event.date,
                event_name=event.display_name,
                group=group,
                assignments=routed_assignments,
            )
            (oversize if post.too_long else posts).append(post)

    posts.sort(key=lambda p: (p.event_date, p.event_name))
    oversize.sort(key=lambda p: (p.event_date, p.event_name))
    unroutable.sort(key=lambda u: (u.event_date, u.event_name))
    return AnnouncePlan(
        posts=tuple(posts),
        unroutable=tuple(unroutable),
        oversize=tuple(oversize),
        identity_check="full" if present is not None else "database-only",
    )
