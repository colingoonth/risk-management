"""The only outbound reply path available to an automated chat agent.

The rail binds an :class:`AgentIdentity` to exactly one :class:`Channel`, adds
visible ``- claude`` attribution, enforces human approval and a durable rolling
rate limit, and records a committed ``pending`` reservation before a transport
is invoked. A process that dies after reservation therefore cannot restart with
a fresh budget or blindly repeat a delivery whose outcome is unknown.

GroupMe destinations are registered ``groupme_groups.slug`` values. iMessage
destinations are chat identifiers and are sent with an argv list beginning at
the absolute :data:`IMSG_BIN` path. The message is one argv element,
``shell=False`` is explicit, and no command string is ever constructed. This is
intentional: the chair's shell-level deny for the literal ``imsg send`` command
must remain effective, while this audited Python rail is the sole permitted
subprocess path. Do not "simplify" it into a shell command.

Agents call only :func:`send`. Transport selection is a coordinator concern, so
supporting another channel kind means registering another transport, not editing
agent code.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from risk.db.connection import transaction
from risk.repos import groupme_groups as groups_repo
from risk.services.groupme import GroupMeAmbiguousError, GroupMeError

ATTRIBUTION = " - claude"
UNATTENDED_BODY = "noted"
MAX_BODY_CHARS = 500
RATE_LIMIT_MAX_SENDS = 5
RATE_LIMIT_WINDOW = timedelta(minutes=10)
IMSG_BIN = "/opt/homebrew/bin/imsg"
IMESSAGE_TIMEOUT_SECONDS = 15.0


class AgentReplyError(RuntimeError):
    """Base class for a reply refused or not known to have been delivered."""


class ChannelBindingError(AgentReplyError):
    """An agent tried to target a channel other than its one binding."""


class ApprovalRequiredError(AgentReplyError):
    """An unattended caller tried to send something substantive."""


class UnknownChannelKindError(AgentReplyError):
    """No audited transport is registered for the channel's kind."""


class RateLimitError(AgentReplyError):
    """The destination has exhausted its durable rolling-window budget."""


class UnsettledReplyError(AgentReplyError):
    """A prior delivery may have happened and must be reconciled first."""


class DeliveryRejectedError(AgentReplyError):
    """The transport established that the message was not delivered."""


class DeliveryAmbiguousError(AgentReplyError):
    """The transport cannot establish whether the message was delivered."""


@dataclass(frozen=True, slots=True)
class Channel:
    """One named outbound destination.

    ``destination`` is a registered GroupMe slug for ``groupme`` and a chat
    identifier for ``imessage``. Names and destinations must already be in
    canonical form so whitespace cannot create a second apparent channel.
    """

    name: str
    kind: str
    destination: str

    def __post_init__(self) -> None:
        for field, value in (
            ("channel name", self.name),
            ("channel kind", self.kind),
            ("channel destination", self.destination),
        ):
            if not value or value != value.strip():
                raise ValueError(f"{field} must be non-empty and contain no outer whitespace")


@dataclass(frozen=True, slots=True)
class AgentIdentity:
    """Trusted coordinator configuration for one agent and exactly one channel."""

    name: str
    channel: Channel

    def __post_init__(self) -> None:
        if not self.name or self.name != self.name.strip():
            raise ValueError("agent name must be non-empty and contain no outer whitespace")


@dataclass(frozen=True, slots=True)
class ReplyReceipt:
    ledger_id: int
    agent_id: str
    channel_name: str
    message_id: str | None
    rendered_text: str
    sent_at: str


class ReplyTransport(Protocol):
    """One audited delivery mechanism, registered under a channel-kind name."""

    def __call__(
        self,
        conn: sqlite3.Connection,
        channel: Channel,
        text: str,
        source_guid: str,
    ) -> str | None: ...


@dataclass(frozen=True, slots=True)
class GroupMeTransport:
    """Resolve a registered slug and deliver through an injected GroupMe client."""

    client: object

    def __call__(
        self,
        conn: sqlite3.Connection,
        channel: Channel,
        text: str,
        source_guid: str,
    ) -> str | None:
        group = groups_repo.get_by_slug(conn, channel.destination)
        if group is None:
            raise DeliveryRejectedError(
                f"no GroupMe group registered as {channel.destination!r}"
            )
        post_message = getattr(self.client, "post_message", None)
        if not callable(post_message):
            raise TypeError("the GroupMe transport needs a client with post_message()")
        try:
            result = post_message(group.groupme_id, text, source_guid=source_guid)
        except GroupMeAmbiguousError as exc:
            raise DeliveryAmbiguousError(str(exc)) from exc
        except GroupMeError as exc:
            raise DeliveryRejectedError(str(exc)) from exc
        return result if isinstance(result, str) else None


ProcessRunner = Callable[..., subprocess.CompletedProcess[str]]


class IMessageTransport:
    """Deliver with the absolute ``imsg`` binary and an argv-only subprocess."""

    def __init__(
        self,
        *,
        runner: ProcessRunner = subprocess.run,
        binary: str = IMSG_BIN,
    ) -> None:
        if not Path(binary).is_absolute():
            raise ValueError("the iMessage binary path must be absolute")
        self._runner = runner
        self._binary = binary

    def _resolve_chat_id(self, identifier: str) -> str:
        """Turn the chat's DURABLE identifier into the row id ``imsg`` wants.

        ``--chat-id`` takes a row id out of the local Messages database, and a
        row id is not stable: rebuild that database, or move to another Mac, and
        the numbers move. Storing one and sending to it later does not fail — it
        addresses whatever chat now holds that number, which is how a message
        meant for one group lands in another. So the destination we persist is
        the chat's identifier, which does not move, and the row id is looked up
        at send time.

        The lookup is verified rather than trusted: the row's identifier must
        equal the one asked for, exactly. No match is a refusal, never a guess —
        there is no safe fallback when the question is which humans receive this.
        """
        listing = self._runner(
            [self._binary, "chats", "--json"],
            capture_output=True,
            text=True,
            check=False,
            shell=False,
            timeout=IMESSAGE_TIMEOUT_SECONDS,
        )
        if getattr(listing, "returncode", 1) != 0:
            raise DeliveryRejectedError(
                "could not list iMessage chats to resolve the destination"
            )
        for line in (getattr(listing, "stdout", "") or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if str(row.get("identifier", "")) == identifier:
                resolved = row.get("id")
                if resolved is not None:
                    return str(resolved)
                break
        raise DeliveryRejectedError(
            "no iMessage chat matches the configured destination identifier; "
            "refusing to send rather than guess which chat it means"
        )

    def __call__(
        self,
        conn: sqlite3.Connection,
        channel: Channel,
        text: str,
        source_guid: str,
    ) -> str | None:
        del conn, source_guid
        argv = [
            self._binary,
            "send",
            "--chat-id",
            self._resolve_chat_id(channel.destination),
            "--text",
            text,
        ]
        try:
            proc = self._runner(
                argv,
                capture_output=True,
                text=True,
                check=False,
                shell=False,
                timeout=IMESSAGE_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise DeliveryAmbiguousError(
                "the iMessage send timed out; delivery is unknown"
            ) from exc
        except OSError as exc:
            raise DeliveryRejectedError(
                f"could not start the iMessage transport at {self._binary}"
            ) from exc
        if proc.returncode != 0:
            # Do not echo stdout/stderr from a process handling private chat
            # content into logs or a traceback.
            raise DeliveryRejectedError(
                f"the iMessage transport exited with status {proc.returncode}"
            )
        return None


def build_transports(
    groupme_client: object,
    *,
    imessage_runner: ProcessRunner = subprocess.run,
) -> dict[str, ReplyTransport]:
    """Build the two production transport adapters with injectable I/O seams."""
    return {
        "groupme": GroupMeTransport(groupme_client),
        "imessage": IMessageTransport(runner=imessage_runner),
    }


def send(
    conn: sqlite3.Connection,
    *,
    agent: AgentIdentity,
    channel: Channel,
    body: str,
    approved: bool,
    transports: Mapping[str, ReplyTransport],
) -> ReplyReceipt:
    """Validate, reserve, deliver, and settle one agent reply.

    ``approved`` deliberately has no default. With ``False``, the caller body
    must be the exact literal ``noted``; attribution is added only after that
    check. Any exception not explicitly classified as a definite or ambiguous
    transport result leaves the durable row ``pending`` so a restart cannot
    guess that delivery did not happen.
    """
    _enforce_in_process_binding(agent, channel)
    if not isinstance(approved, bool):
        raise TypeError("approved must be an explicit bool")
    if not approved and body != UNATTENDED_BODY:
        raise ApprovalRequiredError(
            f"approved=False permits only the literal {UNATTENDED_BODY!r} body"
        )
    if not body.strip():
        raise ValueError("an agent reply needs a non-empty body")
    if len(body) > MAX_BODY_CHARS:
        raise ValueError(
            f"agent reply body is {len(body)} characters; the cap is {MAX_BODY_CHARS}"
        )
    transport = transports.get(channel.kind)
    if transport is None:
        raise UnknownChannelKindError(
            f"no agent reply transport is registered for channel kind {channel.kind!r}"
        )

    rendered = body if body.endswith(ATTRIBUTION) else f"{body}{ATTRIBUTION}"
    reserved_at = _now()
    row_id, source_guid = _reserve(
        conn,
        agent=agent,
        body=body,
        rendered=rendered,
        approved=approved,
        reserved_at=reserved_at,
    )

    try:
        message_id = transport(conn, channel, rendered, source_guid)
    except DeliveryAmbiguousError as exc:
        _mark_unknown(conn, row_id=row_id, error=str(exc))
        raise
    except DeliveryRejectedError as exc:
        _mark_failed(conn, row_id=row_id, error=str(exc), settled_at=_now())
        raise

    sent_at = _now()
    _mark_sent(conn, row_id=row_id, message_id=message_id, settled_at=sent_at)
    return ReplyReceipt(
        ledger_id=row_id,
        agent_id=agent.name,
        channel_name=channel.name,
        message_id=message_id,
        rendered_text=rendered,
        sent_at=sent_at,
    )


def _enforce_in_process_binding(agent: AgentIdentity, channel: Channel) -> None:
    if channel != agent.channel:
        raise ChannelBindingError(
            f"agent {agent.name!r} is bound to channel {agent.channel.name!r}; "
            f"refusing target {channel.name!r}"
        )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _reserve(
    conn: sqlite3.Connection,
    *,
    agent: AgentIdentity,
    body: str,
    rendered: str,
    approved: bool,
    reserved_at: str,
) -> tuple[int, str]:
    """Commit the binding and reservation before control reaches a transport."""
    channel = agent.channel
    cutoff = (
        datetime.fromisoformat(reserved_at) - RATE_LIMIT_WINDOW
    ).isoformat(timespec="seconds")
    source_guid = str(uuid.uuid4())

    with transaction(conn):
        conn.execute(
            """
            INSERT OR IGNORE INTO agent_reply_bindings
              (agent_id, channel_name, channel_kind, destination, bound_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (agent.name, channel.name, channel.kind, channel.destination, reserved_at),
        )
        binding = conn.execute(
            """
            SELECT channel_name, channel_kind, destination
            FROM agent_reply_bindings
            WHERE agent_id = ?
            """,
            (agent.name,),
        ).fetchone()
        assert binding is not None
        persisted = (
            binding["channel_name"],
            binding["channel_kind"],
            binding["destination"],
        )
        configured = (channel.name, channel.kind, channel.destination)
        if persisted != configured:
            raise ChannelBindingError(
                f"agent {agent.name!r} is persistently bound to channel "
                f"{binding['channel_name']!r}; refusing target {channel.name!r}"
            )

        unsettled = conn.execute(
            """
            SELECT id, state
            FROM agent_replies
            WHERE channel_kind = ? AND destination = ?
              AND state IN ('pending', 'unknown')
            ORDER BY id
            LIMIT 1
            """,
            (channel.kind, channel.destination),
        ).fetchone()
        if unsettled is not None:
            raise UnsettledReplyError(
                f"agent reply ledger row {unsettled['id']} is {unsettled['state']!r} "
                f"for channel {channel.name!r}; reconcile it before another send"
            )

        recent = conn.execute(
            """
            SELECT count(*) AS total
            FROM agent_replies
            WHERE channel_kind = ? AND destination = ? AND reserved_at >= ?
            """,
            (channel.kind, channel.destination, cutoff),
        ).fetchone()
        assert recent is not None
        if int(recent["total"]) >= RATE_LIMIT_MAX_SENDS:
            minutes = int(RATE_LIMIT_WINDOW.total_seconds() // 60)
            raise RateLimitError(
                f"channel {channel.name!r} reached {RATE_LIMIT_MAX_SENDS} sends "
                f"in the rolling {minutes}-minute window"
            )

        cursor = conn.execute(
            """
            INSERT INTO agent_replies
              (agent_id, channel_name, channel_kind, destination, body,
               rendered_text, approved, source_guid, state, reserved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                agent.name,
                channel.name,
                channel.kind,
                channel.destination,
                body,
                rendered,
                int(approved),
                source_guid,
                reserved_at,
            ),
        )
        row_id = cursor.lastrowid
        assert row_id is not None
    return row_id, source_guid


def _mark_sent(
    conn: sqlite3.Connection,
    *,
    row_id: int,
    message_id: str | None,
    settled_at: str,
) -> None:
    with transaction(conn):
        conn.execute(
            """
            UPDATE agent_replies
            SET state = 'sent', settled_at = ?, message_id = ?, last_error = NULL
            WHERE id = ? AND state = 'pending'
            """,
            (settled_at, message_id, row_id),
        )


def _mark_failed(
    conn: sqlite3.Connection,
    *,
    row_id: int,
    error: str,
    settled_at: str,
) -> None:
    with transaction(conn):
        conn.execute(
            """
            UPDATE agent_replies
            SET state = 'failed', settled_at = ?, last_error = ?
            WHERE id = ? AND state = 'pending'
            """,
            (settled_at, error, row_id),
        )


def _mark_unknown(conn: sqlite3.Connection, *, row_id: int, error: str) -> None:
    with transaction(conn):
        conn.execute(
            """
            UPDATE agent_replies
            SET state = 'unknown', last_error = ?
            WHERE id = ? AND state = 'pending'
            """,
            (error, row_id),
        )
