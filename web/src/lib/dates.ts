// ISO date arithmetic for the calendar. Pure, UI-free, and the ONLY place in the
// frontend allowed to touch `Date`.
//
// THE RULE: `YYYY-MM-DD` is an opaque string key end to end. Never construct a JS
// Date from an event or semester date to decide where it goes or what it says.
//
// This is not fussiness. Three separate hazards, all verified in this repo's own
// timezone (America/New_York) and all silent:
//
//   new Date('2026-08-25').getDate()          -> 24
//     The string is parsed as UTC midnight, which is the previous evening in any
//     negative-offset zone. The term would open on the wrong square.
//
//   new Date().toISOString().slice(0, 10)     -> tomorrow, from 20:00 onward
//     20:00-23:59 is exactly the event-night shift window seeded in migration
//     0012 — i.e. precisely when the chair is holding the app at a party.
//
//   stepping a day with `+ 86_400_000`        -> 2026-11-01 twice
//     US fall-back. That date is inside FA26 AND is the cleanup morning after the
//     Oct 31 party, so the crew would render on a duplicated cell.
//
// AND THE TRAP THAT HIDES ALL OF IT: in US Eastern the first two bugs cancel
// against a Sunday-first column index. The UTC parse shifts the date back one
// day; Monday-first is Sunday-first minus one. So a naively-built grid renders
// perfectly on this machine and breaks under TZ=UTC or any positive offset.
// The tests run under both timezones for that reason — see dates.test.ts.

const pad = (n: number) => String(n).padStart(2, '0')

export const MONTH_ABBR = [
  'JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN',
  'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC',
]

export const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

const WEEKDAY_NAMES = [
  'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday',
]

/** `'2026-08-25'` -> `[2026, 8, 25]`. Split, never parse. */
export function parts(iso: string): [number, number, number] {
  const [y, m, d] = iso.split('-').map(Number)
  return [y, m, d]
}

/** Read a UTC-constructed Date back out as `YYYY-MM-DD`. Always getUTC*. */
export function fmt(dt: Date): string {
  return `${dt.getUTCFullYear()}-${pad(dt.getUTCMonth() + 1)}-${pad(dt.getUTCDate())}`
}

/**
 * Step `n` days from an ISO date.
 *
 * `Date.UTC` normalises month and year rollover for free and is DST-immune
 * because UTC has no DST. Millisecond arithmetic is forbidden here — see the
 * 2026-11-01 note at the top of this file.
 */
export function addDays(iso: string, n: number): string {
  const [y, m, d] = parts(iso)
  return fmt(new Date(Date.UTC(y, m - 1, d + n)))
}

/**
 * Column index with MONDAY = 0.
 *
 * The `+ 6) % 7` rotation deliberately mirrors `repos/unavailability.py`, which
 * rotates SQLite's Sunday-origin into Python's Monday-origin for exactly the same
 * reason. Monday=0 is already this codebase's committed weekday convention
 * (`services/availability.py` stores Python `weekday()`), and adopting a second
 * origin in the one view that will later overlay weekday-indexed unavailability
 * data is how columns silently shift by one.
 */
export function weekdayMon0(iso: string): number {
  const [y, m, d] = parts(iso)
  return (new Date(Date.UTC(y, m - 1, d)).getUTCDay() + 6) % 7
}

export function mondayOf(iso: string): string {
  return addDays(iso, -weekdayMon0(iso))
}

/** `'2026-08-25'` -> `'2026-08'`. */
export function monthKey(iso: string): string {
  return iso.slice(0, 7)
}

/** Inclusive list of `YYYY-MM` keys from one month to another. */
export function monthsBetween(fromKey: string, toKey: string): string[] {
  if (fromKey > toKey) return []
  const out: string[] = []
  let [y, m] = fromKey.split('-').map(Number)
  for (let guard = 0; guard < 600; guard++) {
    const key = `${y}-${pad(m)}`
    out.push(key)
    if (key === toKey) break
    m += 1
    if (m > 12) {
      m = 1
      y += 1
    }
  }
  return out
}

/**
 * A month as Monday-first weeks of seven ISO keys, padded with the adjacent
 * months' days so every row is full and columns stay aligned.
 */
export function weeksOfMonth(mk: string): string[][] {
  const [y, m] = mk.split('-').map(Number)
  const first = `${y}-${pad(m)}-01`
  const daysInMonth = new Date(Date.UTC(y, m, 0)).getUTCDate()
  const last = `${y}-${pad(m)}-${pad(daysInMonth)}`

  const weeks: string[][] = []
  let cursor = mondayOf(first)
  for (let guard = 0; guard < 8; guard++) {
    const week = Array.from({ length: 7 }, (_, i) => addDays(cursor, i))
    weeks.push(week)
    if (week[6] >= last) break
    cursor = addDays(cursor, 7)
  }
  return weeks
}

/**
 * A real calendar date in `YYYY-MM-DD`.
 *
 * `events.date` is unvalidated TEXT with no CHECK, and the bulk loader writes CSV
 * values through verbatim, so malformed dates are reachable. The round-trip check
 * is what rejects 2026-02-30, which the regex alone would accept.
 */
export function isValidIso(s: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) return false
  const [y, m, d] = parts(s)
  if (m < 1 || m > 12 || d < 1) return false
  return fmt(new Date(Date.UTC(y, m - 1, d))) === s
}

/**
 * Today, on the LOCAL wall clock.
 *
 * Never `toISOString()`: that is UTC and returns tomorrow from 20:00 Eastern
 * onward, which is the exact window a chair is using this at a party.
 */
export function todayLocal(): string {
  const n = new Date()
  return `${n.getFullYear()}-${pad(n.getMonth() + 1)}-${pad(n.getDate())}`
}

/** `'2026-09-19'` -> `'Saturday, 19 September 2026'`. */
export function longDate(iso: string): string {
  if (!isValidIso(iso)) return iso
  const [y, m, d] = parts(iso)
  return `${WEEKDAY_NAMES[weekdayMon0(iso)]}, ${d} ${MONTH_NAMES[m - 1]} ${y}`
}

/** `'2026-09-19'` -> `'Sat 19 Sep'`. */
export function shortDate(iso: string): string {
  if (!isValidIso(iso)) return iso
  const [, m, d] = parts(iso)
  const wd = WEEKDAY_NAMES[weekdayMon0(iso)].slice(0, 3)
  return `${wd} ${d} ${MONTH_NAMES[m - 1].slice(0, 3)}`
}

/** `'2026-09'` -> `'September 2026'`. */
export function monthLabel(mk: string): string {
  const [y, m] = mk.split('-').map(Number)
  return `${MONTH_NAMES[m - 1]} ${y}`
}

/** `'2026-09-19'` -> `'19'`, without a Date round-trip. */
export function dayNumeral(iso: string): string {
  return iso.slice(8, 10)
}
