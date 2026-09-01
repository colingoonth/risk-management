"""Confirmation is a digest of what was previewed, not a reusable boolean."""

from __future__ import annotations

import time

import pytest

from risk.services.groupme_announce import (
    AnnouncePlan,
    AnnouncePost,
    PreviewMention,
    UnroutableEvent,
)
from risk.services.groupme_confirm import (
    MAX_POSTS_PER_REQUEST,
    MAX_WINDOW_DAYS,
    PREVIEW_TTL_SECONDS,
    PreviewDriftedError,
    PreviewExpiredError,
    announce_digest,
    check_volume,
    issue,
    membership_digest,
    verify,
)
from risk.services.groupme_membership import MembershipAdd, MembershipPlan, MembershipRemoval


def _post(
    text: str = "tuesday, sep 1\n@Test Alpha: door", **kw
) -> AnnouncePost:
    defaults: dict = {
        "group_slug": "risk-tuesday",
        "label": "Risk Tuesday",
        "groupme_id": "topic-1",
        "event_id": 1,
        "event_date": "2026-09-01",
        "event_name": "Sample Mixer",
        "text": text,
        "mentions": (
            PreviewMention(
                user_id="a",
                display_name="Test Alpha",
                offset=46,
                length=11,
                nickname="Test Alpha",
            ),
        ),
        "unlinked": (),
        "content_version": "v1",
        "char_count": len(text),
        "too_long": False,
    }
    defaults.update(kw)
    return AnnouncePost(**defaults)


def _plan(*posts: AnnouncePost, unroutable=()) -> AnnouncePlan:
    return AnnouncePlan(posts=tuple(posts), unroutable=tuple(unroutable))


def test_an_unchanged_plan_verifies() -> None:
    plan = _plan(_post())
    digest = announce_digest(plan)
    preview = issue(digest, post_count=1)
    verify(
        preview_id=preview.preview_id,
        submitted_digest=preview.digest,
        current_digest=announce_digest(_plan(_post())),
    )


def test_a_changed_message_body_is_rejected() -> None:
    before = announce_digest(_plan(_post()))
    preview = issue(before, post_count=1)
    after = announce_digest(
        _plan(_post(text="tuesday, sep 1\n@Test Bravo: door"))
    )
    with pytest.raises(PreviewDriftedError, match="changed"):
        verify(
            preview_id=preview.preview_id, submitted_digest=before, current_digest=after
        )


def test_a_changed_mention_user_id_is_rejected() -> None:
    before = announce_digest(_plan(_post()))
    preview = issue(before, post_count=1)
    moved = _post(
        mentions=(
            PreviewMention(
                user_id="DIFFERENT", display_name="Test Alpha", offset=46, length=11,
                nickname="Test Alpha",
            ),
        )
    )
    with pytest.raises(PreviewDriftedError):
        verify(
            preview_id=preview.preview_id,
            submitted_digest=before,
            current_digest=announce_digest(_plan(moved)),
        )


def test_changed_loci_alone_are_rejected() -> None:
    """Same text, same person, different offsets — a different message."""
    before = announce_digest(_plan(_post()))
    preview = issue(before, post_count=1)
    shifted = _post(
        mentions=(
            PreviewMention(
                user_id="a", display_name="Test Alpha", offset=47, length=11,
                nickname="Test Alpha",
            ),
        )
    )
    with pytest.raises(PreviewDriftedError):
        verify(
            preview_id=preview.preview_id,
            submitted_digest=before,
            current_digest=announce_digest(_plan(shifted)),
        )


def test_a_retargeted_topic_is_rejected() -> None:
    before = announce_digest(_plan(_post()))
    preview = issue(before, post_count=1)
    remapped = _post(groupme_id="a-different-topic")
    with pytest.raises(PreviewDriftedError):
        verify(
            preview_id=preview.preview_id,
            submitted_digest=before,
            current_digest=announce_digest(_plan(remapped)),
        )


def test_an_extra_post_appearing_is_rejected() -> None:
    before = announce_digest(_plan(_post()))
    preview = issue(before, post_count=1)
    after = announce_digest(_plan(_post(), _post(event_id=2, event_date="2026-09-04")))
    with pytest.raises(PreviewDriftedError):
        verify(preview_id=preview.preview_id, submitted_digest=before, current_digest=after)


def test_an_event_becoming_routable_is_rejected() -> None:
    """An unroutable event quietly becoming routable would add an unread post."""
    unroutable = UnroutableEvent(
        event_id=9, event_date="2026-09-07", event_name="Sample Dage", weekday=1, reason="x"
    )
    before = announce_digest(_plan(_post(), unroutable=[unroutable]))
    preview = issue(before, post_count=1)
    with pytest.raises(PreviewDriftedError):
        verify(
            preview_id=preview.preview_id,
            submitted_digest=before,
            current_digest=announce_digest(_plan(_post())),
        )


def test_an_expired_preview_is_rejected_before_drift_is_considered() -> None:
    digest = announce_digest(_plan(_post()))
    stale = issue(digest, post_count=1, issued_at=time.time() - PREVIEW_TTL_SECONDS - 1)
    with pytest.raises(PreviewExpiredError, match="expired"):
        verify(
            preview_id=stale.preview_id, submitted_digest=digest, current_digest=digest
        )


def test_a_preview_id_that_does_not_match_its_digest_is_rejected() -> None:
    """Pairing somebody else's token with your own digest does not work."""
    preview = issue(announce_digest(_plan(_post())), post_count=1)
    other = announce_digest(_plan(_post(text="different")))
    with pytest.raises(PreviewExpiredError, match="does not match"):
        verify(preview_id=preview.preview_id, submitted_digest=other, current_digest=other)


@pytest.mark.parametrize("bad", ["", "nonsense", "abc.def", "12345"])
def test_a_malformed_preview_id_is_rejected(bad: str) -> None:
    digest = announce_digest(_plan(_post()))
    with pytest.raises(PreviewExpiredError):
        verify(preview_id=bad, submitted_digest=digest, current_digest=digest)


def test_a_future_stamped_preview_is_rejected() -> None:
    digest = announce_digest(_plan(_post()))
    preview = issue(digest, post_count=1, issued_at=time.time() + 3600)
    with pytest.raises(PreviewExpiredError, match="future"):
        verify(preview_id=preview.preview_id, submitted_digest=digest, current_digest=digest)


# --- membership ---


def test_membership_digest_changes_when_a_membership_id_is_reissued() -> None:
    def plan(membership_id: str) -> MembershipPlan:
        return MembershipPlan(
            add=(),
            remove=(
                MembershipRemoval(
                    member_id=1,
                    display_name="Test Alpha",
                    membership_id=membership_id,
                    groupme_user_id="a",
                    reason="done",
                ),
            ),
            unrecognised=(),
            blocked=(),
            hard_excluded=(),
            unlinked_workers=(),
        )

    before = membership_digest(plan("mem-1"))
    preview = issue(before, post_count=1)
    with pytest.raises(PreviewDriftedError):
        verify(
            preview_id=preview.preview_id,
            submitted_digest=before,
            current_digest=membership_digest(plan("mem-2")),
        )


def test_membership_digest_changes_when_somebody_is_added() -> None:
    empty = MembershipPlan(
        add=(),
        remove=(),
        unrecognised=(),
        blocked=(),
        hard_excluded=(),
        unlinked_workers=(),
    )
    grown = MembershipPlan(
        add=(
            MembershipAdd(
                member_id=1, display_name="Test Alpha", groupme_user_id="a", nickname="Test Alpha"
            ),
        ),
        remove=(),
        unrecognised=(),
        blocked=(),
        hard_excluded=(),
        unlinked_workers=(),
    )
    assert membership_digest(empty) != membership_digest(grown)


# --- volume caps ---


def test_too_many_posts_is_refused() -> None:
    with pytest.raises(ValueError, match="cap is"):
        check_volume(
            post_count=MAX_POSTS_PER_REQUEST + 1,
            on_or_after="2026-09-01",
            on_or_before="2026-09-14",
        )


def test_a_mistyped_year_is_refused_by_the_window_cap() -> None:
    with pytest.raises(ValueError, match=f"cap is {MAX_WINDOW_DAYS}"):
        check_volume(post_count=1, on_or_after="2026-09-01", on_or_before="2027-09-01")


def test_a_backwards_window_is_refused() -> None:
    with pytest.raises(ValueError, match="before"):
        check_volume(post_count=1, on_or_after="2026-09-14", on_or_before="2026-09-01")


def test_a_normal_biweekly_block_is_allowed() -> None:
    check_volume(post_count=4, on_or_after="2026-09-01", on_or_before="2026-09-14")
