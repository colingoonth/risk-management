"""Pydantic DTOs for the API layer.

All response models set ``from_attributes=True`` so they validate straight from
the service/repo frozen dataclasses (``Model.model_validate(dataclass)``) — the
adapter never hand-copies fields. Request bodies are plain input models.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- Entities ---


class SemesterOut(_Out):
    id: int
    name: str
    starts_on: str
    ends_on: str
    is_current: bool
    pledge_takeover_starts_on: str | None
    archived_at: str | None


class MemberOut(_Out):
    id: int
    slug: str
    display_name: str
    status_slug: str
    class_year: int | None
    pledge_class: str | None
    notes: str | None


class EventOut(_Out):
    id: int
    semester_id: int
    display_name: str
    date: str
    start_time: str | None
    end_time: str | None
    status: str
    resync_pending: bool
    notes: str | None
    semester_name: str
    event_type_slug: str
    host_house_slug: str | None


class ShiftSlotGroupOut(_Out):
    shift_type_slug: str
    target_count: int
    assigned_count: int


class EventSummaryOut(_Out):
    """``EventOut`` plus the staffing rollup the calendar reads at a glance.

    Deliberately a field-for-field SUPERSET of ``EventOut`` so the TypeScript
    client can declare ``extends EventRow`` and the two DTOs cannot drift apart.

    Kept as a separate model rather than adding the rollup to ``EventOut``:
    ``EventOut`` is validated straight off the ``events_repo.Event`` dataclass and
    is the return type of four mutation endpoints, so adding fill fields there
    would either weld a staffing rollup into the hot read path or mint a DTO whose
    numbers mean "the real target" on one route and "0, we didn't look" on four
    others.
    """

    # --- identical to EventOut ---
    id: int
    semester_id: int
    display_name: str
    date: str
    start_time: str | None
    end_time: str | None
    status: str
    resync_pending: bool
    notes: str | None
    semester_name: str
    event_type_slug: str
    host_house_slug: str | None
    # --- the rollup ---
    target_slots: int  # SUM(target_count). Independent of whether shifts exist.
    assigned_slots: int  # RAW, UNCLAMPED — may exceed target_slots.
    open_slots: int  # max(target - assigned, 0). The alarm number.
    orphan_slots: int  # max(assigned - target, 0). Surfaced, never hidden.
    by_type: list[ShiftSlotGroupOut] = []


class ShiftTypeWindowOut(_Out):
    """WHEN a shift type is worked, relative to the event date."""

    shift_type_slug: str
    offset_days_start: int
    offset_days_end: int
    window_start_time: str
    window_end_time: str
    min_contiguous_minutes: int | None
    occupies_event_night: bool


class ShiftOut(_Out):
    id: int
    event_id: int
    shift_type_slug: str
    slot_index: int
    assigned_member_id: int | None
    assigned_member_slug: str | None
    assigned_member_display_name: str | None = None
    effective_pledge_mode_slug: str | None
    status: str
    assigned_at: str | None
    chair_set: bool = False


class NumberedStrikeOut(_Out):
    id: int
    member_id: int
    semester_id: int
    strike_number: int
    issued_on: str
    reason: str


class PendingConsequenceOut(_Out):
    id: int
    member_id: int
    member_slug: str | None = None
    semester_id: int
    triggering_strike_id: int
    kind: str
    state: str
    created_at: str
    resolved_at: str | None


class RemovalMethodOut(_Out):
    slug: str
    display_name: str


class StrikeRemovalOut(_Out):
    removal_id: int
    closed_strike_ids: list[int]
    active_count_after: int


class SwapRequestOut(_Out):
    id: int
    semester_id: int
    from_shift_id: int
    to_shift_id: int | None
    initiator_slug: str
    counterparty_slug: str | None
    state: str
    created_at: str
    resolved_at: str | None


class EventTypeOut(_Out):
    slug: str
    display_name: str
    has_shift_defaults: bool


class HouseOut(_Out):
    slug: str
    display_name: str


class ArchiveReportOut(_Out):
    semester_id: int
    semester_name: str
    open_strike_count: int
    pending_consequence_count: int
    future_event_count: int
    open_swap_count: int
    blockers: list[str]
    is_blocked: bool


class ArchiveResultOut(_Out):
    semester_id: int
    archived_at: str
    strikes_closed: int
    strikes_carried_forward: int
    consequences_carried_forward: int
    swaps_cancelled: int
    carry_to_semester_id: int | None


class HouseModeOut(_Out):
    house_slug: str
    pledge_mode_slug: str


# --- Composite / workflow outputs ---


class ProposedAssignmentOut(BaseModel):
    shift_type_slug: str
    slot_index: int
    member_slug: str | None
    score: float | None
    reason: str


class AutoAssignOut(BaseModel):
    event_id: int
    event_name: str
    resolved_mode: str
    configured_mode: str
    seed: int
    eligible_count: int
    assignments: list[ProposedAssignmentOut]
    warnings: list[str]


class UnfilledEventOut(BaseModel):
    event_id: int
    display_name: str
    date: str
    event_type_slug: str
    host_house_slug: str | None
    open_slots: int
    total_slots: int
    resync_pending: bool


class MemberLoadOut(BaseModel):
    member_slug: str
    display_name: str
    shift_count: int


class DashboardOut(BaseModel):
    semester_name: str
    unfilled_events: list[UnfilledEventOut]
    pending_swaps: list[SwapRequestOut]
    members_needing_shifts: list[MemberLoadOut]
    pending_consequences: list[PendingConsequenceOut]
    resync_pending_count: int


# --- Request bodies ---


class SemesterIn(BaseModel):
    name: str
    starts_on: str
    ends_on: str
    pledge_takeover_starts_on: str | None = None


class HouseModeIn(BaseModel):
    house_slug: str
    pledge_mode_slug: str


class ArchiveIn(BaseModel):
    force: bool = False
    carry_to: str | None = None


class EventIn(BaseModel):
    event_type_slug: str
    display_name: str
    date: str
    host_house_slug: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    notes: str | None = None


class AutoAssignIn(BaseModel):
    seed: int | None = None
    reassign: bool = False
    allowed_keys: list[str] = []


class SetHostIn(BaseModel):
    host_house_slug: str | None


class StrikeIn(BaseModel):
    member_slug: str
    issued_on: str
    reason: str


class StrikeRemovalIn(BaseModel):
    member_slug: str
    removal_method_slug: str
    performed_on: str
    strike_ids: list[int]
    semester: str | None = None
    performed_by_slug: str | None = None
    notes: str | None = None


class SwapRequestIn(BaseModel):
    from_shift_id: int
    to_shift_id: int | None = None
    counterparty_member_slug: str | None = None


# --- Chair notes ---


class ChairNoteIn(BaseModel):
    body: str
    kind: str = "one_off"
    author: str = "chair"


class ChairNoteCloseIn(BaseModel):
    closed_note: str | None = None


class ChairNoteBodyIn(BaseModel):
    body: str


class ChairNoteOut(_Out):
    id: int
    semester_id: int
    kind: str
    author: str
    body: str
    created_at: str
    closed_at: str | None
    closed_note: str | None


# --- Manual (chair) shift assignment ---


class ShiftAssignIn(BaseModel):
    member_slug: str
    force: bool = False


class ShiftAssignOut(_Out):
    shift_id: int
    event_name: str
    event_date: str
    shift_type_slug: str
    slot_index: int
    member_slug: str
    display_name: str
    replaced_slug: str | None
    warnings: list[str]


# --- GroupMe forwarder (poll health + inbound traffic) ---


class GroupMeGroupHealthOut(_Out):
    """One topic's line in the health payload.

    ``stale`` is computed, not stored — it is ``last_ok_at`` measured against
    the staleness window in ``services.groupme_poll``, surfaced here so the
    dashboard does not have to re-derive a threshold the service already owns.
    """

    slug: str
    label: str
    last_polled_at: str | None
    last_ok_at: str | None
    consecutive_failures: int
    last_error: str | None
    stale: bool


class GroupMeHealthOut(_Out):
    groups: list[GroupMeGroupHealthOut]
    heartbeat_age_seconds: int | None
    healthy: bool


class GroupMeInboundOut(_Out):
    """A message as the chair reads it.

    A deliberate subset of the row: ``groupme_message_id``, ``sender_user_id``
    and ``received_at`` are dedupe and plumbing, and ``sender_user_id`` in
    particular is a real GroupMe identifier that has no business on a screen.
    """

    id: int
    group_slug: str
    sender_name: str
    text: str
    created_at: str
    triage: str | None
    triage_note: str | None


# --- GroupMe ---


class GroupMeIdentityRowOut(_Out):
    member_id: int
    display_name: str
    nickname: str | None
    confidence: str


class GroupMeUnlinkedOut(_Out):
    member_id: int
    display_name: str
    reason: str | None = None


class GroupMeDriftOut(_Out):
    groupme_user_id: str
    nickname: str


class GroupMeBlockedOut(_Out):
    groupme_user_id: str
    member_id: int
    display_name: str
    reason: str
    detail: str


class GroupMeIdentitiesOut(_Out):
    linked: int
    unlinked: list[GroupMeUnlinkedOut]
    drift: list[GroupMeDriftOut]
    rows: list[GroupMeIdentityRowOut] = []
    blocked: list[GroupMeBlockedOut] = []


class GroupMeMembershipAddOut(_Out):
    member_id: int
    display_name: str
    groupme_user_id: str


class GroupMeMembershipRemoveOut(_Out):
    member_id: int
    display_name: str
    membership_id: str
    reason: str


class GroupMeUnrecognisedOut(_Out):
    groupme_user_id: str
    nickname: str


class GroupMeMembershipPlanOut(_Out):
    """The plan PLUS the digest that has to come back to apply it.

    ``preview_id`` and ``digest`` are not decoration: ``POST membership/apply``
    recomputes the plan and refuses unless the digest still matches, so an
    approval cannot outlive the thing it approved."""

    preview_id: str
    digest: str
    add: list[GroupMeMembershipAddOut]
    remove: list[GroupMeMembershipRemoveOut]
    unrecognised: list[GroupMeUnrecognisedOut] = []
    blocked: list[GroupMeBlockedOut] = []
    unlinked_workers: list[GroupMeUnlinkedOut] = []


class GroupMeMentionOut(_Out):
    user_id: str
    display_name: str
    offset: int
    length: int


class GroupMeAnnouncePostOut(_Out):
    group_slug: str
    label: str
    event_date: str
    event_name: str
    text: str
    mentions: list[GroupMeMentionOut]
    unlinked: list[GroupMeUnlinkedOut] = []
    char_count: int = 0
    too_long: bool = False


class GroupMeUnroutableOut(_Out):
    event_date: str
    event_name: str
    weekday: int
    reason: str


class GroupMeAnnouncePreviewOut(_Out):
    preview_id: str
    digest: str
    posts: list[GroupMeAnnouncePostOut]
    unroutable: list[GroupMeUnroutableOut] = []
    oversize: list[GroupMeAnnouncePostOut] = []
    identity_check: str = "database-only"


class GroupMePostedOut(_Out):
    group_slug: str
    label: str
    event_date: str
    event_name: str
    message_id: str | None
    mention_count: int
    outcome: str
    detail: str | None = None


class GroupMeAnnounceResultOut(_Out):
    posted: list[GroupMePostedOut]


class GroupMeMembershipApplyOut(_Out):
    added: list[str]
    removed: list[str]
    results_id: str | None


class GroupMeOkOut(_Out):
    ok: bool


class GroupMeConfirmIn(BaseModel):
    """The window, plus the preview the caller is actually approving.

    ``confirm`` defaults FALSE and every outbound route requires it true — but
    on its own it is a constant, and a constant is satisfied by a hardcoded UI
    and by a chair who read the preview yesterday. ``preview_id`` and ``digest``
    are what make this an approval OF SOMETHING: the server rebuilds the plan and
    refuses if a shift, a nickname, an event date or a topic mapping moved in
    between."""

    on_or_after: str
    on_or_before: str
    confirm: bool = False
    preview_id: str
    digest: str
    semester: str | None = None


class GroupMeReplyIn(BaseModel):
    group_slug: str
    text: str
    confirm: bool = False
