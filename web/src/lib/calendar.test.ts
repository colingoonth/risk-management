import { describe, expect, it } from 'vitest'
import { buildDays, cleanName, isOverdue, planningOf, rollup } from './calendar'
import type { EventSummaryRow, ShiftTypeWindow } from './types'

// The six real seeded windows, abbreviated to what buildDays reads.
const WINDOWS: ShiftTypeWindow[] = [
  w('driver', 0, 0), w('door', 0, 0), w('bar', 0, 0), w('dj', 0, 0),
  w('setup', -2, 0), w('cleanup', 1, 1),
]

function w(slug: string, start: number, end: number): ShiftTypeWindow {
  return {
    shift_type_slug: slug,
    offset_days_start: start,
    offset_days_end: end,
    window_start_time: '20:00',
    window_end_time: '23:59',
    min_contiguous_minutes: null,
    occupies_event_night: start === 0,
  }
}

function ev(over: Partial<EventSummaryRow> = {}): EventSummaryRow {
  return {
    id: 1,
    semester_id: 1,
    display_name: 'ZTA Mixer',
    date: '2026-09-11',
    start_time: null,
    end_time: null,
    status: 'created',
    resync_pending: false,
    notes: null,
    semester_name: 'FA26',
    event_type_slug: 'mixer',
    host_house_slug: null,
    target_slots: 13,
    assigned_slots: 0,
    open_slots: 13,
    orphan_slots: 0,
    by_type: [
      { shift_type_slug: 'cleanup', target_count: 4, assigned_count: 0 },
      { shift_type_slug: 'setup', target_count: 4, assigned_count: 0 },
      { shift_type_slug: 'door', target_count: 2, assigned_count: 0 },
    ],
    ...over,
  }
}

describe('cleanName', () => {
  it('strips the suffix when it matches the event own date', () => {
    expect(cleanName('SK Mixer (2026-09-11)', '2026-09-11')).toBe('SK Mixer')
  })

  it('leaves the suffix when the date does NOT match', () => {
    // The suffixed string is the lookup key for `risk event auto-assign`, so a
    // blind regex would mangle an event genuinely named this way.
    expect(cleanName('SK Mixer (2026-09-11)', '2026-10-02')).toBe('SK Mixer (2026-09-11)')
  })

  it('leaves ordinary names and other parentheticals alone', () => {
    expect(cleanName('Halloween Krush @ Arena', '2026-10-31')).toBe('Halloween Krush @ Arena')
    expect(cleanName('Mixer (rescheduled)', '2026-09-11')).toBe('Mixer (rescheduled)')
  })
})

describe('planningOf', () => {
  it('reads the vocabulary the loader stashes in notes', () => {
    expect(planningOf('status=placeholder; Thanksgiving week')).toBe('placeholder')
    expect(planningOf('status=confirmed')).toBe('confirmed')
    expect(planningOf('status=potential')).toBe('potential')
  })

  it('returns null for no notes or an unrecognised prefix', () => {
    expect(planningOf(null)).toBe(null)
    expect(planningOf('just a note')).toBe(null)
    expect(planningOf('status=whatever')).toBe(null)
  })
})

describe('buildDays', () => {
  it('places an event on its own date', () => {
    const { days } = buildDays([ev()], WINDOWS)
    expect(days.get('2026-09-11')?.[0]).toMatchObject({ kind: 'event' })
  })

  it('places the cleanup crew on the MORNING AFTER', () => {
    const { days } = buildDays([ev()], WINDOWS)
    const next = days.get('2026-09-12') ?? []
    expect(next).toHaveLength(1)
    expect(next[0]).toMatchObject({ kind: 'ghost', shiftTypeSlug: 'cleanup', target: 4 })
  })

  it('does NOT place setup anywhere — it spans days, so it is not an appointment', () => {
    const { days } = buildDays([ev()], WINDOWS)
    for (const iso of ['2026-09-09', '2026-09-10']) {
      expect(days.get(iso) ?? []).toHaveLength(0)
    }
    const own = days.get('2026-09-11') ?? []
    expect(own.filter((e) => e.kind === 'ghost')).toHaveLength(0)
  })

  it('does not place event-night crews as ghosts', () => {
    const { days } = buildDays([ev()], WINDOWS)
    const own = days.get('2026-09-11') ?? []
    expect(own).toHaveLength(1) // the event itself, no door/driver ghosts
  })

  it('gives a cancelled event no ghost', () => {
    const { days } = buildDays([ev({ status: 'cancelled' })], WINDOWS)
    expect(days.get('2026-09-12') ?? []).toHaveLength(0)
    expect(days.get('2026-09-11')).toHaveLength(1)
  })

  it('emits no ghost for a shift type with a zero target', () => {
    const bare = ev({
      by_type: [{ shift_type_slug: 'cleanup', target_count: 0, assigned_count: 0 }],
    })
    expect(buildDays([bare], WINDOWS).days.get('2026-09-12') ?? []).toHaveLength(0)
  })

  it('sorts events ahead of ghosts on a date that has both', () => {
    // Fri party + Sat party: Saturday holds its own event AND Friday's cleanup.
    const friday = ev({ id: 1, date: '2026-09-11' })
    const saturday = ev({ id: 2, date: '2026-09-12', display_name: 'Krush' })
    const { days } = buildDays([friday, saturday], WINDOWS)
    const sat = days.get('2026-09-12') ?? []
    expect(sat.map((e) => e.kind)).toEqual(['event', 'ghost'])
  })

  it('carries a cleanup crew past the end of the term rather than dropping it', () => {
    // FA26's last event is 2026-12-05; its crew works 2026-12-06, out of bounds.
    const last = ev({ date: '2026-12-05' })
    const { days } = buildDays([last], WINDOWS, { from: '2026-08-25', to: '2026-12-05' })
    expect(days.get('2026-12-06')?.[0]).toMatchObject({ kind: 'ghost' })
  })

  it('routes malformed and far-out-of-range dates to strays instead of the grid', () => {
    const bad = ev({ id: 9, date: '2026-13-45' })
    const far = ev({ id: 10, date: '2027-06-01' })
    const good = ev({ id: 11, date: '2026-09-11' })
    const { days, strays } = buildDays([bad, far, good], WINDOWS, {
      from: '2026-08-25',
      to: '2026-12-31',
    })
    expect(strays.map((e) => e.id).sort((a, b) => a - b)).toEqual([9, 10])
    expect(days.get('2026-09-11')).toHaveLength(1)
  })

  it('tolerates a missing by_type', () => {
    const noGroups = { ...ev(), by_type: undefined } as unknown as EventSummaryRow
    expect(() => buildDays([noGroups], WINDOWS)).not.toThrow()
  })
})

describe('rollup', () => {
  const events = [
    ev({ id: 1, date: '2026-09-11', target_slots: 13, assigned_slots: 13, open_slots: 0 }),
    ev({ id: 2, date: '2026-09-12', target_slots: 13, assigned_slots: 6, open_slots: 7 }),
    ev({ id: 3, date: '2026-10-02', target_slots: 16, assigned_slots: 0, open_slots: 16 }),
    ev({ id: 4, date: '2026-10-03', status: 'cancelled', target_slots: 16, assigned_slots: 4, open_slots: 12 }),
  ]

  it('sums the term, excluding cancelled events entirely', () => {
    expect(rollup(events)).toEqual({
      eventCount: 3,
      target: 42,
      assigned: 19,
      unstaffed: 2,
    })
  })

  it('counts unstaffed EVENTS, not open slots', () => {
    // "2 unstaffed" is a to-do list; "23 open" is a number nobody can act on.
    expect(rollup(events).unstaffed).toBe(2)
  })

  it('scopes to a month when asked', () => {
    expect(rollup(events, '2026-09')).toEqual({
      eventCount: 2, target: 26, assigned: 19, unstaffed: 1,
    })
    expect(rollup(events, '2026-10')).toEqual({
      eventCount: 1, target: 16, assigned: 0, unstaffed: 1,
    })
  })

  it('is all zeroes for an empty term', () => {
    expect(rollup([])).toEqual({ eventCount: 0, target: 0, assigned: 0, unstaffed: 0 })
  })
})

describe('isOverdue', () => {
  const today = '2026-10-01'

  it('fires only for a past date that still has holes', () => {
    expect(isOverdue(ev({ date: '2026-09-11', open_slots: 4 }), today)).toBe(true)
    expect(isOverdue(ev({ date: '2026-09-11', open_slots: 0 }), today)).toBe(false)
    expect(isOverdue(ev({ date: '2026-11-11', open_slots: 4 }), today)).toBe(false)
  })

  it('never fires for a cancelled event', () => {
    // A weekend of cancellations must read as quiet, not as catastrophe.
    expect(isOverdue(ev({ date: '2026-09-11', open_slots: 4, status: 'cancelled' }), today))
      .toBe(false)
  })

  it('does not fire on the day itself', () => {
    expect(isOverdue(ev({ date: today, open_slots: 4 }), today)).toBe(false)
  })
})
