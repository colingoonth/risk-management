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
    effective_pledge_mode_slug: str | None
    status: str
    assigned_at: str | None


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
