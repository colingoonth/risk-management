"""Mention loci arithmetic — the numbers that decide who gets tagged.

GroupMe finds a mention by counting characters into the message text. There is
no marker in the string, so an offset that is off by one underlines the wrong
word, and an offset that is off by a name tags the wrong brother in front of the
chapter. Every test here asserts the same invariant from a different angle::

    text[offset:offset + length] == "@" + nickname

All names are invented.
"""

from __future__ import annotations

import pytest

from risk.services.groupme_announce import (
    MAX_MESSAGE_CHARS,
    Assignment,
    MentionMismatchError,
    PreviewMention,
    format_function_lead_in,
    render_post,
    verify_mentions,
)


def _assignment(
    member_id: int,
    display_name: str,
    slug: str,
    *,
    nickname: str | None = None,
    user_id: str | None = None,
    slot: int = 0,
) -> Assignment:
    return Assignment(
        member_id=member_id,
        display_name=display_name,
        shift_type_slug=slug,
        slot_index=slot,
        groupme_user_id=user_id,
        nickname=nickname,
    )


def _assert_loci_describe_the_text(rendered) -> None:
    """The single invariant, applied to every mention in a rendered post."""
    for mention in rendered.mentions:
        span = rendered.text[mention.offset : mention.offset + mention.length]
        assert span == f"@{mention.nickname}", (
            f"locus ({mention.offset}, {mention.length}) covers {span!r}, "
            f"not {'@' + mention.nickname!r}"
        )
    verify_mentions(rendered.text, rendered.mentions)


# ---------------------------------------------------------------------------
# The contract's exact format
# ---------------------------------------------------------------------------


def test_the_message_matches_the_specified_format_exactly() -> None:
    rendered = render_post(
        event_name="Sample Mixer",
        event_date="2026-09-01",
        assignments=[
            _assignment(1, "Test Alpha", "door", nickname="Test Alpha", user_id="a"),
            _assignment(2, "Test Bravo", "driver", nickname="Test Bravo", user_id="b"),
            _assignment(3, "Test Charlie", "bar", nickname="Test Charlie", user_id="c"),
        ],
    )
    assert rendered.text == (
        "Here's who works next week's Tuesday function\n"
        "@Test Alpha: door\n"
        "@Test Bravo: rides\n"
        "@Test Charlie: bar"
    )
    assert [(mention.offset, mention.length) for mention in rendered.mentions] == [
        (46, 11),
        (64, 11),
        (83, 13),
    ]
    _assert_loci_describe_the_text(rendered)


def test_function_lead_in_uses_the_party_weekday_and_is_locale_independent() -> None:
    assert format_function_lead_in("2026-09-01") == (
        "Here's who works next week's Tuesday function"
    )
    assert format_function_lead_in("2026-12-25") == (
        "Here's who works next week's Friday function"
    )
    assert format_function_lead_in("2026-01-05") == (
        "Here's who works next week's Monday function"
    )


def test_every_shift_type_is_spelled_the_way_the_chapter_says_it() -> None:
    rendered = render_post(
        event_name="Sample Party",
        event_date="2026-09-04",
        assignments=[
            _assignment(1, "Test Alpha", "driver", nickname="A", user_id="1"),
            _assignment(2, "Test Bravo", "door", nickname="B", user_id="2"),
            _assignment(3, "Test Charlie", "bar", nickname="C", user_id="3"),
            _assignment(4, "Test Delta", "dj", nickname="D", user_id="4"),
            _assignment(5, "Test Echo", "setup", nickname="E", user_id="5"),
            _assignment(6, "Test Foxtrot", "cleanup", nickname="F", user_id="6"),
        ],
    )
    lines = rendered.text.split("\n")[1:]
    assert lines == [
        "@B: door",
        "@A: rides",
        "@C: bar",
        "@D: DJ",
        "@E: setup",
        "@F: cleanup",
    ]
    _assert_loci_describe_the_text(rendered)


# ---------------------------------------------------------------------------
# Multi-byte, combining marks, emoji, apostrophes
# ---------------------------------------------------------------------------


def test_accented_nickname_offsets_are_code_points_not_bytes() -> None:
    rendered = render_post(
        event_name="Sample Mixer",
        event_date="2026-09-01",
        assignments=[
            _assignment(1, "Test Alpha", "door", nickname="Tést Álpha", user_id="a"),
            _assignment(2, "Test Bravo", "bar", nickname="Test Bravo", user_id="b"),
        ],
    )
    _assert_loci_describe_the_text(rendered)
    first = rendered.mentions[0]
    assert first.length == len("@Tést Álpha")
    # The byte length is longer; using it would push every later mention along.
    assert len("@Tést Álpha".encode()) > first.length


def test_combining_marks_are_counted_as_separate_code_points() -> None:
    """NFD input: "e" + U+0301 is two code points that render as one glyph.

    Whatever GroupMe holds is what the text must contain, so the count follows
    the string rather than the glyphs. The invariant is unaffected either way,
    which is exactly why it is the thing asserted.
    """
    decomposed = "Tést Alpha"
    rendered = render_post(
        event_name="Sample Mixer",
        event_date="2026-09-01",
        assignments=[
            _assignment(1, "Test Alpha", "door", nickname=decomposed, user_id="a"),
            _assignment(2, "Test Bravo", "bar", nickname="Test Bravo", user_id="b"),
        ],
    )
    _assert_loci_describe_the_text(rendered)
    assert rendered.mentions[0].length == len(decomposed) + 1


def test_emoji_nickname_does_not_shift_the_next_mention() -> None:
    rendered = render_post(
        event_name="Sample Mixer",
        event_date="2026-09-01",
        assignments=[
            _assignment(1, "Test Alpha", "door", nickname="Test Alpha 🎧", user_id="a"),
            _assignment(2, "Test Bravo", "bar", nickname="Test Bravo", user_id="b"),
        ],
    )
    _assert_loci_describe_the_text(rendered)
    assert len(rendered.mentions) == 2


def test_apostrophe_and_hyphen_nicknames_survive_verbatim() -> None:
    """The nickname is never normalised for the message — only for matching."""
    rendered = render_post(
        event_name="Sample Mixer",
        event_date="2026-09-01",
        assignments=[
            _assignment(1, "Test O'Alpha", "door", nickname="Test O'Alpha", user_id="a"),
            _assignment(2, "Test Bravo-Charlie", "bar", nickname="Test Bravo-Charlie", user_id="b"),
        ],
    )
    assert "@Test O'Alpha: door" in rendered.text
    assert "@Test Bravo-Charlie: bar" in rendered.text
    _assert_loci_describe_the_text(rendered)


def test_two_people_with_the_same_nickname_get_distinct_non_overlapping_loci() -> None:
    """At the rendering layer, identical nicknames are still two separate spans.

    (Whether such a pair may be MENTIONED at all is a separate, stricter
    question answered by the identity blocker — see
    ``test_groupme_identity.py``.)
    """
    rendered = render_post(
        event_name="Sample Mixer",
        event_date="2026-09-01",
        assignments=[
            _assignment(1, "Test Alpha One", "door", nickname="Test Alpha", user_id="a"),
            _assignment(2, "Test Alpha Two", "bar", nickname="Test Alpha", user_id="b"),
        ],
    )
    _assert_loci_describe_the_text(rendered)
    first, second = rendered.mentions
    assert first.user_id != second.user_id
    assert first.offset + first.length <= second.offset


def test_the_event_name_does_not_replace_the_chairs_function_lead_in() -> None:
    rendered = render_post(
        event_name="@Sample Mixer @ the house",
        event_date="2026-09-01",
        assignments=[
            _assignment(1, "Test Alpha", "door", nickname="Test Alpha", user_id="a"),
        ],
    )
    _assert_loci_describe_the_text(rendered)
    assert "@Sample Mixer" not in rendered.text
    assert rendered.text.startswith("Here's who works next week's Tuesday function")


def test_a_newline_inside_the_event_name_cannot_corrupt_the_loci() -> None:
    """The fixed lead-in never interpolates event data into mention text."""
    rendered = render_post(
        event_name="Sample Mixer\n(moved indoors)",
        event_date="2026-09-01",
        assignments=[
            _assignment(1, "Test Alpha", "door", nickname="Test Alpha", user_id="a"),
        ],
    )
    _assert_loci_describe_the_text(rendered)


def test_no_mentions_at_all_is_a_valid_message() -> None:
    rendered = render_post(
        event_name="Sample Mixer",
        event_date="2026-09-01",
        assignments=[_assignment(1, "Test Alpha", "door")],
    )
    assert rendered.mentions == ()
    assert rendered.text == (
        "Here's who works next week's Tuesday function\nTest Alpha: door"
    )
    assert [u.member_id for u in rendered.unlinked] == [1]
    verify_mentions(rendered.text, rendered.mentions)


def test_an_unlinked_member_is_listed_without_an_at_and_reported() -> None:
    rendered = render_post(
        event_name="Sample Mixer",
        event_date="2026-09-01",
        assignments=[
            _assignment(1, "Test Alpha", "door", nickname="Test Alpha", user_id="a"),
            _assignment(2, "Test Bravo", "bar"),
        ],
    )
    assert "Test Bravo: bar" in rendered.text
    assert "@Test Bravo" not in rendered.text
    assert [u.display_name for u in rendered.unlinked] == ["Test Bravo"]
    _assert_loci_describe_the_text(rendered)


def test_one_line_per_person_when_somebody_holds_two_posts() -> None:
    rendered = render_post(
        event_name="Sample Mixer",
        event_date="2026-09-01",
        assignments=[
            _assignment(1, "Test Alpha", "door", nickname="Test Alpha", user_id="a"),
            _assignment(1, "Test Alpha", "setup", nickname="Test Alpha", user_id="a", slot=1),
        ],
    )
    assert rendered.text.count("@Test Alpha") == 1
    assert "@Test Alpha: door and setup" in rendered.text
    assert len(rendered.mentions) == 1
    _assert_loci_describe_the_text(rendered)


# ---------------------------------------------------------------------------
# verify_mentions refuses rather than sending something wrong
# ---------------------------------------------------------------------------


def test_verify_rejects_a_locus_that_covers_the_wrong_name() -> None:
    text = "Here's who works next week's Tuesday function\n@Test Alpha: door"
    bad = (PreviewMention(user_id="a", display_name="Test Bravo", offset=46, length=11,
                          nickname="Test Bravo"),)
    with pytest.raises(MentionMismatchError, match="covers"):
        verify_mentions(text, bad)


def test_verify_rejects_an_off_by_one_offset() -> None:
    text = "Here's who works next week's Tuesday function\n@Test Alpha: door"
    off_by_one = (PreviewMention(user_id="a", display_name="Test Alpha", offset=47, length=11,
                                 nickname="Test Alpha"),)
    with pytest.raises(MentionMismatchError):
        verify_mentions(text, off_by_one)


def test_verify_rejects_overlapping_loci() -> None:
    text = "@AB@CD"
    overlapping = (
        PreviewMention(user_id="a", display_name="A", offset=0, length=3, nickname="AB"),
        PreviewMention(user_id="b", display_name="B", offset=2, length=4, nickname="B@C"),
    )
    with pytest.raises(MentionMismatchError, match="overlap"):
        verify_mentions(text, overlapping)


def test_verify_rejects_unordered_loci() -> None:
    text = "@Test Alpha\n@Test Bravo"
    unordered = (
        PreviewMention(user_id="b", display_name="Test Bravo", offset=12, length=11,
                       nickname="Test Bravo"),
        PreviewMention(user_id="a", display_name="Test Alpha", offset=0, length=11,
                       nickname="Test Alpha"),
    )
    with pytest.raises(MentionMismatchError, match="out of order"):
        verify_mentions(text, unordered)


def test_verify_rejects_a_locus_running_past_the_end_of_the_message() -> None:
    text = "@Test Alpha"
    over = (PreviewMention(user_id="a", display_name="Test Alpha", offset=0, length=99,
                           nickname="Test Alpha"),)
    with pytest.raises(MentionMismatchError, match="past the end"):
        verify_mentions(text, over)


def test_verify_rejects_a_zero_length_locus() -> None:
    with pytest.raises(MentionMismatchError, match="nonsensical"):
        verify_mentions(
            "@Test Alpha",
            (PreviewMention(user_id="a", display_name="A", offset=0, length=0, nickname=""),),
        )


def test_the_character_limit_constant_matches_groupmes() -> None:
    assert MAX_MESSAGE_CHARS == 1000
