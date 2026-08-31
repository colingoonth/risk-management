"""The forwarded line, and what happens to hostile text on its way to the feed.

Everything here is pure: no database, no subprocess — and "no subprocess" is now
a property of the module under test rather than of these tests. The format is a
thing Colin specified exactly. The sanitising is what keeps a stranger's words
from being read as something other than a quoted message: escapes render in
whatever terminal is tailing the feed, and a newline would let a sender write a
whole extra line and sign it with somebody else's name.
"""

from __future__ import annotations

import subprocess

import pytest

from risk.services import groupme_forward as fwd
from risk.services import groupme_poll as poll


def test_the_line_is_exactly_quote_text_quote_space_dash_space_sender() -> None:
    assert fwd.format_line("need water at the door", "Test Alpha") == (
        '"need water at the door" - Test Alpha'
    )


def test_an_image_only_message_renders_as_empty_quotes() -> None:
    """GroupMe sends null text for a picture. The row still means "he posted"."""
    assert fwd.format_line("", "Test Bravo") == '"" - Test Bravo'


def test_a_multi_line_message_becomes_one_line() -> None:
    """The feed is line-oriented: one line is one message. A newline that
    survived would split one message into two feed lines."""
    line = fwd.format_line("first\nsecond\n\nthird", "Test Alpha")
    assert line == '"first second third" - Test Alpha'
    assert "\n" not in line


def test_a_newline_cannot_forge_a_second_line_in_somebody_elses_name() -> None:
    """The sharp version of the test above, and the reason it is not cosmetic.

    A feed reader attributes each line to the name after the dash. If a message
    could carry a newline it would carry the whole next line too — quotes,
    suffix and all — and anybody in the chapter chat could put words in anybody
    else's mouth. Flattening whitespace is what makes one message one line.
    """
    forged = 'ok" - Test Alpha\n"I am fine to drive'
    line = fwd.format_line(forged, "Test Bravo")
    assert "\n" not in line
    assert line.count("\n") == 0
    assert line.endswith('" - Test Bravo'), "the only attribution is the real sender"


def test_ansi_escape_sequences_are_removed_whole() -> None:
    """Not just the ESC byte — the whole sequence.

    The feed itself is inert, but a terminal tailing it is not: it renders what
    it is handed. Stripping the lone \\x1b would leave ``[2J`` on screen as
    visible junk while still failing to stop anything, so the sequences go as
    units.
    """
    hostile = "\x1b[2J\x1b[31mred\x1b[0m and \x1b]0;retitled\x07done"
    assert fwd.format_line(hostile, "Test Alpha") == '"red and done" - Test Alpha'


def test_bidi_and_zero_width_characters_are_removed() -> None:
    """A right-to-left override makes a line display as the reverse of what it
    says, which is a lie told with characters rather than words."""
    assert fwd.format_line("‮deliver​ now", "Test​Alpha") == (
        '"deliver now" - TestAlpha'
    )


def test_control_characters_cannot_reach_a_reader() -> None:
    assert fwd.format_line("bell\x07back\x08null\x00", "Test Alpha") == (
        '"bellbacknull" - Test Alpha'
    )


def test_a_long_message_is_capped_and_marked() -> None:
    line = fwd.format_line("x" * 5000, "Test Alpha")
    body = line[1 : line.index('" - ')]
    assert len(body) == fwd.MAX_TEXT_CHARS
    assert body.endswith("…"), "a truncated line must not read as the whole message"


def test_a_long_sender_name_is_capped_too() -> None:
    """A nickname is attacker-controlled text like any other part of the payload."""
    line = fwd.format_line("hi", "N" * 500)
    who = line.split('" - ', 1)[1]
    assert len(who) == fwd.MAX_SENDER_CHARS


def test_the_words_themselves_are_verbatim() -> None:
    """Sanitising is not paraphrasing. Punctuation, case, emoji and the quotes
    somebody typed all survive — Colin asked for the real message."""
    original = "Can't find the keys?! ☎️ \"front door\" — 2 mins"
    assert fwd.format_line(original, "Test Alpha") == f'"{original}" - Test Alpha'


def test_a_nameless_sender_still_produces_a_line() -> None:
    assert fwd.format_line("hi", "   ") == '"hi" - unknown'


@pytest.mark.parametrize(
    "code",
    ["feed_not_configured", "feed_unwritable", "lease_lost"],
)
def test_forward_error_codes_are_a_closed_set(code: str) -> None:
    assert code in fwd.ERROR_CODES


def test_the_delivery_half_holds_nothing_that_can_start_a_process() -> None:
    """The structural half of the fix: there is no child process to hand text to.

    Delivery used to shell out to ``cmux-say``, which TYPES a line into another
    surface's input and presses enter — so every message in the chapter's chat
    became keystrokes in somebody's prompt, and a prompt that was a shell
    evaluated the backticks on submit. No amount of sanitising fixes that; only
    having nothing on the other end does. A file append needs no child process,
    so this module should not have one in scope at all.
    """
    from_subprocess = {
        name
        for name, value in vars(fwd).items()
        if value is subprocess or getattr(value, "__module__", None) == "subprocess"
    }
    assert from_subprocess == set()


# --- error classification -------------------------------------------------


class _HttpError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, "auth_rejected"),
        (403, "auth_rejected"),
        (404, "not_found"),
        (408, "network_timeout"),
        (429, "rate_limited"),
        (500, "server_error"),
        (503, "server_error"),
        (418, "client_error"),
    ],
)
def test_http_status_decides_the_code(status: int, expected: str) -> None:
    assert poll.classify_error(_HttpError(status, "boom")) == expected


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (TimeoutError("slow"), "network_timeout"),
        (ConnectionResetError("reset"), "network_unreachable"),
        (ValueError("not json"), "invalid_response"),
        (ModuleNotFoundError("no module"), "client_unavailable"),
        (RuntimeError("who knows"), "unknown_error"),
    ],
)
def test_exception_types_map_onto_the_vocabulary(exc: Exception, expected: str) -> None:
    assert poll.classify_error(exc) == expected


def test_every_classification_is_in_the_closed_vocabulary() -> None:
    for exc in (TimeoutError(), ValueError(), RuntimeError(), _HttpError(429, "x")):
        assert poll.classify_error(exc) in poll.ERROR_CODES


def test_retry_after_is_read_from_the_attribute_and_clamped() -> None:
    exc = _HttpError(429, "slow down")
    exc.retry_after = 90  # type: ignore[attr-defined]
    assert poll.retry_after_seconds(exc) == 90

    exc.retry_after = 10**9  # type: ignore[attr-defined]
    assert poll.retry_after_seconds(exc) == poll.MAX_RETRY_AFTER_SECONDS

    exc.retry_after = "not a number"  # type: ignore[attr-defined]
    assert poll.retry_after_seconds(exc) is None


def test_retry_after_is_read_from_the_header_when_there_is_no_attribute() -> None:
    class _Response:
        headers = {"Retry-After": "45"}

    exc = _HttpError(429, "slow down")
    exc.response = _Response()  # type: ignore[attr-defined]
    assert poll.retry_after_seconds(exc) == 45


def test_message_ids_sort_numerically_not_lexicographically() -> None:
    """"9" is not newer than "10". Getting this wrong parks the cursor behind
    messages already stored and replays them on every cycle, forever."""
    ordered = sorted(["10", "9", "100", "11"], key=poll.message_sort_key)
    assert ordered == ["9", "10", "11", "100"]


def test_a_non_numeric_id_still_orders_deterministically() -> None:
    ordered = sorted(["b", "10", "a"], key=poll.message_sort_key)
    assert ordered == ["10", "a", "b"]
