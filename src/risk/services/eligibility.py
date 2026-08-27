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

  NOT here: unavailability. It used to be, tested against the event's own date,
  and that is wrong for two of the six shift types — setup runs up to two days
  BEFORE the party and cleanup is the morning AFTER. This function answers one
  question for the whole event, so it cannot ask about a window it does not
  know. ``assignment`` asks ``availability.can_cover`` per shift type instead,
  which is the only place that knows which window applies. Deliberately ONE
  rule: the date-level version lived here for months while the window-aware
  version sat unused in ``services.availability``, and having both is how they
  disagree.

  Soft excludes (overrideable with ``--allow <automation_key>``):
    S1. Roles where ``default_excluded_from_assignment = 1 AND
        exclude_is_soft = 1``. Excluded by default; included only if their
        ``automation_key`` appears in the caller-supplied ``allowed_keys`` set.

``eligible_for`` answers "may this member work THIS EVENT", which is a question
about the person. Whether they may work a particular SHIFT TYPE at that event is
a second, narrower question, and it lives in :func:`filter_for_shift_type` —
because the answer differs per slot and ``eligible_for`` returns one pool for
the whole event.

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
class ShiftTypePool:
    """The event pool narrowed to those who may work one specific shift type."""

    eligible: list[EligibleMember]
    required_qualification_slugs: tuple[str, ...]
    excluded_by_qualification: int


@dataclass(frozen=True, slots=True)
class EligibilityResult:
    eligible: list[EligibleMember]
    excluded_by_status: int
    excluded_by_host_house: int
    excluded_by_hard_role: int
    excluded_by_soft_role: int


def eligible_for(
    conn: sqlite3.Connection,
    *,
    event_id: int,
    semester_id: int,
    host_house_id: int | None,
    allowed_keys: frozenset[str] = frozenset(),
    honor_hard_role_exclusion: bool = True,
) -> EligibilityResult:
    """Compute the eligible pool for ``event_id`` in ``semester_id``.

    ``host_house_id = None`` (off-site event) skips the H2 host-house filter.
    ``allowed_keys`` are the ``roles.automation_key`` values passed via
    ``--allow ROLE`` on the CLI — soft-excluded roles whose key is in this
    set are NOT excluded.
    ``honor_hard_role_exclusion=False`` drops H3 and nothing else. It has
    exactly one caller — the strike make-up pass — and exists because a hard
    exemption and a disciplinary penalty are different things. An exemption
    says the chair does not put this officer in the rotation; it does not say a
    strike he earned stops being owed. Colin's ruling, and the reason the social
    chair carrying a spring strike works one make-up shift and no rotation
    shifts. Status, host house and the soft-role gate all still apply: those are
    about whether the member CAN work the party, which a strike does not change.
    """
    rows = conn.execute(
        """
        SELECT
          m.id AS member_id,
          m.slug AS member_slug,
          m.display_name,
          -- The QUOTA year, not necessarily the roster year. NULL
          -- risk_class_year (almost everyone) falls through to class_year.
          -- Applied here rather than at each use so the tier, the pool counts
          -- and the younger-first tiebreak cannot disagree about a member.
          COALESCE(m.risk_class_year, m.class_year) AS class_year,
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

    for r in rows:
        if r["status_excludes"]:
            excluded_by_status += 1
            continue
        if host_house_id is not None and r["member_house_id"] == host_house_id:
            excluded_by_host_house += 1
            continue
        if honor_hard_role_exclusion and r["hard_role_excluded"]:
            excluded_by_hard_role += 1
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
    )


def filter_for_shift_type(
    conn: sqlite3.Connection,
    *,
    pool: list[EligibleMember],
    shift_type_id: int,
    semester_id: int,
) -> ShiftTypePool:
    """Narrow an event pool to the members who may work ``shift_type_id``.

    Reads ``shift_type_required_qualification``, which has declared since
    migration 0012 that ``dj`` requires the ``dj`` qualification. Nothing acted
    on it until now, so auto-assign would put a member who cannot DJ on the DJ
    slot — the requirement was documented in the schema and unenforced in code.

    A member must hold EVERY qualification the shift type requires. Only ``dj``
    is gated today, so the conjunction is academic, but a shift type carrying two
    requirements should mean both and not either.

    Qualifications are semester-scoped on purpose (the DJ job changes hands), so
    this needs ``semester_id`` and cannot be answered from the member alone.

    ``over-21`` gates NOTHING. It is informational data on 41 members recording
    who purchases alcohol; migration 0014 retracted the bar requirement it was
    briefly wired to. This function will only ever gate ``bar`` again if someone
    re-inserts that row, which they should not.
    """
    from risk.repos import member_qualifications as _mq
    from risk.repos import qualifications as _quals

    required = _quals.required_for_shift_type(conn, shift_type_id)
    if not required:
        return ShiftTypePool(
            eligible=list(pool),
            required_qualification_slugs=(),
            excluded_by_qualification=0,
        )

    holders: set[int] | None = None
    for qual in required:
        ids = _mq.member_ids_with(conn, semester_id=semester_id, qualification_slug=qual.slug)
        holders = ids if holders is None else (holders & ids)
        if not holders:
            break

    qualified = [m for m in pool if m.member_id in (holders or set())]
    return ShiftTypePool(
        eligible=qualified,
        required_qualification_slugs=tuple(q.slug for q in required),
        excluded_by_qualification=len(pool) - len(qualified),
    )
