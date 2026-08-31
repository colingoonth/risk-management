"""Push new GroupMe messages into a cmux surface.

The delivery half of the forwarder. The poll half puts rows in
``groupme_inbound``; this drains the ones whose ``forwarded_at`` is still NULL
by shelling out to ``cmux-say``, the message-bus primitive that types text into
another cmux surface's input and submits it.

THE LINE FORMAT IS FIXED and Colin specified it exactly::

    "<message text>" - <sender name>

Nothing else goes on the line: no topic, no timestamp, no id. He is reading
these in a terminal beside everything else he is doing, and the two facts he
needs at a glance are what was said and who said it.

INBOUND TEXT IS DATA, NEVER INSTRUCTIONS. Everything on that line was typed by
somebody in a group chat, and it is about to be typed into a terminal that is
usually running an agent. So before it goes out: ANSI escape sequences and
control characters are stripped, so a message cannot repaint the screen, move
the cursor, retitle the window, or use bidi overrides to display as something
other than what it says; it is capped, so one paste cannot fill a scrollback;
and it is wrapped in quotes and passed as a single ``argv`` element to a
subprocess with no shell, so there is no word-splitting and nothing to escape
out of. Within those bounds the words are VERBATIM — Colin wants the real
message, not a paraphrase of it, and the archive in the database keeps the
original bytes untouched regardless.

FAILING SOFT IS THE POINT. cmux is a program on a laptop; it is not running
most of the day, the surface id changes when he rearranges his workspace, and
the binary lives in ``~/.local/bin`` which a LaunchAgent's PATH does not
include. None of that is an emergency and none of it may take down the poll
loop — a crash here would stop the 120-second cycle from ever reading GroupMe
again, so a missing terminal would silently become a missing message archive.
Every failure in this module is caught, reduced to a code, and returned as a
value; the messages stay unforwarded and go out on the next cycle that finds
cmux alive.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from risk.db.connection import transaction
from risk.repos import groupme_inbound as inbound_repo

log = logging.getLogger(__name__)

SURFACE_ENV = "RISK_CMUX_SURFACE"
"""Which cmux surface to type into. There is no default and there must not be.

A wrong surface id is worse than no surface id: it delivers the chapter's risk
traffic into whatever unrelated session happens to hold that number, and marks
the messages forwarded on the way out. With nothing configured the forwarder
reports ``cmux_not_configured`` and leaves the queue alone.
"""

CMUX_SAY_ENV = "RISK_CMUX_SAY"
"""Override for the ``cmux-say`` binary. Defaults to ``~/.local/bin/cmux-say``.

Spelled out rather than resolved off PATH first, because the process that
matters is a LaunchAgent, and launchd hands a job a PATH of
``/usr/bin:/bin:/usr/sbin:/sbin`` — ``~/.local/bin`` is not on it.
"""

DEFAULT_CMUX_SAY = Path.home() / ".local" / "bin" / "cmux-say"

SEND_TIMEOUT_S = 30.0
"""``cmux-say`` takes a per-target spin lock and waits up to 60s for it, so a
send can legitimately block behind another agent. This cap is under that on
purpose: a poll cycle runs every 120 seconds and must finish inside its own
interval, and a message that lost a lock race is not lost — it is still
unforwarded and goes out next cycle.
"""

DEFAULT_FORWARD_LIMIT = 25
"""How many queued messages one cycle will push.

A laptop that slept through a party wakes up to a backlog, and dumping two
hundred lines into a terminal in one burst buries whatever the human was
looking at. At 25 per 120-second cycle a backlog drains at 750/hour, which
empties any realistic night in a few minutes while staying readable.
"""

MAX_TEXT_CHARS = 500
"""Cap on the message body in a forwarded line.

GroupMe accepts messages up to a thousand characters and a pasted block can be
most of that. The archive keeps every character; what goes to the terminal is
capped so one message cannot push the rest of the session out of scrollback.
Truncation is marked, so a shortened line never reads as the whole thing.
"""

MAX_SENDER_CHARS = 60
"""Cap on the display name. A GroupMe nickname is attacker-controlled text like
any other part of the payload, and gets the same treatment."""

ERROR_CODES: frozenset[str] = frozenset(
    {
        "cmux_not_configured",
        "cmux_binary_missing",
        "cmux_not_executable",
        "cmux_timeout",
        "cmux_send_failed",
        "cmux_spawn_failed",
        "lease_lost",
    }
)
"""Everything :class:`ForwardResult` may report.

Codes, not messages, for the same reason the poller stores codes: this value is
printed into a log file and rendered on a health surface, and an exception
string from a subprocess carries paths and whatever the child wrote to stderr.
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


class CmuxUnavailableError(RuntimeError):
    """cmux could not be reached. Carries a code; raised inside this module only."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ForwardResult:
    """What one drain of the queue did.

    ``error_code`` is set instead of raised — see the module docstring. A result
    with ``sent`` non-empty AND ``error_code`` set is normal: cmux went away
    partway through the batch, and the messages before that point really did
    land.
    """

    sent: tuple[int, ...]
    remaining: int
    target: str | None
    error_code: str | None

    @property
    def ok(self) -> bool:
        return self.error_code is None


def sanitize(value: str, *, max_chars: int) -> str:
    """Make one field of chat text safe to type into a terminal.

    Order matters. Escape SEQUENCES go first, as whole units — deleting the
    lone ESC byte first would leave ``[2J`` behind as visible junk instead of
    removing the clear-screen. Then the remaining control, zero-width and bidi
    characters. Then whitespace collapses to single spaces, which is what turns
    a multi-line message into something ``cmux-say`` can carry: it types the
    string and presses enter, so an embedded newline would submit the message
    half-typed and leave the remainder sitting in somebody's prompt. Only then
    is it truncated, so the cap counts characters a human will see rather than
    escape bytes they never would.
    """
    without_escapes = _ANSI_RE.sub("", value)
    without_controls = _CONTROL_RE.sub("", without_escapes)
    flattened = " ".join(without_controls.split())
    if len(flattened) > max_chars:
        return flattened[: max_chars - 1].rstrip() + "…"
    return flattened


def format_line(text: str, sender_name: str) -> str:
    """The exact wire format: ``"<message text>" - <sender name>``.

    Both halves are sanitised; neither is otherwise altered. An empty message —
    GroupMe sends one for an image-only post — renders as ``"" - Name``, which
    is the honest rendering of "he posted something that is not words".
    """
    body = sanitize(text, max_chars=MAX_TEXT_CHARS)
    who = sanitize(sender_name, max_chars=MAX_SENDER_CHARS) or "unknown"
    return f'"{body}" - {who}'


def resolve_target(explicit: str | None = None) -> str | None:
    """Argument wins, then ``RISK_CMUX_SURFACE``, then nothing."""
    if explicit:
        return explicit
    env = os.environ.get(SURFACE_ENV, "").strip()
    return env or None


def resolve_cmux_say(explicit: str | Path | None = None) -> Path:
    """Argument wins, then ``RISK_CMUX_SAY``, then ``~/.local/bin/cmux-say``,
    then whatever is on PATH."""
    if explicit:
        return Path(explicit)
    env = os.environ.get(CMUX_SAY_ENV, "").strip()
    if env:
        return Path(env)
    if DEFAULT_CMUX_SAY.exists():
        return DEFAULT_CMUX_SAY
    found = shutil.which("cmux-say")
    return Path(found) if found else DEFAULT_CMUX_SAY


def send_line(
    line: str,
    *,
    target: str,
    cmux_say: Path,
    timeout: float = SEND_TIMEOUT_S,
) -> None:
    """One ``cmux-say <surface> <line>`` call.

    ``argv`` list, no shell: the line is one element and reaches ``cmux-say``
    exactly as built, so there is no quoting to get wrong and nothing a message
    could break out of.

    Raises :class:`CmuxUnavailableError` with a code for every way this goes wrong —
    binary missing, not executable, cmux not running (non-zero exit), or wedged
    (timeout). The child's own output is deliberately discarded rather than
    quoted into the error: it is whatever cmux felt like printing, and this code
    ends up in a log file.
    """
    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
            [str(cmux_say), target, line],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise CmuxUnavailableError("cmux_binary_missing") from exc
    except PermissionError as exc:
        raise CmuxUnavailableError("cmux_not_executable") from exc
    except subprocess.TimeoutExpired as exc:
        raise CmuxUnavailableError("cmux_timeout") from exc
    except OSError as exc:  # pragma: no cover — defensive
        raise CmuxUnavailableError("cmux_spawn_failed") from exc
    if proc.returncode != 0:
        raise CmuxUnavailableError("cmux_send_failed")


def forward_pending(
    conn: sqlite3.Connection,
    *,
    target: str | None = None,
    cmux_say: str | Path | None = None,
    limit: int = DEFAULT_FORWARD_LIMIT,
    now: datetime | None = None,
    timeout: float = SEND_TIMEOUT_S,
    keepalive: Callable[[], bool] | None = None,
) -> ForwardResult:
    """Drain up to ``limit`` unforwarded messages into cmux, oldest first.

    Each message is stamped ``forwarded_at`` in its OWN transaction, immediately
    after its send returns. Batching the stamps would mean a crash between the
    last send and the commit replays the whole batch into the terminal, and
    stamping before the send would mean a cmux that is down silently eats them.
    One-at-a-time is the only ordering where each message is delivered at least
    once and re-delivered only if we genuinely do not know whether it landed.

    ``keepalive`` is the poller's lease check, called before every send. A drain
    of 25 messages can take longer than the lease's TTL, and a holder that has
    been superseded must stop writing rather than race the process that took
    over.

    Stops at the first failure. If cmux is gone, it is gone for the rest of the
    batch too, and 24 further doomed subprocess spawns inside a 120-second
    cycle is how a poll loop misses its interval.
    """
    resolved_target = resolve_target(target)
    pending = inbound_repo.list_unforwarded(conn, limit=limit)
    if resolved_target is None:
        if pending:
            log.warning(
                "groupme forwarder: %d message(s) queued, no cmux surface configured (%s)",
                len(pending),
                SURFACE_ENV,
            )
        return ForwardResult(
            sent=(),
            remaining=inbound_repo.count_unforwarded(conn),
            target=None,
            error_code="cmux_not_configured" if pending else None,
        )

    binary = resolve_cmux_say(cmux_say)
    at = (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds")
    sent: list[int] = []
    error_code: str | None = None

    for message in pending:
        if keepalive is not None and not keepalive():
            error_code = "lease_lost"
            break
        line = format_line(message.text, message.sender_name)
        try:
            send_line(line, target=resolved_target, cmux_say=binary, timeout=timeout)
        except CmuxUnavailableError as exc:
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
        target=resolved_target,
        error_code=error_code,
    )
