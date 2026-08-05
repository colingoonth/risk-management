// Turning the API's flat event list into what a month grid needs. Pure and
// UI-free — no React import — so the awkward parts are testable on their own.

import type { EventSummaryRow, ShiftTypeWindow } from './types'
import { addDays, isValidIso, monthKey } from './dates'

export type Planning = 'confirmed' | 'potential' | 'placeholder' | null

/**
 * Strip the disambiguating ` (YYYY-MM-DD)` suffix — but ONLY when the date it
 * carries is this event's own date.
 *
 * That suffix is not decoration. `events` carries UNIQUE(semester_id,
 * display_name) and a real schedule repeats names, so the loader appends the date
 * to make them unique; `events_repo.resolve()` then looks events up BY
 * display_name, which means the suffixed string is the key a chair types into
 * `risk event auto-assign`. Blind-regexing it off anywhere it appears would
 * quietly mangle an event genuinely named that way.
 *
 * Safe here because the cell IS the date, so the suffix is pure redundancy — and
 * with 29 of 43 FA26 names carrying one, dropping it is the single biggest
 * legibility win available in a ~140px cell. The dock still shows the full name.
 */
export function cleanName(displayName: string, eventDate: string): string {
  const match = /^(.*) \((\d{4}-\d{2}-\d{2})\)$/.exec(displayName)
  if (match && match[2] === eventDate) return match[1]
  return displayName
}

/**
 * The planning status the loader preserved in `notes`.
 *
 * `events.status` only permits created/assigned/completed/cancelled, so the CSV's
 * planning vocabulary (confirmed / potential / placeholder) is stashed behind a
 * greppable `status=` prefix in notes instead. 11 of 43 FA26 events are
 * placeholders; they must read as provisional without vanishing.
 */
export function planningOf(notes: string | null): Planning {
  if (!notes) return null
  const match = /^status=([a-z_]+)/.exec(notes.trim())
  if (!match) return null
  const word = match[1]
  if (word === 'confirmed' || word === 'potential' || word === 'placeholder') return word
  return null
}

export type DayEntry =
  | { kind: 'event'; ev: EventSummaryRow }
  | {
      kind: 'ghost'
      shiftTypeSlug: string
      target: number
      assigned: number
      parent: EventSummaryRow
      workedOn: string
    }

export interface DayMap {
  /** ISO date -> entries, events first then ghosts. */
  days: Map<string, DayEntry[]>
  /** Events whose date is malformed or implausibly far outside the term. */
  strays: EventSummaryRow[]
}

function push(days: Map<string, DayEntry[]>, iso: string, entry: DayEntry): void {
  const list = days.get(iso)
  if (list) list.push(entry)
  else days.set(iso, [entry])
}

/**
 * Place every event on its own date, and every fixed-day off-date crew on the day
 * it is actually worked.
 *
 * THE RULE, drawn from the shift-window data rather than invented: a window with
 * `offset_days_start === offset_days_end` is an APPOINTMENT and earns a mark on
 * that day. A window that spans days is a TASK WITH A DEADLINE and gets none.
 *
 * So cleanup (+1..+1, 00:00-12:00 — a fixed Saturday morning) produces a ghost
 * line on the following day, and setup (-2..0, 08:00-23:59 — "sometime in the
 * next three days") produces nothing. Marking setup would smear one Friday party
 * across three squares, and with 43 events over fifteen weeks the grid lights up
 * almost everywhere and the at-a-glance property dies. Setup is still fully
 * visible in the dock, labelled with its real window.
 *
 * Ghosts are POINTERS, never status: they carry no fill bar, never fire the
 * overdue alarm, and are never counted in any total. The event owns its whole
 * denominator on its own date, so the numbers can only be added up one way.
 */
export function buildDays(
  events: EventSummaryRow[],
  windows: ShiftTypeWindow[],
  bounds?: { from: string; to: string },
): DayMap {
  const days = new Map<string, DayEntry[]>()
  const strays: EventSummaryRow[] = []
  const fixedOffset = new Map<string, number>()
  for (const w of windows) {
    if (w.offset_days_start === w.offset_days_end && w.offset_days_start !== 0) {
      fixedOffset.set(w.shift_type_slug, w.offset_days_start)
    }
  }

  for (const ev of events) {
    if (!isValidIso(ev.date) || (bounds && (ev.date < bounds.from || ev.date > bounds.to))) {
      strays.push(ev)
      continue
    }
    push(days, ev.date, { kind: 'event', ev })
  }

  // Ghosts second, so every day's events sort ahead of its carried-in crews.
  for (const ev of events) {
    if (ev.status === 'cancelled') continue
    if (!isValidIso(ev.date)) continue
    if (bounds && (ev.date < bounds.from || ev.date > bounds.to)) continue
    for (const group of ev.by_type ?? []) {
      const offset = fixedOffset.get(group.shift_type_slug)
      if (offset === undefined || group.target_count <= 0) continue
      const workedOn = addDays(ev.date, offset)
      push(days, workedOn, {
        kind: 'ghost',
        shiftTypeSlug: group.shift_type_slug,
        target: group.target_count,
        assigned: group.assigned_count,
        parent: ev,
        workedOn,
      })
    }
  }

  return { days, strays }
}

export interface Rollup {
  eventCount: number
  target: number
  assigned: number
  unstaffed: number
}

/**
 * Term and month totals.
 *
 * Cancelled events are excluded from every figure, matching the dashboard router,
 * so the calendar's headline numbers and the Register's cannot disagree. Ghosts
 * are never counted — a month's numbers are exactly the sum of its own events.
 *
 * `unstaffed` counts EVENTS with a hole, not slots. "43 unstaffed" is a to-do
 * list; "586 open" is a number nobody can act on.
 */
export function rollup(events: EventSummaryRow[], mk?: string): Rollup {
  let eventCount = 0
  let target = 0
  let assigned = 0
  let unstaffed = 0
  for (const ev of events) {
    if (ev.status === 'cancelled') continue
    if (mk && monthKey(ev.date) !== mk) continue
    eventCount += 1
    target += ev.target_slots
    assigned += ev.assigned_slots
    if (ev.open_slots > 0) unstaffed += 1
  }
  return { eventCount, target, assigned, unstaffed }
}

/** Does this cell need the overdue alarm? Same gate the Register already uses. */
export function isOverdue(ev: EventSummaryRow, today: string): boolean {
  return ev.status !== 'cancelled' && ev.date < today && ev.open_slots > 0
}
