// Thin typed client over the FastAPI layer. All calls hit /api (dev: proxied
// to the local risk-api server; same-origin in any future bundled deploy).

import type {
  ArchiveReport,
  ArchiveResult,
  AutoAssignResult,
  ChairNote,
  Dashboard,
  EventRow,
  EventSummaryRow,
  EventType,
  GroupMeAnnounceResult,
  GroupMeAnnouncementPreview,
  GroupMeHealth,
  GroupMeIdentities,
  GroupMeInboundMessage,
  GroupMeMembershipPlan,
  GroupMeReplyResult,
  House,
  HouseMode,
  Member,
  NoteAuthor,
  NoteKind,
  NumberedStrike,
  PendingConsequence,
  RemovalMethod,
  RosterIngestResult,
  Semester,
  Shift,
  ShiftAssign,
  ShiftTypeWindow,
  StrikeRemovalResult,
  SwapRequest,
} from './types'

export class ApiError extends Error {
  status: number
  detail: string
  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    headers: init?.body && !(init.body instanceof FormData) ? { 'Content-Type': 'application/json' } : undefined,
    ...init,
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.detail ?? detail
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

const post = (path: string, body?: unknown) =>
  req<unknown>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })

export const api = {
  meta: () => req<{ version: string; current_semester: Semester | null }>('/meta'),
  dashboard: (semester?: string) =>
    req<Dashboard>(`/dashboard${semester ? `?semester=${encodeURIComponent(semester)}` : ''}`),

  listSemesters: () => req<Semester[]>('/semesters'),
  setCurrentSemester: (name: string) =>
    req<Semester>(`/semesters/${encodeURIComponent(name)}/set-current`, { method: 'POST' }),
  createSemester: (body: { name: string; starts_on: string; ends_on: string }) =>
    req<Semester>('/semesters', { method: 'POST', body: JSON.stringify(body) }),
  setHouseMode: (semester: string, house_slug: string, pledge_mode_slug: string) =>
    req<unknown>(`/semesters/${encodeURIComponent(semester)}/house-modes`, {
      method: 'POST',
      body: JSON.stringify({ house_slug, pledge_mode_slug }),
    }),
  listHouseModes: (semester: string) =>
    req<HouseMode[]>(`/semesters/${encodeURIComponent(semester)}/house-modes`),

  archiveCheck: (semester: string) =>
    req<ArchiveReport>(`/semesters/${encodeURIComponent(semester)}/archive-check`),
  archiveSemester: (semester: string, body: { force?: boolean; carry_to?: string | null }) =>
    req<ArchiveResult>(`/semesters/${encodeURIComponent(semester)}/archive`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  listHouses: () => req<House[]>('/houses'),
  listEventTypes: () => req<EventType[]>('/event-types'),
  listMembers: () => req<Member[]>('/members'),

  listEvents: (semester?: string) =>
    req<EventRow[]>(`/events${semester ? `?semester=${encodeURIComponent(semester)}` : ''}`),
  // Every event in the term WITH its staffing rollup — what the calendar paints
  // from. Deliberately not folded into listEvents: the rollup costs two extra
  // aggregates and only one page needs it.
  listEventSummaries: (semester?: string) =>
    req<EventSummaryRow[]>(
      `/events/summary${semester ? `?semester=${encodeURIComponent(semester)}` : ''}`,
    ),
  listShiftTypeWindows: () => req<ShiftTypeWindow[]>('/shift-type-windows'),
  createEvent: (
    body: { event_type_slug: string; display_name: string; date: string; host_house_slug?: string | null },
    semester?: string,
  ) =>
    req<EventRow>(`/events${semester ? `?semester=${encodeURIComponent(semester)}` : ''}`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  eventShifts: (eventId: number) => req<Shift[]>(`/events/${eventId}/shifts`),
  setEventHost: (eventId: number, host_house_slug: string | null) =>
    req<EventRow>(`/events/${eventId}/set-host`, {
      method: 'POST',
      body: JSON.stringify({ host_house_slug }),
    }),
  cancelEvent: (eventId: number) => post(`/events/${eventId}/cancel`),
  autoAssign: (eventId: number, body: { seed?: number; reassign?: boolean } = {}, dryRun = false) =>
    req<AutoAssignResult>(`/events/${eventId}/auto-assign${dryRun ? '?dry_run=true' : ''}`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  autoAssignBulk: (semester?: string, body: { seed?: number; reassign?: boolean } = {}) =>
    req<AutoAssignResult[]>(
      `/events/auto-assign-bulk${semester ? `?semester=${encodeURIComponent(semester)}` : ''}`,
      { method: 'POST', body: JSON.stringify(body) },
    ),

  listShifts: (params: { member?: string; semester?: string; status?: string; event?: number } = {}) => {
    const q = new URLSearchParams()
    if (params.member) q.set('member', params.member)
    if (params.semester) q.set('semester', params.semester)
    if (params.status) q.set('status', params.status)
    if (params.event !== undefined) q.set('event', String(params.event))
    const qs = q.toString()
    return req<Shift[]>(`/shifts${qs ? `?${qs}` : ''}`)
  },

  listNotes: (params: { openOnly?: boolean; kind?: string } = {}) => {
    const q = new URLSearchParams()
    if (params.openOnly) q.set('open_only', 'true')
    if (params.kind) q.set('kind', params.kind)
    const qs = q.toString()
    return req<ChairNote[]>(`/notes${qs ? `?${qs}` : ''}`)
  },
  createNote: (body: { body: string; kind?: NoteKind; author?: NoteAuthor }) =>
    req<ChairNote>('/notes', { method: 'POST', body: JSON.stringify(body) }),
  closeNote: (id: number, closed_note?: string) =>
    req<ChairNote>(`/notes/${id}/close`, {
      method: 'POST',
      body: JSON.stringify({ closed_note: closed_note ?? null }),
    }),
  reopenNote: (id: number) => req<ChairNote>(`/notes/${id}/reopen`, { method: 'POST' }),
  editNote: (id: number, body: string) =>
    req<ChairNote>(`/notes/${id}`, { method: 'PUT', body: JSON.stringify({ body }) }),
  deleteNote: (id: number) => req<void>(`/notes/${id}`, { method: 'DELETE' }),

  // Chair overrides. The assignment is marked chair-set server-side, so a
  // rebuild preserves it. `force` assigns despite eligibility problems and
  // returns them as warnings rather than swallowing them.
  assignShift: (id: number, member_slug: string, force = false) =>
    req<ShiftAssign>(`/shifts/${id}/assign`, {
      method: 'POST',
      body: JSON.stringify({ member_slug, force }),
    }),
  unassignShift: (id: number) => req<Shift>(`/shifts/${id}/unassign`, { method: 'POST' }),

  listSwaps: (state?: string) =>
    req<SwapRequest[]>(`/swaps${state ? `?state=${encodeURIComponent(state)}` : ''}`),
  createSwap: (
    body: { from_shift_id: number; to_shift_id?: number; counterparty_member_slug?: string },
    semester?: string,
  ) =>
    req<SwapRequest>(`/swaps${semester ? `?semester=${encodeURIComponent(semester)}` : ''}`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  acceptSwap: (id: number) => post(`/swaps/${id}/accept`),
  rejectSwap: (id: number) => post(`/swaps/${id}/reject`),
  cancelSwap: (id: number) => post(`/swaps/${id}/cancel`),

  listStrikes: (member: string, semester?: string) =>
    req<NumberedStrike[]>(
      `/strikes?member=${encodeURIComponent(member)}${semester ? `&semester=${encodeURIComponent(semester)}` : ''}`,
    ),
  issueStrike: (
    body: { member_slug: string; issued_on: string; reason: string },
    semester?: string,
  ) =>
    req<NumberedStrike>(`/strikes${semester ? `?semester=${encodeURIComponent(semester)}` : ''}`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  removeStrikes: (body: {
    member_slug: string
    removal_method_slug: string
    performed_on: string
    strike_ids: number[]
    semester?: string
    notes?: string
  }) => req<StrikeRemovalResult>('/strikes/remove', { method: 'POST', body: JSON.stringify(body) }),
  listRemovalMethods: () => req<RemovalMethod[]>('/removal-methods'),

  listConsequences: (state = 'pending') =>
    req<PendingConsequence[]>(`/consequences?state=${encodeURIComponent(state)}`),
  resolveConsequence: (id: number, newState = 'served') =>
    post(`/consequences/${id}/resolve?new_state=${encodeURIComponent(newState)}`),

  ingestRoster: (file: File, semester: string, dryRun: boolean) => {
    const form = new FormData()
    form.append('file', file)
    form.append('semester', semester)
    form.append('dry_run', String(dryRun))
    return req<RosterIngestResult>('/ingest/gform-roster', { method: 'POST', body: form })
  },

  groupmeHealth: () => req<GroupMeHealth>('/groupme/health'),
  groupmeInbound: (params: { limit?: number; triage?: string } = {}) => {
    const q = new URLSearchParams()
    q.set('limit', String(params.limit ?? 50))
    if (params.triage !== undefined) q.set('triage', params.triage)
    return req<GroupMeInboundMessage[]>(`/groupme/inbound?${q.toString()}`)
  },
  groupmeIdentities: () => req<GroupMeIdentities>('/groupme/identities'),
  groupmeMembershipPlan: (on_or_after: string, on_or_before: string) => {
    const q = new URLSearchParams({ on_or_after, on_or_before })
    return req<GroupMeMembershipPlan>(`/groupme/membership-plan?${q.toString()}`)
  },
  groupmeAnnouncePreview: (on_or_after: string, on_or_before: string) => {
    const q = new URLSearchParams({ on_or_after, on_or_before })
    return req<GroupMeAnnouncementPreview>(`/groupme/announce-preview?${q.toString()}`)
  },
  groupmeAnnounce: (body: { on_or_after: string; on_or_before: string; confirm: true }) =>
    req<GroupMeAnnounceResult>('/groupme/announce', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  groupmeReply: (body: { group_slug: string; text: string }) =>
    req<GroupMeReplyResult>('/groupme/reply', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  groupmeMembershipApply: (body: {
    on_or_after: string
    on_or_before: string
    confirm: true
  }) =>
    req<unknown>('/groupme/membership/apply', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  groupmePoll: () => req<GroupMeHealth>('/groupme/poll', { method: 'POST' }),
}
