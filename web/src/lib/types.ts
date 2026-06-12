// Mirrors the FastAPI Pydantic DTOs in src/risk/api/schemas.py.

export interface Semester {
  id: number
  name: string
  starts_on: string
  ends_on: string
  is_current: boolean
  pledge_takeover_starts_on: string | null
  archived_at: string | null
}

export interface ArchiveReport {
  semester_id: number
  semester_name: string
  open_strike_count: number
  pending_consequence_count: number
  future_event_count: number
  open_swap_count: number
  blockers: string[]
  is_blocked: boolean
}

export interface ArchiveResult {
  semester_id: number
  archived_at: string
  strikes_closed: number
  strikes_carried_forward: number
  consequences_carried_forward: number
  swaps_cancelled: number
  carry_to_semester_id: number | null
}

export interface Member {
  id: number
  slug: string
  display_name: string
  status_slug: string
  class_year: number | null
  pledge_class: string | null
  notes: string | null
}

export interface EventRow {
  id: number
  semester_id: number
  display_name: string
  date: string
  start_time: string | null
  end_time: string | null
  status: string
  resync_pending: boolean
  notes: string | null
  semester_name: string
  event_type_slug: string
  host_house_slug: string | null
}

export interface Shift {
  id: number
  event_id: number
  shift_type_slug: string
  slot_index: number
  assigned_member_id: number | null
  assigned_member_slug: string | null
  effective_pledge_mode_slug: string | null
  status: string
  assigned_at: string | null
}

export interface ProposedAssignment {
  shift_type_slug: string
  slot_index: number
  member_slug: string | null
  score: number | null
  reason: string
}

export interface AutoAssignResult {
  event_id: number
  event_name: string
  resolved_mode: string
  configured_mode: string
  seed: number
  eligible_count: number
  assignments: ProposedAssignment[]
  warnings: string[]
}

export interface UnfilledEvent {
  event_id: number
  display_name: string
  date: string
  event_type_slug: string
  host_house_slug: string | null
  open_slots: number
  total_slots: number
  resync_pending: boolean
}

export interface MemberLoad {
  member_slug: string
  display_name: string
  shift_count: number
}

export interface SwapRequest {
  id: number
  semester_id: number
  from_shift_id: number
  to_shift_id: number | null
  initiator_slug: string
  counterparty_slug: string | null
  state: string
  created_at: string
  resolved_at: string | null
}

export interface PendingConsequence {
  id: number
  member_id: number
  member_slug: string | null
  semester_id: number
  triggering_strike_id: number
  kind: string
  state: string
  created_at: string
  resolved_at: string | null
}

export interface House {
  slug: string
  display_name: string
}

export interface HouseMode {
  house_slug: string
  pledge_mode_slug: string
}

export interface NumberedStrike {
  id: number
  member_id: number
  semester_id: number
  strike_number: number
  issued_on: string
  reason: string
}

export interface RemovalMethod {
  slug: string
  display_name: string
}

export interface StrikeRemovalResult {
  removal_id: number
  closed_strike_ids: number[]
  active_count_after: number
}

export interface Dashboard {
  semester_name: string
  unfilled_events: UnfilledEvent[]
  pending_swaps: SwapRequest[]
  members_needing_shifts: MemberLoad[]
  pending_consequences: PendingConsequence[]
  resync_pending_count: number
}

export interface RosterIngestResult {
  preview: {
    semester: string
    base_year: number
    total_rows: number
    new_members: number
    existing_members: number
    exec_assignments: number
    unmapped_rising_class: string[]
    unmapped_pledge_class: string[]
  }
  inserted_members?: number
  exec_roles_set?: number
  dry_run?: boolean
}
