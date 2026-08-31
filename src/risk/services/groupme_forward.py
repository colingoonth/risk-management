"""Append new GroupMe messages to a passive feed file.

The delivery half of the forwarder. The poll half puts rows in
``groupme_inbound``; this drains the ones whose ``forwarded_at`` is still NULL
by appending them, one per line, to a plain text file Colin keeps a ``tail -f``
on beside whatever else he is doing.

THE DESTINATION IS PASSIVE, AND THAT IS THE POINT.
--------------------------------------------------
This used to shell out to ``cmux-say``, the message-bus primitive that TYPES a
line into another cmux surface's input AND PRESSES ENTER. Every line it carried
was written by whoever happened to be in the chapter's GroupMe — a hundred-odd
people, none of them audited, any one of whom could send whatever they liked.

Passing the line as a single ``argv`` element protected the FORWARDING process
and nothing beyond it. argv quoting ends where the keystrokes begin: the bytes
were then typed into somebody else's prompt and submitted there. If that prompt
belonged to a shell, ``$(...)`` and backticks were evaluated by the shell on
submit — which is why sanitising deliberately preserved them, and why preserving
them was safe only in the archive and never at this boundary. If it belonged to
an agent, attacker-authored text arrived as a user turn and was read as
instructions. Neither is a quoting bug that better escaping fixes; the vector is
having an active prompt on the other end at all.

So there is no longer a process on the other end. A file has no input line to
submit into: appending to it starts nothing, prompts nobody, and the worst a
hostile message can do is be read. There is no subprocess in this module, and
there must not be one.

WHAT STILL HAS TO BE SANITISED, AND WHY IT IS NOT BELT-AND-BRACES:

* **Escape sequences and control characters.** A feed is read with ``tail -f``,
  and a terminal renders whatever it is handed. A message carrying a clear-screen
  sequence blanks the screen of the person reading it; an OSC sequence retitles their
  window. The file being inert does not make the terminal showing it inert.
* **Bidi overrides and zero-width characters.** They make a line display as
  something other than what it says, which is a lie told with characters rather
  than words.
* **Newlines.** The feed is line-oriented and one line means one message. A
  message containing a newline would otherwise become two feed lines, the second
  of which the sender writes in full — including the ``" - Name`` suffix. That
  is message forgery: anybody in the chat could put words in anybody's mouth.

Within those bounds the words are VERBATIM — Colin wants the real message, not a
paraphrase of it, and the archive in the database keeps the original bytes
untouched regardless.

FAILING SOFT IS STILL THE POINT. A feed on an external disk that is not mounted,
in a directory that has been moved, or on a volume that has filled up is not an
emergency and may not take down the poll loop — a crash here would stop the
120-second cycle from ever reading GroupMe again, so an unwritable file would
silently become a missing message archive. Every failure in this module is
caught, reduced to a code, and returned as a value; the messages stay
unforwarded and go out on the next cycle that finds the feed writable.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from risk.db.connection import transaction
from risk.repos import groupme_inbound as inbound_repo

log = logging.getLogger(__name__)

FEED_ENV = "RISK_FORWARD_FEED"
"""Which file the lines are appended to. There is no default and there must not be.

The feed accumulates the chapter's chat in the clear, so where it lands is a
privacy decision and belongs to whoever installs the LaunchAgent — not to a
guess made here, which would put months of other people's messages somewhere
they never chose. With nothing configured the forwarder reports
``feed_not_configured`` and leaves the queue alone; nothing is lost, and the
next cycle after it is set delivers the backlog.
"""

FEED_MODE = 0o600
"""Owner-only, applied when the feed is created.

Same reasoning as the database (``risk.db.connection``): this is other people's
conversation. Only the creating mode is set — an existing file keeps whatever
permissions its owner gave it, because silently re-chmod'ing a file somebody
pointed us at is not this module's call to make.
"""

DEFAULT_FORWARD_LIMIT = 25
"""How many queued messages one cycle will append.

A laptop that slept through a party wakes up to a backlog, and dumping two
hundred lines into a tailed feed in one burst buries whatever the human was
looking at. At 25 per 120-second cycle a backlog drains at 750/hour, which
empties any realistic night in a few minutes while staying readable.
"""

MAX_TEXT_CHARS = 500
"""Cap on the message body in a forwarded line.

GroupMe accepts messages up to a thousand characters and a pasted block can be
most of that. The archive keeps every character; what goes to the feed is capped
so one message cannot push the rest of the evening out of a reader's scrollback.
Truncation is marked, so a shortened line never reads as the whole thing.
"""

MAX_SENDER_CHARS = 60
"""Cap on the display name. A GroupMe nickname is attacker-controlled text like
any other part of the payload, and gets the same treatment."""

ERROR_CODES: frozenset[str] = frozenset(
    {
        "feed_not_configured",
        "feed_unwritable",
        "lease_lost",
    }
)
"""Everything :class:`ForwardResult` may report.

Codes, not messages, for the same reason the poller stores codes: this value is
printed into a log file and rendered on a health surface, and an ``OSError``'s
string carries the path it was working on.
"""

_ANSI_RE = re.compile(
    r"""
    \x1b\[[0-?]*[ -/]*[@-~]        # CSI  — colours, cursor moves, screen clears
    | \x1b\][^\x07\x1b]*(?:\x07|\x1b\\)  # OSC  — window titles, hyperlinks
    | \x1b[@-Z\\-_]                # two-character escapes
    """,
    re.VERBOSE,
)

_CONTROL_RE = re.compile(
    r"[\x00-\x08\x0e-\x1f\x7f-\x9f"      # C0 (bar whitespace), DEL, C1
    r"\u200b-\u200f"                      # zero-width space/joiner, LRM/RLM
    r"\u2028-\u202e"                      # line/para separators, bidi embeds+overrides
    r"\u2066-\u2069]"                     # bidi isolates
)
"""C0 (minus the whitespace the collapse below handles), DEL, C1, zero-width
characters, and the bidi overrides — the last of which can make a line render
as the reverse of what it says."""


class FeedUnavailableError(RuntimeError):
    """The feed could not be written. Carries a code; raised inside this module only."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ForwardResult:
    """What one drain of the queue did.

    ``error_code`` is set instead of raised — see the module docstring. A result
    with ``sent`` non-empty AND ``error_code`` set is normal: the feed became
    unwritable partway through the batch, and the messages before that point
    really are on disk.
    """

    sent: tuple[int, ...]
    remaining: int
    feed: str | None
    """Where the lines went, for a caller that needs to say so to a human.

    A filesystem path, not a code — so unlike ``error_code`` it must NOT be
    printed into the LaunchAgent's log, which lives beside a public repo and
    carries codes and counts only. Nothing does today; ``risk-forwarder``
    reports the code.
    """
    error_code: str | None

    @property
    def ok(self) -> bool:
        return self.error_code is None


def sanitize(value: str, *, max_chars: int) -> str:
    """Make one field of chat text safe to put in a line somebody will read.

    Order matters. Escape SEQUENCES go first, as whole units — deleting the
    lone ESC byte first would leave ``[2J`` behind as visible junk instead of
    removing the clear-screen. Then the remaining control, zero-width and bidi
    characters. Then whitespace collapses to single spaces, which is what keeps
    one message to one line: the feed is line-oriented, so an embedded newline
    would let a sender write a second line in full — suffix and all — and
    attribute it to anybody they chose. Only then is it truncated, so the cap
    counts characters a human will see rather than escape bytes they never
    would.
    """
    without_escapes = _ANSI_RE.sub("", value)
    without_controls = _CONTROL_RE.sub("", without_escapes)
    flattened = " ".join(without_controls.split())
    if len(flattened) > max_chars:
        return flattened[: max_chars - 1].rstrip() + "…"
    return flattened


def format_line(text: str, sender_name: str) -> str:
    """The exact wire format: ``"<message text>" - <sender name>``.

    Colin specified this and nothing else goes on the line: no topic, no
    timestamp, no id. He is reading these beside everything else he is doing,
    and the two facts he needs at a glance are what was said and who said it.

    Both halves are sanitised; neither is otherwise altered. An empty message —
    GroupMe sends one for an image-only post — renders as ``"" - Name``, which
    is the honest rendering of "he posted something that is not words".
    """
    body = sanitize(text, max_chars=MAX_TEXT_CHARS)
    who = sanitize(sender_name, max_chars=MAX_SENDER_CHARS) or "unknown"
    return f'"{body}" - {who}'


def resolve_feed(explicit: str | Path | None = None) -> Path | None:
    """Argument wins, then ``RISK_FORWARD_FEED``, then nothing."""
    if explicit:
        return Path(explicit)
    env = os.environ.get(FEED_ENV, "").strip()
    return Path(env) if env else None


def append_line(line: str, *, feed: Path) -> None:
    """Add one line to the end of the feed, creating it owner-only if needed.

    Opened per message rather than once per drain, and flushed on close, so a
    ``tail -f`` sees each message as it lands and a feed that was rotated or
    removed mid-batch is simply recreated instead of silently swallowing the
    rest of the night. ``O_APPEND`` means two writers — a hand-run overlapping
    the LaunchAgent — interleave whole lines rather than overwriting each other.

    The parent directory is NOT created. A feed path whose directory does not
    exist is a misconfiguration, and quietly materialising a tree somewhere
    unexpected is how other people's messages end up in a place nobody chose.

    Raises :class:`FeedUnavailableError` for every way this goes wrong — no such
    directory, no permission, path is a directory, disk full, volume gone. The
    ``OSError``'s own text is deliberately dropped rather than carried into the
    code: it names the path, and this code ends up in a log file.
    """
    try:
        fd = os.open(feed, os.O_WRONLY | os.O_APPEND | os.O_CREAT, FEED_MODE)
    except OSError as exc:
        raise FeedUnavailableError("feed_unwritable") from exc
    try:
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError as exc:
        raise FeedUnavailableError("feed_unwritable") from exc


def forward_pending(
    conn: sqlite3.Connection,
    *,
    feed: str | Path | None = None,
    limit: int = DEFAULT_FORWARD_LIMIT,
    now: datetime | None = None,
    keepalive: Callable[[], bool] | None = None,
) -> ForwardResult:
    """Drain up to ``limit`` unforwarded messages into the feed, oldest first.

    Each message is stamped ``forwarded_at`` in its OWN transaction, immediately
    after its append returns. Batching the stamps would mean a crash between the
    last write and the commit replays the whole batch into the feed, and stamping
    before the write would mean an unwritable file silently eats them.
    One-at-a-time is the only ordering where each message is delivered at least
    once and re-delivered only if we genuinely do not know whether it landed.

    ``keepalive`` is the poller's lease check, called before every append. A
    drain of 25 messages can take longer than the lease's TTL, and a holder that
    has been superseded must stop writing rather than race the process that took
    over.

    Stops at the first failure. If the feed is unwritable, it is unwritable for
    the rest of the batch too, and 24 further doomed ``open`` calls inside a
    120-second cycle is how a poll loop misses its interval.
    """
    resolved_feed = resolve_feed(feed)
    pending = inbound_repo.list_unforwarded(conn, limit=limit)
    if resolved_feed is None:
        if pending:
            log.warning(
                "groupme forwarder: %d message(s) queued, no feed file configured (%s)",
                len(pending),
                FEED_ENV,
            )
        return ForwardResult(
            sent=(),
            remaining=inbound_repo.count_unforwarded(conn),
            feed=None,
            error_code="feed_not_configured" if pending else None,
        )

    at = (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds")
    sent: list[int] = []
    error_code: str | None = None

    for message in pending:
        if keepalive is not None and not keepalive():
            error_code = "lease_lost"
            break
        line = format_line(message.text, message.sender_name)
        try:
            append_line(line, feed=resolved_feed)
        except FeedUnavailableError as exc:
            error_code = exc.code
            log.warning(
                "groupme forwarder: %s, %d message(s) left queued",
                error_code,
                len(pending) - len(sent),
            )
            break
        with transaction(conn):
            inbound_repo.mark_forwarded(conn, message_id=message.id, forwarded_at=at)
        sent.append(message.id)

    return ForwardResult(
        sent=tuple(sent),
        remaining=inbound_repo.count_unforwarded(conn),
        feed=str(resolved_feed),
        error_code=error_code,
    )
