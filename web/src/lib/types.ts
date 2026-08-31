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

export interface ShiftSlotGroup {
  shift_type_slug: string
  target_count: number
  assigned_count: number
}

// A strict superset of EventRow — the backend model is declared field-for-field
// against EventOut for exactly this reason, so the two cannot drift.
export interface EventSummaryRow extends EventRow {
  target_slots: number // SUM(event_shift_requirements.target_count)
  assigned_slots: number // RAW — may exceed target_slots when slots are orphaned
  open_slots: number // clamped at 0. The alarm number.
  orphan_slots: number // clamped at 0. Surfaced rather than hidden.
  by_type: ShiftSlotGroup[]
}

// WHEN a shift type is worked, relative to the event date. Static, six rows.
// The calendar reads cleanup's +1 from here rather than hardcoding it.
export interface ShiftTypeWindow {
  shift_type_slug: string
  offset_days_start: number
  offset_days_end: number
  window_start_time: string
  window_end_time: string
  min_contiguous_minutes: number | null
  occupies_event_night: boolean
}

export interface ShiftAssign {
  shift_id: number
  event_name: string
  event_date: string
  shift_type_slug: string
  slot_index: number
  member_slug: string
  display_name: string
  replaced_slug: string | null
  /** Eligibility objections that were overridden with force. */
  warnings: string[]
}

export interface Shift {
  id: number
  event_id: number
  shift_type_slug: string
  slot_index: number
  assigned_member_id: number | null
  assigned_member_slug: string | null
  assigned_member_display_name: string | null
  effective_pledge_mode_slug: string | null
  status: string
  assigned_at: string | null
  /** Placed by the chair rather than the fill — a rebuild leaves it alone. */
  chair_set: boolean
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

export interface EventType {
  slug: string
  display_name: string
  has_shift_defaults: boolean
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

export type NoteKind = 'one_off' | 'standing'
export type NoteAuthor = 'chair' | 'claude'

export interface ChairNote {
  id: number
  semester_id: number
  kind: NoteKind
  author: NoteAuthor
  body: string
  created_at: string
  /** null while the note still applies — done, for a one-off; retired, for a standing rule. */
  closed_at: string | null
  /** What was actually done about it. */
  closed_note: string | null
}

export interface GroupMeHealthGroup {
  slug: string
  label: string
  last_polled_at: string | null
  last_ok_at: string | null
  consecutive_failures: number
  last_error: string | null
  stale: boolean
}

export interface GroupMeHealth {
  groups: GroupMeHealthGroup[]
  heartbeat_age_seconds: number | null
  healthy: boolean
}

export type GroupMeTriage = 'urgent' | 'noted' | 'handled' | null

export interface GroupMeInboundMessage {
  id: number
  group_slug: string
  sender_name: string
  text: string
  created_at: string
  triage: GroupMeTriage
  triage_note: string | null
}

export interface GroupMeUnlinkedIdentity {
  member_id: number
  display_name: string
  /** Why this one cannot be mentioned, when the server knows. */
  reason: string | null
}

export interface GroupMeIdentityDrift {
  groupme_user_id: string
  nickname: string
}

export interface GroupMeBlockedIdentity {
  groupme_user_id: string
  member_id: number
  display_name: string
  reason: string
  detail: string
}

export interface GroupMeIdentityRow {
  member_id: number
  display_name: string
  nickname: string | null
  confidence: string
}

export interface GroupMeIdentities {
  linked: number
  unlinked: GroupMeUnlinkedIdentity[]
  drift: GroupMeIdentityDrift[]
  rows: GroupMeIdentityRow[]
  blocked: GroupMeBlockedIdentity[]
}

export interface GroupMeMembershipAddition {
  member_id: number
  display_name: string
  groupme_user_id: string
}

export interface GroupMeMembershipRemoval {
  member_id: number
  display_name: string
  membership_id: string
  reason: string
}

export interface GroupMeUnrecognisedAccount {
  groupme_user_id: string
  nickname: string
}

/**
 * A preview the chair can approve: the plan PLUS the two values that have to
 * come back with the approval.
 *
 * `preview_id` is issued with the plan and expires (fifteen minutes, and never
 * across an API restart); `digest` is a hash of exactly what would be sent.
 * `POST /groupme/membership/apply` and `POST /groupme/announce` rebuild the plan
 * from live data and refuse unless both still hold, so a client that drops
 * either of these can approve nothing at all.
 */
export interface GroupMeApprovable {
  preview_id: string
  digest: string
}

export interface GroupMeMembershipPlan extends GroupMeApprovable {
  add: GroupMeMembershipAddition[]
  remove: GroupMeMembershipRemoval[]
  unrecognised: GroupMeUnrecognisedAccount[]
  blocked: GroupMeBlockedIdentity[]
  unlinked_workers: GroupMeUnlinkedIdentity[]
}

export interface GroupMeMention {
  user_id: string
  display_name: string
  offset: number
  length: number
}

export interface GroupMeAnnouncementPost {
  group_slug: string
  /** The destination topic as the chapter names it — what the chair reads. */
  label: string
  event_date: string
  event_name: string
  text: string
  mentions: GroupMeMention[]
  /** Crew on this shift who cannot be @-ed, so they will not see the post. */
  unlinked: GroupMeUnlinkedIdentity[]
  char_count: number
  too_long: boolean
}

export interface GroupMeUnroutableEvent {
  event_date: string
  event_name: string
  weekday: number
  reason: string
}

export interface GroupMeAnnouncementPreview extends GroupMeApprovable {
  posts: GroupMeAnnouncementPost[]
  unroutable: GroupMeUnroutableEvent[]
  /** Over GroupMe's 1000-character limit. Reported here, not sent. */
  oversize: GroupMeAnnouncementPost[]
  /** `live` when membership was checked against GroupMe, `database-only` when
   * the API could not be reached and the mentions are unverified. */
  identity_check: string
}

export interface GroupMePosted {
  group_slug: string
  label: string
  event_date: string
  event_name: string
  message_id: string | null
  mention_count: number
  outcome: string
  detail: string | null
}

export interface GroupMeAnnounceResult {
  posted: GroupMePosted[]
}

export interface GroupMeMembershipApplyResult {
  added: string[]
  removed: string[]
  results_id: string | null
}

export interface GroupMeReplyResult {
  ok: true
}
