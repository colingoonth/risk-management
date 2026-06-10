"""Eligibility filter for auto-assign.

Computes which members may be assigned to shifts for a given event in a given
semester. Layers (all evaluated):

  Hard excludes (cannot be overridden):
    H1. Status: ``member_statuses.excludes_from_assignment = 1``
        (alumni, abroad, exempt, inactive, transferred)
    H2. Host-house members: ``member_house_assignments`` matches event's
        ``host_house_id`` for the event's semester
    H3. Hard-excluded roles: any role the member holds in the event's
        semester where ``default_excluded_from_assignment = 1 AND
        exclude_is_soft = 0`` (e.g. EC).

  Soft excludes (overrideable with ``--allow <automation_key>``):
    S1. Roles where ``default_excluded_from_assignment = 1 AND
        exclude_is_soft = 1``. Excluded by default; included only if their
        ``automation_key`` appears in the caller-supplied ``allowed_keys`` set.

The function returns ``EligibleMember`` rows, which carry the per-member info
fairness needs (class_year + whether the member is a pledge in this semester).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

PLEDGE_ROLE_SLUG = "pledge"
"""Members with this role for the event's semester are considered pledges.

Used by pledge_mode resolution + assignment when the resolved mode prefers
pledges. Locked as a string constant rather than configured to keep the
identification rule inspectable.
"""


@dataclass(frozen=True, slots=True)
class EligibleMember:
    member_id: int
    member_slug: str
    display_name: str
    class_year: int | None
    pledge_class: str | None
    is_pledge: bool


@dataclass(frozen=True, slots=True)
class EligibilityResult:
    eligible: list[EligibleMember]
    excluded_by_status: int
    excluded_by_host_house: int
    excluded_by_hard_role: int
    excluded_by_soft_role: int
    excluded_by_unavailability: int = 0


def eligible_for(
    conn: sqlite3.Connection,
    *,
    event_id: int,
    semester_id: int,
    host_house_id: int | None,
    allowed_keys: frozenset[str] = frozenset(),
    event_date: str | None = None,
) -> EligibilityResult:
    """Compute the eligible pool for ``event_id`` in ``semester_id``.

    ``host_house_id = None`` (off-site event) skips the H2 host-house filter.
    ``allowed_keys`` are the ``roles.automation_key`` values passed via
    ``--allow ROLE`` on the CLI — soft-excluded roles whose key is in this
    set are NOT excluded.
    ``event_date`` (ISO YYYY-MM-DD) enables H4 unavailability filtering:
    any member with an unavailability window covering ``event_date`` is
    excluded. Phase 4 callers that pre-date Phase 6 may omit it.
    """
    from risk.repos import unavailability as _unav

    unavailable_member_ids: set[int] = (
        _unav.member_ids_unavailable_on(
            conn, semester_id=semester_id, date=event_date
        )
        if event_date is not None
        else set()
    )
    rows = conn.execute(
        """
        SELECT
          m.id AS member_id,
          m.slug AS member_slug,
          m.display_name,
          m.class_year,
          m.pledge_class,
          ms.excludes_from_assignment AS status_excludes,
          mha.house_id AS member_house_id,
          EXISTS(
            SELECT 1 FROM member_roles mr2
            JOIN roles r2 ON r2.id = mr2.role_id
            WHERE mr2.member_id = m.id
              AND mr2.semester_id = ?
              AND r2.default_excluded_from_assignment = 1
              AND r2.exclude_is_soft = 0
          ) AS hard_role_excluded,
          (
            SELECT GROUP_CONCAT(r3.automation_key, ',')
            FROM member_roles mr3
            JOIN roles r3 ON r3.id = mr3.role_id
            WHERE mr3.member_id = m.id
              AND mr3.semester_id = ?
              AND r3.default_excluded_from_assignment = 1
              AND r3.exclude_is_soft = 1
              AND r3.automation_key IS NOT NULL
          ) AS soft_role_keys,
          EXISTS(
            SELECT 1 FROM member_roles mr4
            JOIN roles r4 ON r4.id = mr4.role_id
            WHERE mr4.member_id = m.id
              AND mr4.semester_id = ?
              AND r4.slug = ?
          ) AS is_pledge
        FROM members m
        JOIN member_statuses ms ON ms.id = m.status_id
        LEFT JOIN member_house_assignments mha
          ON mha.member_id = m.id AND mha.semester_id = ?
        ORDER BY m.slug
        """,
        (semester_id, semester_id, semester_id, PLEDGE_ROLE_SLUG, semester_id),
    ).fetchall()

    eligible: list[EligibleMember] = []
    excluded_by_status = 0
    excluded_by_host_house = 0
    excluded_by_hard_role = 0
    excluded_by_soft_role = 0
    excluded_by_unavailability = 0

    for r in rows:
        if r["status_excludes"]:
            excluded_by_status += 1
            continue
        if host_house_id is not None and r["member_house_id"] == host_house_id:
            excluded_by_host_house += 1
            continue
        if r["hard_role_excluded"]:
            excluded_by_hard_role += 1
            continue
        if r["member_id"] in unavailable_member_ids:
            excluded_by_unavailability += 1
            continue
        soft_keys_csv = r["soft_role_keys"]
        if soft_keys_csv:
            soft_keys = {k for k in soft_keys_csv.split(",") if k}
            # A soft-excluded role blocks the member unless EVERY soft key
            # they carry is in allowed_keys. One un-allowed soft key is enough
            # to exclude — the chair must opt in per role.
            if not soft_keys.issubset(allowed_keys):
                excluded_by_soft_role += 1
                continue
        eligible.append(
            EligibleMember(
                member_id=r["member_id"],
                member_slug=r["member_slug"],
                display_name=r["display_name"],
                class_year=r["class_year"],
                pledge_class=r["pledge_class"],
                is_pledge=bool(r["is_pledge"]),
            )
        )

    _ = event_id  # event_id reserved for future per-event exclusion tables.
    return EligibilityResult(
        eligible=eligible,
        excluded_by_status=excluded_by_status,
        excluded_by_host_house=excluded_by_host_house,
        excluded_by_hard_role=excluded_by_hard_role,
        excluded_by_soft_role=excluded_by_soft_role,
        excluded_by_unavailability=excluded_by_unavailability,
    )
