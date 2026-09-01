"""HTTP client for the GroupMe v3 API.

Three things about this module are load-bearing and none of them are obvious.

**The token comes out of the macOS keychain, and only out of the keychain.**
There is no environment-variable default, no dotfile, no constructor literal.
This repository is public and a GroupMe access token is a full-privilege
credential for a person's entire account — it can read every chat they are in.
The one supported source is::

    /usr/bin/security find-generic-password -a "$USER" -s groupme-access-token -w

ABSOLUTE PATH, fixed argv, no shell, minimal environment. This process is meant
to run unattended from a LaunchAgent, and a LaunchAgent inherits a sparse
environment that no login shell has sanitised. Resolving ``security`` through
``PATH`` there means anything earlier on that PATH — a stale ``/usr/local/bin``,
a directory some installer left group-writable — receives the keychain request
and, one ``print`` later, the token. ``/usr/bin/security`` is not tidiness; it is
the only lookup that cannot be redirected.

The token never appears in argv (``-w`` writes it to stdout), never in a log
line, never in an exception message, and is never placed in the environment of
any child process.

**The token travels in the ``X-Access-Token`` header, never in the URL.** GroupMe
accepts ``?token=`` and it works, which is the problem: a query string ends up in
shell history, in proxy logs, and in the traceback of any exception that carries
the URL. Every error raised here is built from method + path, not from the full
URL, for the same reason.

**Topics are not groups.** A topic (subgroup) accepts messages and message reads
at its own id, and nothing else — ``GET /groups/{topic}`` and
``POST /groups/{topic}/members/add`` both 404. Membership lives on the PARENT
group. The method signatures below name their argument ``parent_id`` or
``group_id`` accordingly, so a caller that mixes them up is doing so in plain
sight rather than discovering it against the live chapter chat.

Transport is injected so tests never touch the network; see ``Transport``.
"""

from __future__ import annotations

import getpass
import json
import os
import subprocess
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, urlencode

API_BASE = "https://api.groupme.com/v3"
KEYCHAIN_SERVICE = "groupme-access-token"
SECURITY_BIN = "/usr/bin/security"
"""Absolute path. Under a LaunchAgent, PATH is attacker-influenced."""
AUTH_HEADER = "X-Access-Token"
DEFAULT_TIMEOUT_S = 15.0
MESSAGE_PAGE_LIMIT = 100


class GroupMeError(RuntimeError):
    """The API said no, or the network did.

    A ``RuntimeError`` rather than the service layer's usual
    LookupError/ValueError vocabulary: those two mean "your data is wrong", and
    an API layer mapping them to 404/400 would turn "GroupMe is down" into "that
    event does not exist".
    """


class GroupMeAuthError(GroupMeError):
    """No usable token, or GroupMe rejected the one we have. Definitely not sent."""


class GroupMeAmbiguousError(GroupMeError):
    """The request was in flight when the answer was lost.

    A timeout, a reset connection, a 5xx. The message MAY have posted. This is a
    distinct type because the outbound ledger has to treat it differently from
    every other failure: a definite failure is retryable, and this is not — a
    blind retry here is a coin flip whose losing side is a duplicate
    announcement to the whole chapter. It settles as ``unknown`` and waits for
    ``risk groupme reconcile`` to read the chat back.
    """


# ---------------------------------------------------------------------------
# Credential
# ---------------------------------------------------------------------------


def read_token_from_keychain(
    *, service: str = KEYCHAIN_SERVICE, account: str | None = None
) -> str:
    """Fetch the access token from the macOS keychain.

    Invoked at :data:`SECURITY_BIN` with a fixed argv, ``shell=False`` and a
    minimal environment — see the module docstring for why the absolute path is
    load-bearing rather than fastidious.

    Raises ``GroupMeAuthError`` naming the command that stores a token: the
    failure a chair actually hits is "I never put one there", and the fix should
    not require reading this file. The message is built from the service and
    account names only, never from stdout — stdout is the secret.
    """
    who = account or os.environ.get("USER") or getpass.getuser()
    # A deliberately tiny environment. HOME is required — it is how `security`
    # finds the login keychain. PATH is pinned even though the binary is
    # absolute, so nothing `security` may itself exec inherits a poisoned one.
    env = {"HOME": str(Path.home()), "PATH": "/usr/bin:/bin"}
    try:
        proc = subprocess.run(
            [SECURITY_BIN, "find-generic-password", "-a", who, "-s", service, "-w"],
            capture_output=True,
            text=True,
            check=False,
            shell=False,
            env=env,
        )
    except FileNotFoundError as exc:  # pragma: no cover — non-macOS
        raise GroupMeAuthError(
            f"{SECURITY_BIN} not found: the GroupMe token is read from the macOS keychain"
        ) from exc
    if proc.returncode != 0:
        # stderr is deliberately NOT echoed. It is normally a one-line "could
        # not be found", but it is a channel out of a process holding a
        # credential and this message reaches logs and tracebacks.
        raise GroupMeAuthError(
            f"no keychain item {service!r} for account {who!r}. Store one with:\n"
            f'  /usr/bin/security add-generic-password -a "$USER" -s {service} -w'
        )
    token = proc.stdout.strip()
    if not token:
        raise GroupMeAuthError(f"keychain item {service!r} is empty")
    return token


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    body: bytes


class Transport(Protocol):
    """One HTTP round trip. The seam every test substitutes."""

    def __call__(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
    ) -> HttpResponse: ...


def urllib_transport(
    method: str, url: str, *, headers: Mapping[str, str], body: bytes | None
) -> HttpResponse:
    """Stdlib transport. No third-party HTTP dependency is added for this."""
    if not url.startswith("https://"):
        raise GroupMeError("refusing to send an access token over a non-https URL")
    req = urllib.request.Request(url, data=body, method=method)
    for key, value in headers.items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT_S) as resp:
            return HttpResponse(status=int(resp.status), body=resp.read())
    except urllib.error.HTTPError as exc:
        return HttpResponse(status=int(exc.code), body=exc.read())
    except TimeoutError as exc:
        raise GroupMeAmbiguousError(
            f"{method} timed out after {DEFAULT_TIMEOUT_S}s — delivery is unknown"
        ) from exc
    except urllib.error.URLError as exc:
        # The request left this process. Whether it arrived is not knowable from
        # here, and guessing "it didn't" is how a duplicate gets posted.
        raise GroupMeAmbiguousError(f"could not reach GroupMe: {exc.reason}") from exc


# ---------------------------------------------------------------------------
# Payload shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Mention:
    """One ``@`` in a message.

    ``offset``/``length`` are CHARACTER offsets into the message text and are
    computed by the announce builder, not here — this type only carries them to
    the wire. ``user_ids`` and ``loci`` are positional partners in the
    attachment, so the pairing is done in one place (:meth:`GroupMeClient.post_message`)
    and never reassembled by a caller.
    """

    user_id: str
    offset: int
    length: int


@dataclass(frozen=True, slots=True)
class GroupMeMember:
    """A membership row on the PARENT group.

    ``user_id`` identifies the person across every group; ``membership_id`` is
    this group's row for them and is what ``/members/{id}/remove`` takes. They
    are different numbers, they look identical, and swapping them removes the
    wrong person — hence two named fields rather than one ``id``.
    """

    user_id: str
    membership_id: str
    nickname: str


@dataclass(frozen=True, slots=True)
class Topic:
    topic_id: str
    name: str


@dataclass(frozen=True, slots=True)
class InboundMessage:
    message_id: str
    group_id: str
    sender_user_id: str | None
    sender_name: str
    text: str
    created_at: int
    """Epoch seconds, as GroupMe sends it. Converted to ISO by whoever stores it."""
    source_guid: str | None = None
    """Echoed back on every message GroupMe stores.

    This is what makes reconciliation evidence rather than inference: after an
    ambiguous failure, reading the topic back and looking for the guid answers
    "did it post?" exactly, with no timing heuristic involved."""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class GroupMeClient:
    """Thin, explicit wrapper over the endpoints this app actually uses."""

    def __init__(
        self,
        *,
        token_provider: Callable[[], str] = read_token_from_keychain,
        transport: Transport = urllib_transport,
        base_url: str = API_BASE,
    ) -> None:
        self._token_provider = token_provider
        self._transport = transport
        self._base_url = base_url.rstrip("/")
        self._token: str | None = None

    # -- plumbing ---------------------------------------------------------

    def _auth(self) -> str:
        """Read the token once per client, then hold it in memory only.

        Cached because a keychain lookup is a subprocess and a membership sync
        makes dozens of calls; never written anywhere, never logged, and never
        placed in a URL.
        """
        if self._token is None:
            self._token = self._token_provider()
        return self._token

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, object] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> Any:
        url = f"{self._base_url}{path}"
        if params:
            pairs = [(k, str(v)) for k, v in params.items() if v is not None]
            if pairs:
                url = f"{url}?{urlencode(pairs)}"
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {AUTH_HEADER: self._auth(), "Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"

        resp = self._transport(method, url, headers=headers, body=payload)

        if resp.status in (401, 403):
            raise GroupMeAuthError(
                f"GroupMe rejected the access token on {method} {path} ({resp.status})"
            )
        if resp.status == 304:
            # "nothing newer than since_id" on the messages endpoint.
            return None
        if resp.status >= 500:
            raise GroupMeAmbiguousError(
                f"{method} {path} got {resp.status} from GroupMe — delivery is unknown"
            )
        if resp.status >= 400:
            raise GroupMeError(f"{method} {path} failed: {resp.status} {_short(resp.body)}")
        if not resp.body:
            return None
        try:
            doc = json.loads(resp.body)
        except json.JSONDecodeError as exc:
            raise GroupMeError(f"{method} {path} returned non-JSON") from exc
        if isinstance(doc, dict) and "response" in doc:
            return doc["response"]
        return doc

    # -- messages ---------------------------------------------------------

    def post_message(
        self,
        group_id: str,
        text: str,
        *,
        mentions: Sequence[Mention] = (),
        source_guid: str | None = None,
    ) -> str | None:
        """Post to a group OR a topic — both accept their own id here.

        ``source_guid`` is GroupMe's idempotency key. It is generated when the
        caller does not supply one, but a caller that may retry should supply a
        stable one: a retried post with a fresh guid is a SECOND message in the
        chapter's chat, and the chat is the one place a duplicate cannot be
        quietly deleted afterwards.
        """
        message: dict[str, Any] = {
            "source_guid": source_guid or str(uuid.uuid4()),
            "text": text,
        }
        if mentions:
            message["attachments"] = [
                {
                    "type": "mentions",
                    "user_ids": [m.user_id for m in mentions],
                    "loci": [[m.offset, m.length] for m in mentions],
                }
            ]
        resp = self._request(
            "POST", f"/groups/{_seg(group_id)}/messages", body={"message": message}
        )
        if isinstance(resp, dict):
            posted = resp.get("message")
            if isinstance(posted, dict):
                return _opt_str(posted.get("id"))
        return None

    def post_direct_message(
        self,
        recipient_id: str,
        text: str,
        *,
        source_guid: str | None = None,
    ) -> str | None:
        """Send a 1:1 DM to a GroupMe user id.

        The escape hatch for a brother the group itself will not accept.
        GroupMe returns HTTP 400 on ``members/add`` for anyone who has left the
        group before or who has restricted who may add him, and there is no way
        around that from this side — the chair has to add him by hand. Until he
        does, a mention in the group reaches nobody, so the man's shifts are
        sent to him directly instead of being silently dropped.

        A different endpoint and a different envelope from
        :meth:`post_message`: ``/direct_messages`` with a ``direct_message``
        body keyed by ``recipient_id``, not ``/groups/{id}/messages``. Deliberately
        NO mentions parameter — an @ in a 1:1 chat has nobody to notify that the
        message has not already notified.

        GROUPME CURRENTLY REFUSES THIS FOR USER TOKENS. Measured 2026-08-31
        against the live account, not read in a doc:

            GET  /users/me           200
            GET  /chats              200   (DM reads are fine)
            POST /groups/{id}/messages   201   (group posts are fine)
            POST /direct_messages    403   — to five different brothers AND to
                                            the token's own user id

        A self-DM failing the same way is what rules out "those recipients
        refuse" and leaves "the endpoint is closed". The method is kept because
        it matches the documented API and costs nothing if GroupMe reopens it;
        callers must treat 403 as "deliver this by hand", not as a bad token.
        """
        resp = self._request(
            "POST",
            "/direct_messages",
            body={
                "direct_message": {
                    "source_guid": source_guid or str(uuid.uuid4()),
                    "recipient_id": recipient_id,
                    "text": text,
                }
            },
        )
        if isinstance(resp, dict):
            sent = resp.get("direct_message")
            if isinstance(sent, dict):
                return _opt_str(sent.get("id"))
        return None

    def list_messages(
        self, group_id: str, *, after_id: str | None = None, limit: int = MESSAGE_PAGE_LIMIT
    ) -> list[InboundMessage]:
        """Messages AFTER a cursor, oldest-first. A topic id works here, and a
        topic id is the only thing that works for reading a topic.

        ``after_id``, NOT ``since_id``, and the difference is the whole reason
        this signature is spelled out. Both take a message id and both look like
        "give me what I have not seen", but:

        * ``after_id`` returns the page immediately FOLLOWING the cursor, in
          ascending order. Paging it walks the backlog forwards.
        * ``since_id`` returns the MOST RECENT messages instead. Ask it for
          anything older than one page and it hands back the newest page and
          silently drops the middle.

        The failure that distinction causes is quiet and total: a laptop that
        sleeps overnight wakes to two hundred unread messages, and a poller built
        on ``since_id`` forwards the last hundred and loses the rest with no
        error anywhere. Nothing in the response says a gap happened.

        With no cursor at all GroupMe returns the most recent page — which is
        what a first poll and a reconciliation both want, and neither of them
        needs the ordering.
        """
        resp = self._request(
            "GET",
            f"/groups/{_seg(group_id)}/messages",
            params={"after_id": after_id, "limit": limit},
        )
        if not isinstance(resp, dict):
            return []
        raw = resp.get("messages")
        if not isinstance(raw, list):
            return []
        out: list[InboundMessage] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            out.append(
                InboundMessage(
                    message_id=str(item.get("id", "")),
                    group_id=str(item.get("group_id", group_id)),
                    sender_user_id=_opt_str(item.get("user_id")),
                    sender_name=str(item.get("name", "")),
                    text=item.get("text") or "",
                    created_at=int(item.get("created_at", 0)),
                    source_guid=_opt_str(item.get("source_guid")),
                )
            )
        return out

    def list_messages_after(
        self, group_id: str, *, after_id: str | None = None, limit: int = MESSAGE_PAGE_LIMIT
    ) -> list[InboundMessage]:
        """Explicit alias for :meth:`list_messages`.

        Same call, named so the cursor semantics are unmissable at the call
        site. Not the forwarder's entry point — that is the module-level
        :func:`read_messages_after`, and it deliberately reaches no method on
        this class. This alias is for code that legitimately holds a client and
        wants the cursor named in the call it writes.
        """
        return self.list_messages(group_id, after_id=after_id, limit=limit)

    # -- topics -----------------------------------------------------------

    def list_topics(self, parent_id: str) -> list[Topic]:
        """The day topics under a parent Risk group."""
        resp = self._request("GET", f"/groups/{_seg(parent_id)}/subgroups")
        items = resp if isinstance(resp, list) else []
        return [
            Topic(topic_id=str(i.get("id", "")), name=str(i.get("name", "")))
            for i in items
            if isinstance(i, dict)
        ]

    def get_topic(self, parent_id: str, topic_id: str) -> Topic | None:
        """Topic detail. Only reachable through the parent — ``GET /groups/{topic}`` 404s."""
        resp = self._request("GET", f"/groups/{_seg(parent_id)}/subgroups/{_seg(topic_id)}")
        if not isinstance(resp, dict):
            return None
        return Topic(topic_id=str(resp.get("id", topic_id)), name=str(resp.get("name", "")))

    def delete_message(self, group_id: str, message_id: str) -> None:
        """Remove one message the token's own account posted.

        Lives on ``/conversations``, NOT ``/groups`` — the only write in this
        client that does — and returns an empty body on success. A message
        somebody else posted, or one already gone, comes back 404; callers that
        are cleaning up a batch should treat that as done rather than fatal,
        because the point of the sweep is the end state.
        """
        self._request(
            "DELETE",
            f"/conversations/{_seg(group_id)}/messages/{_seg(message_id)}",
        )

    # -- membership (PARENT group only) -----------------------------------

    def list_members(self, parent_id: str) -> list[GroupMeMember]:
        """Everyone currently in the parent group, with their membership ids."""
        resp = self._request("GET", f"/groups/{_seg(parent_id)}")
        if not isinstance(resp, dict):
            return []
        raw = resp.get("members")
        if not isinstance(raw, list):
            return []
        out: list[GroupMeMember] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            out.append(
                GroupMeMember(
                    user_id=str(item.get("user_id", "")),
                    membership_id=str(item.get("id", "")),
                    nickname=str(item.get("nickname", "")),
                )
            )
        return out

    def add_members(
        self, parent_id: str, members: Sequence[tuple[str, str]]
    ) -> str | None:
        """Add ``(user_id, nickname)`` pairs. Returns GroupMe's ``results_id``.

        This endpoint is ASYNCHRONOUS — a 202 means "queued", not "in the
        group". Callers must not treat the return value as proof anybody was
        added; the next ``list_members`` is the proof.
        """
        if not members:
            return None
        resp = self._request(
            "POST",
            f"/groups/{_seg(parent_id)}/members/add",
            body={"members": [{"user_id": uid, "nickname": nick} for uid, nick in members]},
        )
        if isinstance(resp, dict):
            return _opt_str(resp.get("results_id"))
        return None

    def remove_member(self, parent_id: str, membership_id: str) -> None:
        """Remove by MEMBERSHIP id, which is not the user id. See ``GroupMeMember``."""
        self._request("POST", f"/groups/{_seg(parent_id)}/members/{_seg(membership_id)}/remove")


# ---------------------------------------------------------------------------
# The read seam
#
# `risk.services.groupme_poll` reaches GroupMe through exactly one name in this
# module: `read_messages_after`. Everything about that is deliberate and is
# spelled out in the two docstrings below — it is the reason the poller cannot
# post, and it is where the two halves' shapes are reconciled.
# ---------------------------------------------------------------------------


def message_payload(message: InboundMessage) -> dict[str, Any]:
    """One :class:`InboundMessage` back in the wire shape the poller parses.

    This client's job is to turn GroupMe's JSON into typed values. The poller's
    ``normalize`` was written against that JSON directly, and it is the half
    that must keep working when a payload is malformed — it defends every field
    and skips a message it cannot key, rather than aborting a catch-up with two
    thousand good messages behind it.

    Rather than teach either half the other's vocabulary, the seam translates:
    dataclass in, the same keys GroupMe sends out. ``created_at`` stays epoch
    seconds because that is what ``normalize`` converts; handing it an ISO
    string would work by accident, through the fallback branch meant for a
    payload we could not parse.
    """
    return {
        "id": message.message_id,
        "group_id": message.group_id,
        "user_id": message.sender_user_id,
        "name": message.sender_name,
        "text": message.text,
        "created_at": message.created_at,
        "source_guid": message.source_guid,
    }


_read_client: GroupMeClient | None = None
"""Module-private, and handed to nobody. See :func:`read_messages_after`."""


def _read_only_client() -> GroupMeClient:
    """The process's one read client, built on first use.

    Cached because the token comes out of the keychain through a subprocess and
    a single catch-up can make twenty-five calls; building a client per page
    would mean twenty-five ``security`` invocations for one poll.

    Nothing returns this outside the module. That is the point — see
    :func:`read_messages_after`.
    """
    global _read_client
    if _read_client is None:
        _read_client = GroupMeClient()
    return _read_client


def read_messages_after(
    group_id: str, after_id: str | None = None, *, limit: int = MESSAGE_PAGE_LIMIT
) -> list[dict[str, Any]]:
    """The forwarder's entire reach into GroupMe: one page after a cursor.

    THIS FUNCTION IS THE POLLER'S WHOLE VIEW OF THIS MODULE. It imports this
    module only long enough to pull out this one name, keeps the function and
    drops the module; it is never handed a :class:`GroupMeClient`, and it never
    holds a reference it could reach one through. A bare function has no
    ``post_message`` and no ``add_members`` hanging off it, so the read-only
    guarantee in ``groupme_poll`` is a property of what is in scope there rather
    than a rule somebody has to remember. Keep it that way: anything added here
    that returns the client, or the module, gives that back.

    It also owns the two shape questions the poller must not know about, and
    both of them are the kind that type-check clean and fail at run time:

    * **The cursor is positional here and keyword-only on the client.** The
      poller's contract is ``(group_id, after_id)`` — two positional arguments,
      because that is the whole of what it knows how to ask. ``after_id`` is
      keyword-only on :meth:`GroupMeClient.list_messages` on purpose (it is one
      of two same-shaped cursor arguments with opposite semantics; see that
      method), so the translation happens once, here.
    * **The client returns dataclasses and the poller parses mappings.**
      :func:`message_payload` converts each one back.

    ``after_id`` keeps its meaning exactly: the page IMMEDIATELY FOLLOWING the
    cursor, ascending, and ``None`` means "the most recent page" — the
    bootstrap. See :meth:`GroupMeClient.list_messages` for why the alternative
    silently loses a night.
    """
    page = _read_only_client().list_messages(group_id, after_id=after_id, limit=limit)
    return [message_payload(message) for message in page]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seg(value: str) -> str:
    """Percent-encode a path segment.

    Ids are numeric strings in practice, so this is belt-and-braces — but an id
    that arrives from the database with a stray slash would otherwise silently
    address a different endpoint.
    """
    return quote(str(value), safe="")


def _opt_str(value: object) -> str | None:
    return None if value is None else str(value)


def _short(body: bytes, *, limit: int = 200) -> str:
    """Truncated response body for an error message.

    Bodies are echoed because GroupMe's failure reasons live in them, truncated
    because a full HTML error page in a traceback helps nobody.
    """
    text = body.decode("utf-8", errors="replace").strip()
    return text[:limit] + ("…" if len(text) > limit else "")
