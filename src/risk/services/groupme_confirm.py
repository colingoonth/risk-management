"""Confirmation as a digest of what was previewed, not a reusable boolean.

``confirm: true`` is not a confirmation. It is a constant, and a constant is
satisfied by a UI that hardcodes it, by a curl someone pasted from the docs, and
by a chair who approved a preview on Friday and clicked on Monday. None of those
say "I read THESE messages and I want THEM sent".

So the preview returns a DIGEST of exactly what would go out — destinations,
message bodies, mention user ids, mention loci, and the number of posts — plus a
short-lived ``preview_id``. The send route takes both back, RECOMPUTES the
preview from live data, and refuses if the two digests differ. Anything that
moved in between (a shift reassigned, a nickname changed, an event moved, a topic
re-pointed) changes the digest and stops the send, which is the entire point:
the chair approved a specific set of messages, and only that set may be sent.

WHAT THE preview_id ADDS. The digest catches change; it cannot catch staleness
when nothing changed. The ``preview_id`` carries an issue time and expires, so an
approval cannot be replayed a week later against a schedule that merely happens
to be identical. It is HMAC'd with a secret generated at import, which makes it
unforgeable for the life of the process and simply invalid across a restart —
the recovery is to refresh the preview, which is what a restart should force
anyway. This is a local single-user app (ADR-014) with no user auth, so the token
is a freshness and integrity check, not an authentication credential, and it is
not described as one.

VOLUME CAP. A single send is capped at :data:`MAX_POSTS_PER_REQUEST` posts and
:data:`MAX_WINDOW_DAYS` of calendar. A mistyped year turns "the next fortnight"
into "the next fourteen months", and the failure mode of that mistake is dozens
of announcements to the chapter that cannot be unsent.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date as _date

from risk.services.groupme_announce import AnnouncePlan
from risk.services.groupme_membership import MembershipPlan

PREVIEW_TTL_SECONDS = 15 * 60
"""How long an approved preview stays sendable. Long enough to read four
messages carefully, short enough that yesterday's approval is not usable."""

MAX_POSTS_PER_REQUEST = 10
"""One biweekly block is four to six parties. Ten is headroom; a semester is
not."""

MAX_WINDOW_DAYS = 31
"""Guards the mistyped year before the post cap has to."""

_SECRET = secrets.token_bytes(32)
# Process-lifetime. See the module docstring: a restart invalidates outstanding
# previews, which is the correct behaviour rather than a limitation.

_FIELD = "\x1f"
_RECORD = "\x1e"
_SECTION = "\x1d"
# Separators that cannot appear in a slug, a user id, or a rendered message,
# so no two different plans can serialise to the same string.


class PreviewExpiredError(ValueError):
    """The preview is older than the TTL, or was issued by a previous process."""


class PreviewDriftedError(ValueError):
    """Something changed between the preview and the send."""


@dataclass(frozen=True, slots=True)
class Preview:
    preview_id: str
    digest: str
    post_count: int


def _digest(sections: Sequence[str]) -> str:
    return hashlib.sha256(_SECTION.join(sections).encode("utf-8")).hexdigest()


def announce_digest(plan: AnnouncePlan) -> str:
    """Hash the destinations, bodies, mention ids and loci, and the post count.

    Loci are included, not just the user ids: a message that tags the right
    people at the wrong offsets underlines the wrong words and is a different
    message. Unroutable events are included too — an event silently becoming
    routable between preview and send would otherwise add a post nobody read.
    """
    records = [f"count{_FIELD}{len(plan.posts)}"]
    for post in plan.posts:
        mention_parts = [
            f"{m.user_id}{_FIELD}{m.offset}{_FIELD}{m.length}" for m in post.mentions
        ]
        records.append(
            _RECORD.join(
                [
                    post.group_slug,
                    post.groupme_id,
                    str(post.event_id),
                    post.event_date,
                    post.text,
                    *mention_parts,
                ]
            )
        )
    records.extend(f"unroutable{_FIELD}{u.event_id}{_FIELD}{u.event_date}" for u in plan.unroutable)
    return _digest(records)


def membership_digest(plan: MembershipPlan) -> str:
    """Hash exactly who would be added and exactly who would be removed.

    ``membership_id`` is in the removal record because that is the value the
    remove call sends. If GroupMe re-issued somebody's membership row between
    the preview and the click, the removal targets a different row than the one
    the chair approved.
    """
    records = [f"count{_FIELD}{len(plan.add)}{_FIELD}{len(plan.remove)}"]
    records.extend(
        f"add{_FIELD}{a.member_id}{_FIELD}{a.groupme_user_id}" for a in plan.add
    )
    records.extend(
        f"remove{_FIELD}{r.member_id}{_FIELD}{r.groupme_user_id}{_FIELD}{r.membership_id}"
        for r in plan.remove
    )
    return _digest(records)


def issue(digest: str, *, post_count: int, issued_at: float | None = None) -> Preview:
    """Mint a preview token bound to this digest and this moment."""
    stamp = int(issued_at if issued_at is not None else time.time())
    tag = hmac.new(_SECRET, f"{stamp}{_FIELD}{digest}".encode(), hashlib.sha256).hexdigest()[:32]
    return Preview(preview_id=f"{stamp}.{tag}", digest=digest, post_count=post_count)


def verify(
    *,
    preview_id: str,
    submitted_digest: str,
    current_digest: str,
    now: float | None = None,
) -> None:
    """Raise unless this exact preview is fresh, intact and still accurate.

    Order matters. Drift is checked LAST so an expired-but-identical preview
    reports "refresh it" rather than "something changed" — the two send the chair
    to different places, and telling him the schedule moved when it did not costs
    him a hunt through a calendar that is fine.
    """
    stamp_text, _, tag = preview_id.partition(".")
    if not tag or not stamp_text.isdigit():
        raise PreviewExpiredError("malformed preview_id — refresh the preview and try again")
    stamp = int(stamp_text)
    expected = hmac.new(
        _SECRET, f"{stamp}{_FIELD}{submitted_digest}".encode(), hashlib.sha256
    ).hexdigest()[:32]
    if not hmac.compare_digest(expected, tag):
        raise PreviewExpiredError(
            "preview_id does not match the digest sent with it — refresh the preview"
        )
    moment = now if now is not None else time.time()
    if moment - stamp > PREVIEW_TTL_SECONDS:
        raise PreviewExpiredError(
            f"preview expired after {PREVIEW_TTL_SECONDS // 60} minutes — refresh it and re-read"
        )
    if moment + 60 < stamp:
        raise PreviewExpiredError("preview is stamped in the future — refresh the preview")
    if not hmac.compare_digest(submitted_digest, current_digest):
        raise PreviewDriftedError(
            "the schedule changed since this preview was generated — "
            "nothing was sent. Refresh the preview and read it again."
        )


def check_volume(*, post_count: int, on_or_after: str, on_or_before: str) -> None:
    """Refuse an outsized request before any of it is sent."""
    start = _date.fromisoformat(on_or_after)
    end = _date.fromisoformat(on_or_before)
    if end < start:
        raise ValueError(f"{on_or_before} is before {on_or_after}")
    span = (end - start).days + 1
    if span > MAX_WINDOW_DAYS:
        raise ValueError(
            f"window is {span} days; the cap is {MAX_WINDOW_DAYS}. "
            "Announce one block at a time."
        )
    if post_count > MAX_POSTS_PER_REQUEST:
        raise ValueError(
            f"{post_count} posts in one request; the cap is {MAX_POSTS_PER_REQUEST}. "
            "Narrow the date range."
        )
