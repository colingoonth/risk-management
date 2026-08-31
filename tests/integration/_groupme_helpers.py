"""Shared fixtures-adjacent helpers for the GroupMe suites.

Not a test module (pytest will not collect it). It exists so the three GroupMe
suites share one stub client and one seeding vocabulary instead of three
near-copies that drift.

EVERY name in here is invented and every id is an obvious placeholder string.
The repository is public; the chapter's real ids live only in the chair's local
database.
"""

from __future__ import annotations

import sqlite3

from risk.db.connection import transaction
from risk.repos import event_types as etypes_repo
from risk.repos import events as events_repo
from risk.repos import groupme_groups as groups_repo
from risk.repos import groupme_identities as identities_repo
from risk.repos import member_statuses as statuses_repo
from risk.repos import members as members_repo
from risk.repos import semesters as semesters_repo
from risk.repos import shift_types as stypes_repo
from risk.repos import shifts as shifts_repo
from risk.services.groupme import GroupMeMember, InboundMessage

STAMP = "2026-08-31T00:00:00+00:00"
FRIDAY = "2026-09-04"


class StubClient:
    """Stands in for ``GroupMeClient``. Records calls, never opens a socket.

    Substituted by passing it where a client is expected, or via the API's
    ``get_groupme_client`` dependency override. Nothing in the suite constructs
    a real client, so nothing can reach the network or the macOS keychain.
    """

    def __init__(self, *, members: list[GroupMeMember] | None = None) -> None:
        self.members = members or []
        self.posted: list[dict[str, object]] = []
        self.added: list[tuple[str, str]] = []
        self.removed: list[str] = []
        self.messages: list[InboundMessage] = []
        self.raise_on_post: Exception | None = None

    def list_members(self, parent_id: str) -> list[GroupMeMember]:
        return list(self.members)

    def post_message(self, group_id, text, *, mentions=(), source_guid=None):
        if self.raise_on_post is not None:
            raise self.raise_on_post
        self.posted.append(
            {
                "group_id": group_id,
                "text": text,
                "mentions": list(mentions),
                "source_guid": source_guid,
            }
        )
        return f"msg-{len(self.posted)}"

    def add_members(self, parent_id, members):
        self.added.extend(members)
        return "results-1"

    def remove_member(self, parent_id, membership_id):
        self.removed.append(membership_id)

    def list_messages(self, group_id, *, after_id=None, limit=100):
        return list(self.messages)

    def list_messages_after(self, group_id, *, after_id=None, limit=100):
        return self.list_messages(group_id, after_id=after_id, limit=limit)


def account(user_id: str, nickname: str) -> GroupMeMember:
    return GroupMeMember(user_id=user_id, membership_id=f"mem-{user_id}", nickname=nickname)


def seed_semester(conn: sqlite3.Connection) -> int:
    with transaction(conn):
        sem_id = semesters_repo.insert(
            conn, name="FA26", starts_on="2026-08-20", ends_on="2026-12-15"
        )
        conn.execute("UPDATE semesters SET is_current = 1 WHERE id = ?", (sem_id,))
    return sem_id


def seed_member(conn: sqlite3.Connection, slug: str, display_name: str) -> int:
    active = statuses_repo.get_by_slug(conn, "active")
    assert active is not None
    with transaction(conn):
        return members_repo.insert(
            conn, slug=slug, display_name=display_name, status_id=active.id
        )


def seed_event(conn: sqlite3.Connection, semester_id: int, name: str, date: str) -> int:
    etype = etypes_repo.get_by_slug(conn, "mixer")
    assert etype is not None
    with transaction(conn):
        return events_repo.insert(
            conn,
            semester_id=semester_id,
            event_type_id=etype.id,
            display_name=name,
            date=date,
        )


def assign(
    conn: sqlite3.Connection, event_id: int, member_id: int, shift_type: str, slot: int = 0
) -> int:
    stype = stypes_repo.get_by_slug(conn, shift_type)
    assert stype is not None
    with transaction(conn):
        shift_id = shifts_repo.insert_open(
            conn, event_id=event_id, shift_type_id=stype.id, slot_index=slot
        )
        shifts_repo.assign(
            conn,
            shift_id=shift_id,
            member_id=member_id,
            effective_pledge_mode_id=None,
            assigned_at=STAMP,
        )
    return shift_id


def link(conn: sqlite3.Connection, member_id: int, user_id: str, nickname: str) -> None:
    with transaction(conn):
        identities_repo.link(
            conn,
            member_id=member_id,
            groupme_user_id=user_id,
            nickname=nickname,
            confidence="exact",
            linked_at=STAMP,
        )


def seed_groups(conn: sqlite3.Connection, *, days: dict[int, str] | None = None) -> None:
    """Register a parent plus day topics. Placeholder ids, never real ones."""
    with transaction(conn):
        groups_repo.upsert(
            conn, slug="risk-parent", groupme_id="parent-placeholder", label="Risk"
        )
        for weekday, slug in (days or {2: "risk-tuesday", 5: "risk-friday"}).items():
            groups_repo.upsert(
                conn,
                slug=slug,
                groupme_id=f"topic-placeholder-{weekday}",
                label=f"Risk {slug}",
                parent_slug="risk-parent",
                weekday=weekday,
            )
