"""The /api/groupme routes, with the GroupMe client stubbed via dependency override.

Nothing here opens a socket or reads the keychain: ``get_groupme_client`` is
overridden wholesale, which is why it exists as a dependency rather than being
constructed inline.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from risk.api import create_app
from risk.api.routers.groupme import get_groupme_client
from risk.db.connection import connect, transaction
from risk.db.schema import ensure_schema
from risk.repos import event_types as etypes_repo
from risk.repos import events as events_repo
from risk.repos import groupme_groups as groups_repo
from risk.repos import groupme_identities as identities_repo
from risk.repos import member_statuses as statuses_repo
from risk.repos import members as members_repo
from risk.repos import semesters as semesters_repo
from risk.repos import shift_types as stypes_repo
from risk.repos import shifts as shifts_repo
from risk.services.groupme import GroupMeMember
from tests.integration._groupme_helpers import FRIDAY, STAMP, StubClient

pytestmark = pytest.mark.integration


@pytest.fixture()
def stub() -> StubClient:
    return StubClient(members=[GroupMeMember("user-1", "mem-user-1", "Test Alpha")])


@pytest.fixture()
def client(tmp_path: Path, stub: StubClient) -> Iterator[TestClient]:
    db_path = tmp_path / "api.db"
    _seed(db_path)
    app = create_app(db_path=db_path)
    app.dependency_overrides[get_groupme_client] = lambda: stub
    with TestClient(app) as c:
        yield c


def _seed(db_path: Path) -> None:
    conn = connect(db_path)
    ensure_schema(conn)
    with transaction(conn):
        sem_id = semesters_repo.insert(
            conn, name="FA26", starts_on="2026-08-20", ends_on="2026-12-15"
        )
        conn.execute("UPDATE semesters SET is_current = 1 WHERE id = ?", (sem_id,))
        groups_repo.upsert(
            conn, slug="risk-parent", groupme_id="parent-placeholder", label="Risk"
        )
        groups_repo.upsert(
            conn,
            slug="risk-friday",
            groupme_id="topic-placeholder-5",
            label="Risk Friday",
            parent_slug="risk-parent",
            weekday=5,
        )
        active = statuses_repo.get_by_slug(conn, "active")
        assert active is not None
        alpha = members_repo.insert(
            conn, slug="test-alpha", display_name="Test Alpha", status_id=active.id
        )
        bravo = members_repo.insert(
            conn, slug="test-bravo", display_name="Test Bravo", status_id=active.id
        )
        identities_repo.link(
            conn,
            member_id=alpha,
            groupme_user_id="user-1",
            nickname="Test Alpha",
            confidence="exact",
            linked_at=STAMP,
        )
        etype = etypes_repo.get_by_slug(conn, "mixer")
        assert etype is not None
        event_id = events_repo.insert(
            conn,
            semester_id=sem_id,
            event_type_id=etype.id,
            display_name="Sample Mixer",
            date=FRIDAY,
        )
        for member_id, slug in ((alpha, "door"), (bravo, "bar")):
            stype = stypes_repo.get_by_slug(conn, slug)
            assert stype is not None
            shift_id = shifts_repo.insert_open(
                conn, event_id=event_id, shift_type_id=stype.id, slot_index=0
            )
            shifts_repo.assign(
                conn,
                shift_id=shift_id,
                member_id=member_id,
                effective_pledge_mode_id=None,
                assigned_at=STAMP,
            )
    conn.close()


def _preview(client: TestClient) -> dict:
    r = client.get(
        "/api/groupme/announce-preview",
        params={"on_or_after": FRIDAY, "on_or_before": FRIDAY},
    )
    assert r.status_code == 200
    return r.json()


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


def test_the_preview_renders_the_exact_message_and_its_loci(client: TestClient) -> None:
    body = _preview(client)
    post = body["posts"][0]
    assert post["group_slug"] == "risk-friday"
    assert post["text"] == (
        "friday, sep 4\n"
        "@Test Alpha: door\n"
        "Test Bravo: bar"
    )
    mention = post["mentions"][0]
    assert post["text"][mention["offset"] : mention["offset"] + mention["length"]] == (
        "@Test Alpha"
    )
    assert [u["display_name"] for u in post["unlinked"]] == ["Test Bravo"]


def test_the_preview_carries_a_preview_id_and_a_digest(client: TestClient) -> None:
    body = _preview(client)
    assert body["preview_id"]
    assert len(body["digest"]) == 64


def test_the_preview_makes_no_outbound_post(client: TestClient, stub: StubClient) -> None:
    _preview(client)
    assert stub.posted == []


# ---------------------------------------------------------------------------
# Announce — the four gates
# ---------------------------------------------------------------------------


def test_announce_without_confirm_is_rejected(client: TestClient, stub: StubClient) -> None:
    body = _preview(client)
    r = client.post(
        "/api/groupme/announce",
        json={
            "on_or_after": FRIDAY,
            "on_or_before": FRIDAY,
            "confirm": False,
            "preview_id": body["preview_id"],
            "digest": body["digest"],
        },
    )
    assert r.status_code == 400
    assert "confirm" in r.json()["detail"]
    assert stub.posted == []


def test_announce_without_a_digest_is_rejected_by_validation(
    client: TestClient, stub: StubClient
) -> None:
    """A UI that hardcodes confirm:true and sends nothing else cannot post."""
    r = client.post(
        "/api/groupme/announce",
        json={"on_or_after": FRIDAY, "on_or_before": FRIDAY, "confirm": True},
    )
    assert r.status_code == 422
    assert stub.posted == []


def test_announce_with_a_matching_digest_sends(client: TestClient, stub: StubClient) -> None:
    body = _preview(client)
    r = client.post(
        "/api/groupme/announce",
        json={
            "on_or_after": FRIDAY,
            "on_or_before": FRIDAY,
            "confirm": True,
            "preview_id": body["preview_id"],
            "digest": body["digest"],
        },
    )
    assert r.status_code == 200
    posted = r.json()["posted"]
    assert [p["outcome"] for p in posted] == ["sent"]
    assert len(stub.posted) == 1
    assert stub.posted[0]["group_id"] == "topic-placeholder-5"


def test_a_reassignment_between_preview_and_click_stops_the_send(
    client: TestClient, stub: StubClient, tmp_path: Path
) -> None:
    """The case a plain confirm:true cannot see."""
    body = _preview(client)

    # The chair reassigns a shift in another window while reading the preview.
    conn = connect(tmp_path / "api.db")
    ensure_schema(conn)
    row = conn.execute(
        "SELECT s.id FROM shifts s JOIN shift_types st ON st.id = s.shift_type_id "
        "WHERE st.slug = 'bar'"
    ).fetchone()
    alpha = members_repo.get_by_slug(conn, "test-alpha")
    assert alpha is not None
    with transaction(conn):
        shifts_repo.unassign(conn, shift_id=row["id"])
        shifts_repo.assign(
            conn,
            shift_id=row["id"],
            member_id=alpha.id,
            effective_pledge_mode_id=None,
            assigned_at=STAMP,
        )
    conn.close()

    r = client.post(
        "/api/groupme/announce",
        json={
            "on_or_after": FRIDAY,
            "on_or_before": FRIDAY,
            "confirm": True,
            "preview_id": body["preview_id"],
            "digest": body["digest"],
        },
    )
    assert r.status_code == 400
    assert "changed" in r.json()["detail"]
    assert stub.posted == []


def test_a_forged_digest_is_rejected(client: TestClient, stub: StubClient) -> None:
    body = _preview(client)
    r = client.post(
        "/api/groupme/announce",
        json={
            "on_or_after": FRIDAY,
            "on_or_before": FRIDAY,
            "confirm": True,
            "preview_id": body["preview_id"],
            "digest": "0" * 64,
        },
    )
    assert r.status_code == 400
    assert stub.posted == []


def test_an_outsized_window_is_refused_before_anything_is_sent(
    client: TestClient, stub: StubClient
) -> None:
    r = client.get(
        "/api/groupme/announce-preview",
        params={"on_or_after": "2026-08-20", "on_or_before": "2027-08-20"},
    )
    body = r.json()
    r = client.post(
        "/api/groupme/announce",
        json={
            "on_or_after": "2026-08-20",
            "on_or_before": "2027-08-20",
            "confirm": True,
            "preview_id": body["preview_id"],
            "digest": body["digest"],
        },
    )
    assert r.status_code == 400
    assert "cap is" in r.json()["detail"]
    assert stub.posted == []


def test_announcing_twice_posts_once(client: TestClient, stub: StubClient) -> None:
    for _ in range(2):
        body = _preview(client)
        r = client.post(
            "/api/groupme/announce",
            json={
                "on_or_after": FRIDAY,
                "on_or_before": FRIDAY,
                "confirm": True,
                "preview_id": body["preview_id"],
                "digest": body["digest"],
            },
        )
        assert r.status_code == 200
    assert len(stub.posted) == 1


# ---------------------------------------------------------------------------
# Identities + membership + reply
# ---------------------------------------------------------------------------


def test_identities_reports_linked_and_unlinked(client: TestClient) -> None:
    body = client.get("/api/groupme/identities").json()
    assert body["linked"] == 1
    assert [u["display_name"] for u in body["unlinked"]] == ["Test Bravo"]
    assert body["drift"] == []


def test_identities_reports_a_renamed_account_as_drift(
    client: TestClient, stub: StubClient
) -> None:
    stub.members = [GroupMeMember("user-1", "mem-user-1", "Renamed Entirely")]
    body = client.get("/api/groupme/identities").json()
    # The drift row carries the CURRENT GroupMe nickname — the new name is the
    # half the chair does not already know.
    assert body["drift"] == [
        {"groupme_user_id": "user-1", "nickname": "Renamed Entirely"}
    ]
    assert [b["reason"] for b in body["blocked"]] == ["drifted"]
    assert body["blocked"][0]["display_name"] == "Test Alpha"


def test_membership_plan_carries_its_own_digest(client: TestClient) -> None:
    body = client.get(
        "/api/groupme/membership-plan",
        params={"on_or_after": FRIDAY, "on_or_before": FRIDAY},
    ).json()
    assert body["preview_id"]
    assert len(body["digest"]) == 64


def test_membership_apply_requires_confirm_and_the_digest(
    client: TestClient, stub: StubClient
) -> None:
    plan = client.get(
        "/api/groupme/membership-plan",
        params={"on_or_after": FRIDAY, "on_or_before": FRIDAY},
    ).json()
    unconfirmed = client.post(
        "/api/groupme/membership/apply",
        json={
            "on_or_after": FRIDAY,
            "on_or_before": FRIDAY,
            "confirm": False,
            "preview_id": plan["preview_id"],
            "digest": plan["digest"],
        },
    )
    assert unconfirmed.status_code == 400
    assert stub.removed == []

    confirmed = client.post(
        "/api/groupme/membership/apply",
        json={
            "on_or_after": FRIDAY,
            "on_or_before": FRIDAY,
            "confirm": True,
            "preview_id": plan["preview_id"],
            "digest": plan["digest"],
        },
    )
    assert confirmed.status_code == 200


def test_reply_requires_confirm(client: TestClient, stub: StubClient) -> None:
    unconfirmed = client.post(
        "/api/groupme/reply", json={"group_slug": "risk-friday", "text": "on my way"}
    )
    assert unconfirmed.status_code == 400
    assert stub.posted == []

    confirmed = client.post(
        "/api/groupme/reply",
        json={"group_slug": "risk-friday", "text": "on my way", "confirm": True},
    )
    assert confirmed.status_code == 200
    assert confirmed.json() == {"ok": True}
    assert stub.posted[0]["text"] == "on my way"


def test_replying_to_an_unregistered_slug_is_a_404(client: TestClient) -> None:
    r = client.post(
        "/api/groupme/reply",
        json={"group_slug": "risk-nowhere", "text": "hi", "confirm": True},
    )
    assert r.status_code == 404


def test_the_map_route_writes_only_the_exact_matches(
    client: TestClient, stub: StubClient, tmp_path: Path
) -> None:
    """"Test Bra" resembles Test Bravo and is not equal to him, so it must not
    link — and the route must not quietly promote a near-miss to make its
    numbers look better."""
    conn = connect(tmp_path / "api.db")
    ensure_schema(conn)
    with transaction(conn):
        groups_repo.upsert(
            conn,
            slug="roster-source",
            groupme_id="announcements-placeholder",
            label="Announcements",
        )
    conn.close()

    stub.members = [
        GroupMeMember("user-1", "m1", "Test Alpha"),
        GroupMeMember("user-9", "m9", "Test Bra"),
    ]
    r = client.post("/api/groupme/identities/map", params={"confirm": True})
    assert r.status_code == 200
    assert r.json()["linked"] == 1
    assert [u["display_name"] for u in r.json()["unlinked"]] == ["Test Bravo"]


def test_the_map_route_requires_confirm(client: TestClient) -> None:
    r = client.post("/api/groupme/identities/map")
    assert r.status_code == 400
