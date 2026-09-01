"""The HTTP client, driven entirely through an injected fake transport.

Nothing here touches the network or the keychain. The token provider is a lambda
in every test, which is also the point of it being injectable: the real one
shells out to ``/usr/bin/security`` and a test that reached it would either
prompt a human or fail on CI.
"""

from __future__ import annotations

import json

import pytest

from risk.services.groupme import (
    AUTH_HEADER,
    KEYCHAIN_SERVICE,
    SECURITY_BIN,
    GroupMeAmbiguousError,
    GroupMeAuthError,
    GroupMeClient,
    GroupMeError,
    HttpResponse,
    Mention,
)


class FakeTransport:
    """Records every call and replays queued responses."""

    def __init__(self, *responses: HttpResponse) -> None:
        self.queue = list(responses)
        self.calls: list[dict[str, object]] = []

    def __call__(self, method, url, *, headers, body):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "body": json.loads(body) if body else None,
            }
        )
        return self.queue.pop(0) if self.queue else _ok({})


def _ok(payload: object, status: int = 200) -> HttpResponse:
    return HttpResponse(status=status, body=json.dumps({"response": payload}).encode())


def _client(transport: FakeTransport) -> GroupMeClient:
    return GroupMeClient(token_provider=lambda: "test-token", transport=transport)


# ---------------------------------------------------------------------------
# Credential handling
# ---------------------------------------------------------------------------


def test_the_keychain_service_name_and_binary_are_exactly_as_specified() -> None:
    assert KEYCHAIN_SERVICE == "groupme-access-token"
    assert SECURITY_BIN == "/usr/bin/security"


def test_the_token_is_not_read_until_a_request_is_made() -> None:
    """Constructing a client must not shell out — every read-only CLI command
    builds one and most of them never make a call."""
    calls = []

    def provider() -> str:
        calls.append(1)
        return "test-token"

    client = GroupMeClient(token_provider=provider, transport=FakeTransport())
    assert calls == []
    client.list_topics("parent")
    assert calls == [1]


def test_the_token_is_read_once_and_reused() -> None:
    calls = []
    client = GroupMeClient(
        token_provider=lambda: (calls.append(1), "test-token")[1],
        transport=FakeTransport(_ok([]), _ok([])),
    )
    client.list_topics("parent")
    client.list_topics("parent")
    assert len(calls) == 1


def test_the_token_travels_in_the_header_and_never_in_the_url() -> None:
    transport = FakeTransport(_ok([]))
    _client(transport).list_topics("parent-id")
    call = transport.calls[0]
    assert call["headers"][AUTH_HEADER] == "test-token"
    assert "token" not in call["url"]
    assert "test-token" not in call["url"]


def test_a_401_becomes_an_auth_error_that_does_not_leak_the_token() -> None:
    transport = FakeTransport(HttpResponse(status=401, body=b"nope"))
    with pytest.raises(GroupMeAuthError) as exc:
        _client(transport).list_topics("parent")
    assert "test-token" not in str(exc.value)


def test_a_non_https_base_url_is_refused_by_the_real_transport() -> None:
    from risk.services.groupme import urllib_transport

    with pytest.raises(GroupMeError, match="non-https"):
        urllib_transport("GET", "http://example.invalid/x", headers={}, body=None)


# ---------------------------------------------------------------------------
# Posting
# ---------------------------------------------------------------------------


def test_post_message_sends_the_documented_body_shape() -> None:
    transport = FakeTransport(_ok({"message": {"id": "msg-1"}}))
    message_id = _client(transport).post_message(
        "topic-1",
        "tuesday, sep 1\n@Test Alpha: door",
        mentions=[Mention(user_id="1", offset=15, length=11)],
        source_guid="guid-1",
    )
    assert message_id == "msg-1"
    call = transport.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/groups/topic-1/messages")
    message = call["body"]["message"]
    assert message["source_guid"] == "guid-1"
    assert message["attachments"] == [
        {"type": "mentions", "user_ids": ["1"], "loci": [[15, 11]]}
    ]


def test_mentions_pair_user_ids_and_loci_positionally() -> None:
    transport = FakeTransport(_ok({"message": {"id": "m"}}))
    _client(transport).post_message(
        "topic-1",
        "text",
        mentions=[Mention("11", 0, 3), Mention("22", 5, 4), Mention("33", 12, 2)],
    )
    attachment = transport.calls[0]["body"]["message"]["attachments"][0]
    assert attachment["user_ids"] == ["11", "22", "33"]
    assert attachment["loci"] == [[0, 3], [5, 4], [12, 2]]


def test_a_message_without_mentions_carries_no_attachments_key() -> None:
    transport = FakeTransport(_ok({"message": {"id": "m"}}))
    _client(transport).post_message("topic-1", "text")
    assert "attachments" not in transport.calls[0]["body"]["message"]


def test_a_source_guid_is_generated_when_none_is_given() -> None:
    transport = FakeTransport(_ok({"message": {"id": "m"}}))
    _client(transport).post_message("topic-1", "text")
    assert transport.calls[0]["body"]["message"]["source_guid"]


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def test_list_topics_reads_the_parents_subgroups() -> None:
    transport = FakeTransport(_ok([{"id": "t1", "name": "Tuesday"}]))
    topics = _client(transport).list_topics("parent-1")
    assert transport.calls[0]["url"].endswith("/groups/parent-1/subgroups")
    assert [(t.topic_id, t.name) for t in topics] == [("t1", "Tuesday")]


def test_get_topic_goes_through_the_parent() -> None:
    """`GET /groups/{topic}` 404s; detail is only reachable via the parent."""
    transport = FakeTransport(_ok({"id": "t1", "name": "Tuesday"}))
    _client(transport).get_topic("parent-1", "t1")
    assert transport.calls[0]["url"].endswith("/groups/parent-1/subgroups/t1")


def test_list_members_separates_user_id_from_membership_id() -> None:
    transport = FakeTransport(
        _ok({"members": [{"user_id": "u1", "id": "mem1", "nickname": "Test Alpha"}]})
    )
    members = _client(transport).list_members("parent-1")
    assert members[0].user_id == "u1"
    assert members[0].membership_id == "mem1"
    assert members[0].nickname == "Test Alpha"


def test_list_messages_uses_after_id_not_since_id() -> None:
    """``since_id`` returns the NEWEST page, so a poller built on it loses the
    middle of an overnight backlog with no error anywhere."""
    transport = FakeTransport(_ok({"messages": []}))
    _client(transport).list_messages("t1", after_id="m0", limit=100)
    url = transport.calls[0]["url"]
    assert "after_id=m0" in url
    assert "since_id" not in url


def test_list_messages_after_is_the_same_call_under_an_explicit_name() -> None:
    transport = FakeTransport(_ok({"messages": []}), _ok({"messages": []}))
    client = _client(transport)
    client.list_messages("t1", after_id="m0")
    client.list_messages_after("t1", after_id="m0")
    assert transport.calls[0]["url"] == transport.calls[1]["url"]


def test_a_first_poll_with_no_cursor_sends_no_cursor_parameter() -> None:
    transport = FakeTransport(_ok({"messages": []}))
    _client(transport).list_messages("t1")
    assert "after_id" not in transport.calls[0]["url"]


def test_list_messages_carries_the_echoed_source_guid() -> None:
    transport = FakeTransport(
        _ok(
            {
                "messages": [
                    {
                        "id": "m1",
                        "group_id": "t1",
                        "user_id": "u1",
                        "name": "Test Alpha",
                        "text": "hello",
                        "created_at": 1_756_000_000,
                        "source_guid": "guid-1",
                    }
                ]
            }
        )
    )
    messages = _client(transport).list_messages("t1", after_id="m0", limit=50)
    assert messages[0].source_guid == "guid-1"
    assert messages[0].created_at == 1_756_000_000
    assert "after_id=m0" in transport.calls[0]["url"]
    assert "limit=50" in transport.calls[0]["url"]


def test_a_304_from_the_messages_endpoint_is_an_empty_page_not_an_error() -> None:
    transport = FakeTransport(HttpResponse(status=304, body=b""))
    assert _client(transport).list_messages("t1", after_id="m9") == []


# ---------------------------------------------------------------------------
# Membership writes
# ---------------------------------------------------------------------------


def test_add_members_targets_the_parent_and_returns_the_results_id() -> None:
    transport = FakeTransport(_ok({"results_id": "r1"}, status=202))
    results_id = _client(transport).add_members("parent-1", [("u1", "Test Alpha")])
    assert results_id == "r1"
    assert transport.calls[0]["url"].endswith("/groups/parent-1/members/add")
    assert transport.calls[0]["body"] == {
        "members": [{"user_id": "u1", "nickname": "Test Alpha"}]
    }


def test_adding_nobody_makes_no_request_at_all() -> None:
    transport = FakeTransport()
    assert _client(transport).add_members("parent-1", []) is None
    assert transport.calls == []


def test_remove_member_uses_the_membership_id() -> None:
    transport = FakeTransport(_ok({}))
    _client(transport).remove_member("parent-1", "mem-9")
    assert transport.calls[0]["url"].endswith("/groups/parent-1/members/mem-9/remove")


# ---------------------------------------------------------------------------
# Failure classification — the ledger depends on this being right
# ---------------------------------------------------------------------------


def test_a_5xx_is_ambiguous_because_the_message_may_have_posted() -> None:
    transport = FakeTransport(HttpResponse(status=503, body=b"upstream"))
    with pytest.raises(GroupMeAmbiguousError):
        _client(transport).post_message("t1", "text")


def test_a_4xx_is_a_definite_failure_not_an_ambiguous_one() -> None:
    transport = FakeTransport(HttpResponse(status=400, body=b"bad"))
    with pytest.raises(GroupMeError) as exc:
        _client(transport).post_message("t1", "text")
    assert not isinstance(exc.value, GroupMeAmbiguousError)


def test_a_path_segment_with_a_slash_cannot_address_another_endpoint() -> None:
    transport = FakeTransport(_ok({}))
    _client(transport).remove_member("parent-1", "../../evil")
    assert "../.." not in transport.calls[0]["url"]
