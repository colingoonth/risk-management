"""The agent reply rail's binding, approval, ledger, and transport guarantees.

All destinations and identities are fictional. GroupMe uses the shared stub and
iMessage uses an argv-recording callable; this module never opens a socket,
reads the keychain, or starts ``imsg``.
"""

from __future__ import annotations

import inspect
import sqlite3
import subprocess
from pathlib import Path

import pytest

from risk.db.connection import connect, transaction
from risk.db.schema import ensure_schema
from risk.repos import groupme_groups as groups_repo
from risk.services import agent_reply

from ._groupme_helpers import StubClient

pytestmark = pytest.mark.integration

ALPHA = agent_reply.Channel(
    name="fictional-alpha-chat",
    kind="groupme",
    destination="fictional-alpha-group",
)
BRAVO = agent_reply.Channel(
    name="fictional-bravo-chat",
    kind="groupme",
    destination="fictional-bravo-group",
)


def _register_group(db: sqlite3.Connection, channel: agent_reply.Channel) -> None:
    with transaction(db):
        groups_repo.upsert(
            db,
            slug=channel.destination,
            groupme_id=f"{channel.destination}-placeholder-id",
            label=channel.name,
        )


def _send_groupme(
    db: sqlite3.Connection,
    client: StubClient,
    *,
    identity: agent_reply.AgentIdentity,
    channel: agent_reply.Channel,
    body: str,
    approved: bool,
) -> agent_reply.ReplyReceipt:
    return agent_reply.send(
        db,
        agent=identity,
        channel=channel,
        body=body,
        approved=approved,
        transports={"groupme": agent_reply.GroupMeTransport(client)},
    )


def test_agent_bound_to_channel_a_is_refused_for_registered_channel_b(db) -> None:
    _register_group(db, ALPHA)
    _register_group(db, BRAVO)
    identity = agent_reply.AgentIdentity("fictional-agent-alpha", ALPHA)
    client = StubClient()

    with pytest.raises(agent_reply.ChannelBindingError, match="bound.*alpha.*bravo"):
        _send_groupme(
            db,
            client,
            identity=identity,
            channel=BRAVO,
            body="Please check the schedule.",
            approved=True,
        )

    assert client.posted == []
    assert db.execute("SELECT count(*) FROM agent_replies").fetchone()[0] == 0


@pytest.mark.parametrize("body", ["Noted", "noted.", "noted ", "yes", "noted - claude"])
def test_unapproved_body_other_than_literal_noted_is_refused(db, body: str) -> None:
    _register_group(db, ALPHA)
    identity = agent_reply.AgentIdentity("fictional-agent-alpha", ALPHA)
    client = StubClient()

    with pytest.raises(agent_reply.ApprovalRequiredError, match="literal 'noted'"):
        _send_groupme(
            db,
            client,
            identity=identity,
            channel=ALPHA,
            body=body,
            approved=False,
        )

    assert client.posted == []


def test_unattended_noted_is_attributed_and_sent(db) -> None:
    _register_group(db, ALPHA)
    identity = agent_reply.AgentIdentity("fictional-agent-alpha", ALPHA)
    client = StubClient()

    receipt = _send_groupme(
        db,
        client,
        identity=identity,
        channel=ALPHA,
        body="noted",
        approved=False,
    )

    assert receipt.rendered_text == "noted - claude"
    assert client.posted[0]["text"] == "noted - claude"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("I can cover that.", "I can cover that. - claude"),
        ("I can cover that. - claude", "I can cover that. - claude"),
    ],
)
def test_claude_attribution_is_appended_exactly_once(db, body: str, expected: str) -> None:
    _register_group(db, ALPHA)
    identity = agent_reply.AgentIdentity(f"fictional-agent-{len(body)}", ALPHA)
    client = StubClient()

    _send_groupme(
        db,
        client,
        identity=identity,
        channel=ALPHA,
        body=body,
        approved=True,
    )

    assert client.posted[0]["text"] == expected
    assert expected.count(" - claude") == 1


def test_approved_has_no_default() -> None:
    assert inspect.signature(agent_reply.send).parameters["approved"].default is inspect.Parameter.empty


def test_body_over_the_short_chat_cap_is_refused_before_reservation(db) -> None:
    _register_group(db, ALPHA)
    identity = agent_reply.AgentIdentity("fictional-agent-alpha", ALPHA)

    with pytest.raises(ValueError, match="cap"):
        _send_groupme(
            db,
            StubClient(),
            identity=identity,
            channel=ALPHA,
            body="x" * (agent_reply.MAX_BODY_CHARS + 1),
            approved=True,
        )

    assert db.execute("SELECT count(*) FROM agent_replies").fetchone()[0] == 0


def test_rate_limit_survives_a_restart_with_a_new_connection(tmp_path: Path) -> None:
    db_path = tmp_path / "agent-rate.db"
    first = connect(db_path)
    ensure_schema(first)
    _register_group(first, ALPHA)
    identity = agent_reply.AgentIdentity("fictional-agent-alpha", ALPHA)
    client = StubClient()
    for index in range(agent_reply.RATE_LIMIT_MAX_SENDS):
        _send_groupme(
            first,
            client,
            identity=identity,
            channel=ALPHA,
            body=f"Approved reply {index}",
            approved=True,
        )
    first.close()

    restarted = connect(db_path)
    ensure_schema(restarted)
    try:
        with pytest.raises(agent_reply.RateLimitError, match="rolling"):
            _send_groupme(
                restarted,
                client,
                identity=identity,
                channel=ALPHA,
                body="One reply too many",
                approved=True,
            )
        assert len(client.posted) == agent_reply.RATE_LIMIT_MAX_SENDS
    finally:
        restarted.close()


class SimulatedProcessCrash(BaseException):
    pass


class CrashBeforeResultClient(StubClient):
    def post_message(self, group_id, text, *, mentions=(), source_guid=None):
        row = self.ledger_conn.execute(
            "SELECT state FROM agent_replies ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row is not None and row["state"] == "pending"
        raise SimulatedProcessCrash


def test_crash_after_reservation_blocks_send_after_restart(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "agent-crash.db"
    first = connect(db_path)
    ensure_schema(first)
    _register_group(first, ALPHA)
    identity = agent_reply.AgentIdentity("fictional-agent-alpha", ALPHA)
    crashing = CrashBeforeResultClient()
    crashing.ledger_conn = first

    with pytest.raises(SimulatedProcessCrash):
        _send_groupme(
            first,
            crashing,
            identity=identity,
            channel=ALPHA,
            body="Approved but interrupted",
            approved=True,
        )
    assert first.execute("SELECT state FROM agent_replies").fetchone()[0] == "pending"
    first.close()

    restarted = connect(db_path)
    ensure_schema(restarted)
    safe_client = StubClient()
    try:
        with pytest.raises(agent_reply.UnsettledReplyError, match="reconcile"):
            _send_groupme(
                restarted,
                safe_client,
                identity=identity,
                channel=ALPHA,
                body="Do not blindly repeat",
                approved=True,
            )
        assert safe_client.posted == []
        assert restarted.execute("SELECT count(*) FROM agent_replies").fetchone()[0] == 1
    finally:
        restarted.close()


def test_persisted_identity_binding_cannot_be_changed_after_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "agent-binding.db"
    first = connect(db_path)
    ensure_schema(first)
    _register_group(first, ALPHA)
    _register_group(first, BRAVO)
    client = StubClient()
    _send_groupme(
        first,
        client,
        identity=agent_reply.AgentIdentity("fictional-agent-alpha", ALPHA),
        channel=ALPHA,
        body="First channel",
        approved=True,
    )
    first.close()

    restarted = connect(db_path)
    ensure_schema(restarted)
    rebound = agent_reply.AgentIdentity("fictional-agent-alpha", BRAVO)
    try:
        with pytest.raises(agent_reply.ChannelBindingError, match="persistently bound"):
            _send_groupme(
                restarted,
                client,
                identity=rebound,
                channel=BRAVO,
                body="Different channel",
                approved=True,
            )
        assert len(client.posted) == 1
    finally:
        restarted.close()


def test_imessage_passes_text_as_one_argv_element_without_a_shell(db) -> None:
    calls: list[tuple[object, dict[str, object]]] = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        if "chats" in argv:
            listing = (
                '{"id": 7, "identifier": "chat-fictional-42", "name": "Fictional"}\n'
                '{"id": 9, "identifier": "chat-fictional-99", "name": "Other"}\n'
            )
            return subprocess.CompletedProcess(argv, 0, stdout=listing, stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    channel = agent_reply.Channel(
        name="fictional-imessage-chat",
        kind="imessage",
        destination="chat-fictional-42",
    )
    identity = agent_reply.AgentIdentity("fictional-imessage-agent", channel)
    body = "Approved $(touch never) ; still one argument"

    receipt = agent_reply.send(
        db,
        agent=identity,
        channel=channel,
        body=body,
        approved=True,
        transports={"imessage": agent_reply.IMessageTransport(runner=runner)},
    )

    assert receipt.rendered_text == f"{body} - claude"
    send_calls = [c for c in calls if "send" in c[0]]
    assert len(send_calls) == 1
    argv, kwargs = send_calls[0]
    assert isinstance(argv, list)
    # The DURABLE identifier is resolved to the row id imsg wants; the body is
    # still exactly one argv element, shell metacharacters and all.
    assert argv == [
        agent_reply.IMSG_BIN,
        "send",
        "--chat-id",
        "7",
        "--text",
        f"{body} - claude",
    ]
    assert kwargs["shell"] is False


def test_imessage_refuses_when_no_chat_matches_the_identifier(db) -> None:
    """A row id is not stable. Guessing one addresses whatever chat now holds
    that number, which is how a message meant for one group reaches another."""

    def runner(argv, **kwargs):
        if "chats" in argv:
            listing = '{"id": 7, "identifier": "some-other-chat", "name": "Other"}\n'
            return subprocess.CompletedProcess(argv, 0, stdout=listing, stderr="")
        raise AssertionError("must not send when the destination did not resolve")

    channel = agent_reply.Channel(
        name="fictional-missing-chat",
        kind="imessage",
        destination="chat-fictional-42",
    )
    identity = agent_reply.AgentIdentity("fictional-imessage-agent", channel)

    with pytest.raises(agent_reply.DeliveryRejectedError):
        agent_reply.send(
            db,
            agent=identity,
            channel=channel,
            body="Anything",
            approved=True,
            transports={"imessage": agent_reply.IMessageTransport(runner=runner)},
        )

