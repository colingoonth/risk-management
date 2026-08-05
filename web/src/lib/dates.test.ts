// The calendar's date arithmetic, exercised under two timezones.
//
// Run under BOTH `TZ=America/New_York` (Colin's machine) and `TZ=UTC` — see the
// `test` / `test:utc` scripts. That is not belt-and-braces: in US Eastern the
// UTC-parse bug and a Sunday-first column index cancel each other out exactly, so
// a naively-built grid renders correctly here and silently breaks anywhere with a
// non-negative UTC offset. A single-timezone run cannot catch it.

import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  addDays,
  dayNumeral,
  fmt,
  isValidIso,
  longDate,
  monthKey,
  monthLabel,
  monthsBetween,
  mondayOf,
  parts,
  shortDate,
  todayLocal,
  weekdayMon0,
  weeksOfMonth,
} from './dates'

describe('parts / fmt', () => {
  it('splits without parsing', () => {
    expect(parts('2026-08-25')).toEqual([2026, 8, 25])
  })

  it('round-trips through a UTC-constructed Date', () => {
    expect(fmt(new Date(Date.UTC(2026, 7, 25)))).toBe('2026-08-25')
  })
})

describe('addDays', () => {
  it('steps forward and back', () => {
    expect(addDays('2026-09-11', 1)).toBe('2026-09-12')
    expect(addDays('2026-09-11', -1)).toBe('2026-09-10')
    expect(addDays('2026-09-11', 0)).toBe('2026-09-11')
  })

  it('rolls over months and years', () => {
    expect(addDays('2026-08-31', 1)).toBe('2026-09-01')
    expect(addDays('2026-12-31', 1)).toBe('2027-01-01')
    expect(addDays('2026-01-01', -1)).toBe('2025-12-31')
  })

  it('handles a leap day', () => {
    expect(addDays('2028-02-28', 1)).toBe('2028-02-29')
    expect(addDays('2027-02-28', 1)).toBe('2027-03-01')
  })

  it('does not duplicate a day across the DST fall-back inside FA26', () => {
    // 2026-11-01 is the US fall-back AND the cleanup morning after the Oct 31
    // party. Millisecond arithmetic yields it twice; this must not.
    const seen: string[] = []
    let cursor = '2026-10-30'
    for (let i = 0; i < 5; i++) {
      seen.push(cursor)
      cursor = addDays(cursor, 1)
    }
    expect(seen).toEqual([
      '2026-10-30',
      '2026-10-31',
      '2026-11-01',
      '2026-11-02',
      '2026-11-03',
    ])
    expect(new Set(seen).size).toBe(seen.length)
  })

  it('does not duplicate a day across the spring-forward either', () => {
    expect(addDays('2027-03-13', 1)).toBe('2027-03-14')
    expect(addDays('2027-03-14', 1)).toBe('2027-03-15')
  })
})

describe('weekdayMon0', () => {
  it('places real FA26 dates in the right column', () => {
    // Verified against the calendar: FA26 opens on a Tuesday.
    expect(weekdayMon0('2026-08-25')).toBe(1) // Tue
    expect(weekdayMon0('2026-09-11')).toBe(4) // Fri
    expect(weekdayMon0('2026-09-10')).toBe(3) // Thu
    expect(weekdayMon0('2026-12-05')).toBe(5) // Sat
  })

  it('puts Sunday last, not first', () => {
    expect(weekdayMon0('2026-11-01')).toBe(6) // Sunday
    expect(weekdayMon0('2026-11-02')).toBe(0) // Monday
  })

  it('agrees with the backend weekday origin used for recurring unavailability', () => {
    // repos/unavailability.py rotates SQLite's Sunday=0 into Python's Monday=0
    // with (strftime('%w') + 6) % 7 and stores Thursday as 3. If these two ever
    // disagree, a "busy Thursdays" row would highlight the wrong column.
    expect(weekdayMon0('2026-09-10')).toBe(3)
    expect(weekdayMon0('2026-10-08')).toBe(3)
  })
})

describe('mondayOf', () => {
  it('returns the date itself on a Monday', () => {
    expect(mondayOf('2026-11-02')).toBe('2026-11-02')
  })

  it('walks back to Monday from any day, including Sunday', () => {
    expect(mondayOf('2026-08-25')).toBe('2026-08-24')
    expect(mondayOf('2026-11-01')).toBe('2026-10-26') // Sunday -> previous Monday
  })
})

describe('weeksOfMonth', () => {
  it('yields full Monday-first weeks covering the month', () => {
    const weeks = weeksOfMonth('2026-09')
    expect(weeks.every((w) => w.length === 7)).toBe(true)
    expect(weeks[0][0]).toBe('2026-08-31') // the Monday before Sep 1
    expect(weeks.flat()).toContain('2026-09-01')
    expect(weeks.flat()).toContain('2026-09-30')
  })

  it('covers a month that begins on a Sunday without dropping it', () => {
    // 2026-11-01 is a Sunday — the classic off-by-one for Monday-first grids.
    const flat = weeksOfMonth('2026-11').flat()
    expect(flat).toContain('2026-11-01')
    expect(flat).toContain('2026-11-30')
  })

  it('covers every day of the FA26 months', () => {
    for (const mk of ['2026-08', '2026-09', '2026-10', '2026-11', '2026-12']) {
      const flat = weeksOfMonth(mk).flat()
      const [y, m] = mk.split('-').map(Number)
      const days = new Date(Date.UTC(y, m, 0)).getUTCDate()
      for (let d = 1; d <= days; d++) {
        expect(flat).toContain(`${mk}-${String(d).padStart(2, '0')}`)
      }
    }
  })

  it('produces strictly consecutive dates with no repeats', () => {
    const flat = weeksOfMonth('2026-11').flat()
    expect(new Set(flat).size).toBe(flat.length)
    for (let i = 1; i < flat.length; i++) {
      expect(flat[i]).toBe(addDays(flat[i - 1], 1))
    }
  })
})

describe('monthsBetween', () => {
  it('spans the FA26 term', () => {
    expect(monthsBetween('2026-08', '2026-12')).toEqual([
      '2026-08', '2026-09', '2026-10', '2026-11', '2026-12',
    ])
  })

  it('crosses a year boundary', () => {
    expect(monthsBetween('2026-11', '2027-02')).toEqual([
      '2026-11', '2026-12', '2027-01', '2027-02',
    ])
  })

  it('returns a single month when from === to, and nothing when reversed', () => {
    expect(monthsBetween('2026-09', '2026-09')).toEqual(['2026-09'])
    expect(monthsBetween('2026-10', '2026-09')).toEqual([])
  })
})

describe('isValidIso', () => {
  it('accepts real dates', () => {
    expect(isValidIso('2026-08-25')).toBe(true)
    expect(isValidIso('2028-02-29')).toBe(true)
  })

  it('rejects what the bulk loader can actually write', () => {
    // events.date has no CHECK and load_events.py passes CSV values through.
    expect(isValidIso('2026-02-30')).toBe(false)
    expect(isValidIso('2026-13-45')).toBe(false)
    expect(isValidIso('09/15/2026')).toBe(false)
    expect(isValidIso('')).toBe(false)
    expect(isValidIso('2026-9-5')).toBe(false)
  })
})

describe('display helpers', () => {
  it('takes the day numeral off the string, not off a Date', () => {
    expect(dayNumeral('2026-08-25')).toBe('25')
    expect(dayNumeral('2026-09-01')).toBe('01')
  })

  it('formats a long date with the right weekday', () => {
    expect(longDate('2026-09-19')).toBe('Saturday, 19 September 2026')
    expect(longDate('2026-08-25')).toBe('Tuesday, 25 August 2026')
  })

  it('passes a malformed date through rather than throwing', () => {
    expect(longDate('not-a-date')).toBe('not-a-date')
  })

  it('labels months and keys', () => {
    expect(monthLabel('2026-09')).toBe('September 2026')
    expect(monthKey('2026-09-19')).toBe('2026-09')
  })
})

describe('shortDate', () => {
  it('names the day a crew actually works', () => {
    // This string is the only thing telling a chair WHICH morning the cleanup
    // crew is on, so a silent weekday error here misroutes four people.
    expect(shortDate('2026-09-13')).toBe('Sun 13 Sep')
    expect(shortDate('2026-09-12')).toBe('Sat 12 Sep')
    expect(shortDate('2026-11-01')).toBe('Sun 1 Nov')
    expect(shortDate('2026-08-25')).toBe('Tue 25 Aug')
  })

  it('agrees with longDate about the weekday', () => {
    for (const iso of ['2026-08-25', '2026-09-13', '2026-11-01', '2026-12-05']) {
      expect(longDate(iso).startsWith(shortDate(iso).slice(0, 3))).toBe(true)
    }
  })

  it('passes a malformed date through rather than throwing', () => {
    expect(shortDate('nope')).toBe('nope')
  })
})

describe('todayLocal', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('reads the LOCAL wall clock, not UTC', () => {
    // 2026-08-29T01:00Z is 2026-08-28 21:00 in New York — inside the 20:00-23:59
    // event-night window, i.e. exactly when the chair is holding this at a party.
    // toISOString() would answer "the 29th" and shift both the today marker and
    // the overdue alarm onto a party that is still in progress.
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-29T01:00:00Z'))
    const offsetMinutes = new Date('2026-08-29T01:00:00Z').getTimezoneOffset()
    const expected = offsetMinutes > 0 ? '2026-08-28' : '2026-08-29'
    expect(todayLocal()).toBe(expected)
    // And it must never simply be the UTC slice in a negative-offset zone.
    if (offsetMinutes > 0) {
      expect(todayLocal()).not.toBe(new Date().toISOString().slice(0, 10))
    }
  })

  it('is a well-formed ISO date that round-trips', () => {
    // Kills "return a constant" and off-by-one mutants under every timezone.
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-12T15:00:00Z'))
    const t = todayLocal()
    expect(isValidIso(t)).toBe(true)
    const now = new Date()
    expect(t).toBe(
      `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(
        now.getDate(),
      ).padStart(2, '0')}`,
    )
  })
})

describe('the timezone trap itself', () => {
  it('never lets a naive Date parse decide a day numeral', () => {
    // This is the assertion that fails if someone reaches for `new Date(iso)`.
    // In a negative-offset zone it yields 24; the string always yields 25.
    expect(dayNumeral('2026-08-25')).toBe('25')
    expect(longDate('2026-08-25').startsWith('Tuesday, 25')).toBe(true)
  })

  it('is stable regardless of the host offset', () => {
    // Same inputs, same outputs, whichever TZ the suite is run under. Both
    // TZ=America/New_York and TZ=UTC must satisfy these.
    expect(weekdayMon0('2026-08-25')).toBe(1)
    expect(addDays('2026-08-25', 1)).toBe('2026-08-26')
    expect(weeksOfMonth('2026-08')[0][0]).toBe('2026-07-27')
  })
})
