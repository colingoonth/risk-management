// Thin typed client over the FastAPI layer. All calls hit /api (dev: proxied
// to the local risk-api server; same-origin in any future bundled deploy).

import type {
  AutoAssignResult,
  Dashboard,
  EventRow,
  Member,
  NumberedStrike,
  PendingConsequence,
  RemovalMethod,
  RosterIngestResult,
  Semester,
  Shift,
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

  listMembers: () => req<Member[]>('/members'),

  listEvents: (semester?: string) =>
    req<EventRow[]>(`/events${semester ? `?semester=${encodeURIComponent(semester)}` : ''}`),
  createEvent: (
    body: { event_type_slug: string; display_name: string; date: string; host_house_slug?: string | null },
    semester?: string,
  ) =>
    req<EventRow>(`/events${semester ? `?semester=${encodeURIComponent(semester)}` : ''}`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  eventShifts: (eventId: number) => req<Shift[]>(`/events/${eventId}/shifts`),
  autoAssign: (eventId: number, body: { seed?: number; reassign?: boolean } = {}) =>
    req<AutoAssignResult>(`/events/${eventId}/auto-assign`, { method: 'POST', body: JSON.stringify(body) }),
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
}
