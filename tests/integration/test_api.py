"""Integration tests for the FastAPI layer via TestClient."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from risk.api import create_app
from risk.db.connection import connect
from risk.db.schema import ensure_schema
from risk.repos import event_types as etypes_repo
from risk.repos import events as events_repo
from risk.repos import houses as houses_repo
from risk.repos import member_statuses as statuses_repo
from risk.repos import members as members_repo
from risk.repos import semesters as semesters_repo
from risk.services import shift_requirements as reqs_svc

pytestmark = pytest.mark.integration


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    db_path = tmp_path / "api.db"
    _seed(db_path)
    app = create_app(db_path=db_path)
    with TestClient(app) as c:
        yield c


def _seed(db_path: Path) -> None:
    conn = connect(db_path)
    ensure_schema(conn)
    conn.execute("BEGIN")
    sem_id = semesters_repo.insert(conn, name="FA26", starts_on="2026-08-20", ends_on="2026-12-15")
    conn.execute("UPDATE semesters SET is_current = 1 WHERE id = ?", (sem_id,))
    house_id = houses_repo.insert(conn, slug="zta", display_name="ZTA")
    active = statuses_repo.get_by_slug(conn, "active")
    assert active is not None
    for i in range(6):
        members_repo.insert(
            conn,
            slug=f"bro{i}",
            display_name=f"Bro {i}",
            status_id=active.id,
            class_year=2027 + (i % 3),
            pledge_class="Zeta",
        )
    et = etypes_repo.get_by_slug(conn, "mixer")
    assert et is not None
    ev_id = events_repo.insert(
        conn,
        semester_id=sem_id,
        event_type_id=et.id,
        display_name="ZTA Mixer",
        date="2026-09-12",
        host_house_id=house_id,
    )
    reqs_svc.snapshot_for_event(conn, ev_id)
    conn.execute("COMMIT")
    conn.close()


def test_health(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_meta_reports_current_semester(client: TestClient) -> None:
    r = client.get("/api/meta")
    assert r.status_code == 200
    body = r.json()
    assert body["current_semester"]["name"] == "FA26"


def test_list_members(client: TestClient) -> None:
    r = client.get("/api/members")
    assert r.status_code == 200
    members = r.json()
    assert len(members) == 6
    assert all(m["pledge_class"] == "Zeta" for m in members)


def test_get_member_404(client: TestClient) -> None:
    r = client.get("/api/members/nope")
    assert r.status_code == 404


def test_list_events(client: TestClient) -> None:
    r = client.get("/api/events?semester=FA26")
    assert r.status_code == 200
    events = r.json()
    assert len(events) == 1
    assert events[0]["display_name"] == "ZTA Mixer"


def test_create_event_and_snapshot(client: TestClient) -> None:
    r = client.post(
        "/api/events?semester=FA26",
        json={"event_type_slug": "mixer", "display_name": "Late Add", "date": "2026-10-01"},
    )
    assert r.status_code == 201, r.text
    ev = r.json()
    # Shift requirements snapshotted → it has open shifts to auto-assign later.
    shifts = client.get(f"/api/events/{ev['id']}/shifts")
    assert shifts.status_code == 200


def test_create_event_bad_date_400(client: TestClient) -> None:
    r = client.post(
        "/api/events?semester=FA26",
        json={"event_type_slug": "mixer", "display_name": "Bad", "date": "October 1"},
    )
    assert r.status_code == 400


def test_auto_assign_single_event(client: TestClient) -> None:
    events = client.get("/api/events?semester=FA26").json()
    eid = events[0]["id"]
    r = client.post(f"/api/events/{eid}/auto-assign", json={"seed": 42})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["seed"] == 42
    assert body["resolved_mode"]
    assert any(a["reason"] == "assigned" for a in body["assignments"])


def test_auto_assign_dry_run_does_not_persist(client: TestClient) -> None:
    events = client.get("/api/events?semester=FA26").json()
    eid = events[0]["id"]
    r = client.post(f"/api/events/{eid}/auto-assign?dry_run=true", json={"seed": 1})
    assert r.status_code == 200
    # No shifts persisted from a dry run.
    shifts = client.get(f"/api/events/{eid}/shifts").json()
    assert all(s["assigned_member_id"] is None for s in shifts)


def test_auto_assign_bulk(client: TestClient) -> None:
    r = client.post("/api/events/auto-assign-bulk?semester=FA26", json={"seed": 7})
    assert r.status_code == 200, r.text
    results = r.json()
    assert len(results) == 1


def test_archive_check_reports_event_blocker(client: TestClient) -> None:
    r = client.get("/api/semesters/FA26/archive-check")
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["future_event_count"] >= 1  # the seeded ZTA Mixer is non-terminal
    assert rep["is_blocked"] is True


def test_archive_refuses_current_term(client: TestClient) -> None:
    r = client.post("/api/semesters/FA26/archive", json={"force": True})
    assert r.status_code == 400, r.text
    assert "current term" in r.json()["detail"]


def test_archive_non_current_empty_term(client: TestClient) -> None:
    client.post(
        "/api/semesters",
        json={"name": "SP27", "starts_on": "2027-01-15", "ends_on": "2027-05-10"},
    )
    # FA26 stays current; SP27 is non-current and empty → archivable cleanly.
    r = client.post("/api/semesters/SP27/archive", json={})
    assert r.status_code == 200, r.text
    assert r.json()["archived_at"]
    # Idempotency: a second archive is a clean 400, not a 500.
    again = client.post("/api/semesters/SP27/archive", json={})
    assert again.status_code == 400, again.text


def test_archive_blocked_by_non_terminal_event(client: TestClient) -> None:
    client.post(
        "/api/semesters",
        json={"name": "SP27", "starts_on": "2027-01-15", "ends_on": "2027-05-10"},
    )
    client.post("/api/semesters/SP27/set-current")  # FA26 no longer current
    r = client.post("/api/semesters/FA26/archive", json={"force": True})
    assert r.status_code == 400, r.text  # the non-terminal event hard-blocks even under force


def test_list_event_types(client: TestClient) -> None:
    r = client.get("/api/event-types")
    assert r.status_code == 200, r.text
    types = r.json()
    slugs = {t["slug"] for t in types}
    assert {"mixer", "krush"} <= slugs
    # All seeded types have shift-requirement defaults.
    assert all(t["has_shift_defaults"] for t in types)


def test_list_houses(client: TestClient) -> None:
    r = client.get("/api/houses")
    assert r.status_code == 200, r.text
    houses = r.json()
    assert {h["slug"] for h in houses} == {"zta"}
    assert all("display_name" in h for h in houses)


def test_pledge_takeover_set_house_mode(client: TestClient) -> None:
    r = client.post(
        "/api/semesters/FA26/house-modes",
        json={"house_slug": "zta", "pledge_mode_slug": "pledge_takeover_full"},
    )
    assert r.status_code == 200, r.text
    modes = client.get("/api/semesters/FA26/house-modes").json()
    assert any(m["pledge_mode_slug"] == "pledge_takeover_full" for m in modes)


def test_dashboard_surfaces_unfilled_and_loads(client: TestClient) -> None:
    r = client.get("/api/dashboard?semester=FA26")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["semester_name"] == "FA26"
    # Before assignment the mixer is unfilled.
    assert len(body["unfilled_events"]) == 1
    # "Who hasn't worked" lists active members, lightest first (all 0 here).
    assert len(body["members_needing_shifts"]) == 6
    assert body["members_needing_shifts"][0]["shift_count"] == 0


def test_dashboard_unfilled_clears_after_assign(client: TestClient) -> None:
    events = client.get("/api/events?semester=FA26").json()
    eid = events[0]["id"]
    client.post(f"/api/events/{eid}/auto-assign", json={"seed": 42})
    body = client.get("/api/dashboard?semester=FA26").json()
    # With 6 members and a mixer needing fewer, some get shifts; loads shift.
    assert sum(m["shift_count"] for m in body["members_needing_shifts"]) >= 1


def test_issue_strike_and_list(client: TestClient) -> None:
    r = client.post(
        "/api/strikes?semester=FA26",
        json={"member_slug": "bro0", "issued_on": "2026-09-15", "reason": "no-show"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["strike_number"] == 1
    listed = client.get("/api/strikes?member=bro0&semester=FA26").json()
    assert len(listed) == 1


def test_issue_strike_rejects_identical_duplicate(client: TestClient) -> None:
    body = {"member_slug": "bro0", "issued_on": "2026-09-15", "reason": "no-show"}
    first = client.post("/api/strikes?semester=FA26", json=body)
    assert first.status_code == 201, first.text
    dup = client.post("/api/strikes?semester=FA26", json=body)
    assert dup.status_code == 400, dup.text
    # The phantom strike never lands: still exactly one on the ladder.
    listed = client.get("/api/strikes?member=bro0&semester=FA26").json()
    assert len(listed) == 1


def test_list_removal_methods(client: TestClient) -> None:
    r = client.get("/api/removal-methods")
    assert r.status_code == 200, r.text
    methods = r.json()
    slugs = {m["slug"] for m in methods}
    assert "donation" in slugs  # seeded in migration 0002
    assert all("display_name" in m for m in methods)


def test_remove_strike_closes_and_recounts(client: TestClient) -> None:
    for day in ("2026-09-15", "2026-09-22"):
        client.post(
            "/api/strikes?semester=FA26",
            json={"member_slug": "bro0", "issued_on": day, "reason": "no-show"},
        )
    ladder = client.get("/api/strikes?member=bro0&semester=FA26").json()
    assert len(ladder) == 2
    target = ladder[0]["id"]
    method = client.get("/api/removal-methods").json()[0]["slug"]
    r = client.post(
        "/api/strikes/remove",
        json={
            "member_slug": "bro0",
            "removal_method_slug": method,
            "performed_on": "2026-10-01",
            "strike_ids": [target],
            "semester": "FA26",
        },
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["closed_strike_ids"] == [target]
    assert out["active_count_after"] == 1
    assert len(client.get("/api/strikes?member=bro0&semester=FA26").json()) == 1


def test_remove_strike_rejects_foreign_id(client: TestClient) -> None:
    client.post(
        "/api/strikes?semester=FA26",
        json={"member_slug": "bro0", "issued_on": "2026-09-15", "reason": "no-show"},
    )
    method = client.get("/api/removal-methods").json()[0]["slug"]
    # Strike id 999 is not an open strike for bro0 in FA26 — guard must reject.
    r = client.post(
        "/api/strikes/remove",
        json={
            "member_slug": "bro0",
            "removal_method_slug": method,
            "performed_on": "2026-10-01",
            "strike_ids": [999],
            "semester": "FA26",
        },
    )
    assert r.status_code == 400, r.text


def test_consequences_carry_member_slug(client: TestClient) -> None:
    # Cross expulsion-review (5 strikes) so a pending consequence is emitted.
    for i in range(5):
        client.post(
            "/api/strikes?semester=FA26",
            json={"member_slug": "bro0", "issued_on": f"2026-09-1{i}", "reason": f"r{i}"},
        )
    rows = client.get("/api/consequences?state=pending").json()
    assert rows, "expected at least one pending consequence"
    assert all(row["member_slug"] == "bro0" for row in rows)


def test_swap_request_and_accept(client: TestClient) -> None:
    # Add a spare member who stays unassigned, to serve as swap counterparty.
    client.post(
        "/api/ingest/gform-roster",
        data={"semester": "FA26", "dry_run": "false"},
        files={
            "file": (
                "r.csv",
                "Full Name,Rising Class,PC,EC\nSpare Guy,Rising Senior,Eta,No\n",
                "text/csv",
            )
        },
    )
    events = client.get("/api/events?semester=FA26").json()
    eid = events[0]["id"]
    client.post(f"/api/events/{eid}/auto-assign", json={"seed": 42})
    shifts = client.get(f"/api/events/{eid}/shifts").json()
    assigned = [s for s in shifts if s["assigned_member_id"] is not None]
    assert assigned, "auto-assign should have filled at least one shift"
    from_shift = assigned[0]

    r = client.post(
        "/api/swaps?semester=FA26",
        json={"from_shift_id": from_shift["id"], "counterparty_member_slug": "spare-guy"},
    )
    assert r.status_code == 201, r.text
    req_id = r.json()["id"]
    assert r.json()["state"] == "open"

    accepted = client.post(f"/api/swaps/{req_id}/accept")
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["state"] == "accepted"


def test_concurrent_reads_no_cross_thread_error(client: TestClient) -> None:
    # FastAPI runs sync endpoints in a threadpool; a connection opened on one
    # thread must be usable on another (check_same_thread=False). Fire many
    # parallel reads and assert none 500 with the cross-thread ProgrammingError.
    from concurrent.futures import ThreadPoolExecutor

    paths = ["/api/events?semester=FA26", "/api/shifts", "/api/swaps", "/api/members"] * 8
    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(lambda p: client.get(p).status_code, paths))
    assert all(c == 200 for c in codes), codes


def test_list_shifts_by_member_and_status(client: TestClient) -> None:
    events = client.get("/api/events?semester=FA26").json()
    eid = events[0]["id"]
    client.post(f"/api/events/{eid}/auto-assign", json={"seed": 42})
    shifts = client.get(f"/api/events/{eid}/shifts").json()
    holder = next(s["assigned_member_slug"] for s in shifts if s["assigned_member_id"])
    mine = client.get(f"/api/shifts?member={holder}&status=assigned&semester=FA26")
    assert mine.status_code == 200, mine.text
    rows = mine.json()
    assert rows and all(r["assigned_member_slug"] == holder for r in rows)
    assert all(r["status"] == "assigned" for r in rows)


def test_list_shifts_rejects_bad_status(client: TestClient) -> None:
    r = client.get("/api/shifts?status=bogus")
    assert r.status_code == 422, r.text


def test_raise_swap_rejects_self_swap(client: TestClient) -> None:
    events = client.get("/api/events?semester=FA26").json()
    eid = events[0]["id"]
    client.post(f"/api/events/{eid}/auto-assign", json={"seed": 42})
    shifts = client.get(f"/api/events/{eid}/shifts").json()
    assigned = next(s for s in shifts if s["assigned_member_id"])
    holder = assigned["assigned_member_slug"]
    r = client.post(
        "/api/swaps?semester=FA26",
        json={"from_shift_id": assigned["id"], "counterparty_member_slug": holder},
    )
    assert r.status_code == 400, r.text


def test_gform_roster_upload(client: TestClient) -> None:
    csv = "Full Name,Rising Class,PC,EC\nNew Guy,Rising Senior,Eta,Yes\n"
    r = client.post(
        "/api/ingest/gform-roster",
        data={"semester": "FA26", "dry_run": "false"},
        files={"file": ("roster.csv", csv, "text/csv")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["inserted_members"] == 1
    assert client.get("/api/members/new-guy").status_code == 200
