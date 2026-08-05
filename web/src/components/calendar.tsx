// Layout pieces for the calendar. Page-adjacent, deliberately NOT in ledger.tsx —
// that file is the app-wide kit, and nothing outside the calendar wants a day cell.

import type { ReactNode } from 'react'
import type { DayEntry } from '../lib/calendar'
import { cleanName, isOverdue, planningOf } from '../lib/calendar'
import { dayNumeral, monthKey, monthLabel, weeksOfMonth } from '../lib/dates'
import { FillRule, LedgerSection } from './ledger'

const WEEKDAYS = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN']

function EventLine({ entry, today }: { entry: Extract<DayEntry, { kind: 'event' }>; today: string }) {
  const { ev } = entry
  const cancelled = ev.status === 'cancelled'
  const provisional = planningOf(ev.notes) !== null && planningOf(ev.notes) !== 'confirmed'
  return (
    <span className={`block ${cancelled ? 'opacity-50' : ''}`}>
      <span
        className={`block truncate text-[13px] leading-tight ${
          cancelled ? 'text-ink-100 line-through' : provisional ? 'italic text-ink-500' : 'text-ink-100'
        }`}
        title={ev.display_name}
      >
        {cleanName(ev.display_name, ev.date)}
      </span>
      <span className="flex items-baseline justify-between gap-1 font-mono text-[10px] uppercase tracking-wider text-ink-500">
        <span className="truncate">
          {ev.event_type_slug}
          {ev.host_house_slug && ` · ${ev.host_house_slug}`}
          {ev.resync_pending && !cancelled && <span className="ml-1 text-brass-400">· resync</span>}
        </span>
        <span className="shrink-0 tabular-nums">
          {cancelled ? '—' : `${ev.assigned_slots}/${ev.target_slots}`}
        </span>
      </span>
      {ev.target_slots > 0 ? (
        <FillRule filled={ev.assigned_slots} total={ev.target_slots} muted={cancelled} />
      ) : (
        <span className="block font-mono text-[10px] italic text-ink-500">no slots configured</span>
      )}
      {/* aria-hidden: the cell's own label already carries all of this. */}
      <span className="sr-only">{isOverdue(ev, today) ? 'overdue' : ''}</span>
    </span>
  )
}

function GhostLine({ entry }: { entry: Extract<DayEntry, { kind: 'ghost' }> }) {
  // The PARENT NAME, not the crew type. In a ~120px cell there is room for one of
  // them, and "↳ cleanup · …" on two consecutive Sundays is indistinguishable
  // noise, while "↳ AXID Mixer" says exactly which night this crew belongs to.
  // The ↳ already carries "carried in from another day", and the dock's CARRIED
  // IN block names the crew type in full.
  const name = cleanName(entry.parent.display_name, entry.parent.date)
  return (
    <span
      className="flex items-baseline justify-between gap-1 font-mono text-[10px] leading-tight text-ink-500"
      title={`${entry.shiftTypeSlug} ×${entry.target} for ${name}`}
    >
      <span className="truncate">↳ {name}</span>
      <span className="shrink-0 tabular-nums">
        {entry.assigned}/{entry.target}
      </span>
    </span>
  )
}

export function DayCell({
  iso,
  entries,
  inTerm,
  today,
  selected,
  focusable,
  onSelect,
}: {
  iso: string
  entries: DayEntry[]
  inTerm: boolean
  today: string
  selected: boolean
  /** Roving tabindex: exactly one cell per grid is tabbable, so Tab enters the
      grid once rather than walking all 42 squares. */
  focusable: boolean
  onSelect: (iso: string) => void
}) {
  const events = entries.filter((e): e is Extract<DayEntry, { kind: 'event' }> => e.kind === 'event')
  const ghosts = entries.filter((e): e is Extract<DayEntry, { kind: 'ghost' }> => e.kind === 'ghost')
  const overdue = events.some((e) => isOverdue(e.ev, today))
  const isToday = iso === today
  // Inert only when it is BOTH out of term and empty: FA26's last cleanup crew
  // works the day after the term ends, and greying that out would hide four real
  // slots from the only view that shows them.
  const inert = !inTerm && entries.length === 0

  const posted = events.reduce((n, e) => n + e.ev.assigned_slots, 0)
  const target = events.reduce((n, e) => n + e.ev.target_slots, 0)
  const ghostSlots = ghosts.reduce((n, g) => n + g.target, 0)
  const ghostPosted = ghosts.reduce((n, g) => n + g.assigned, 0)
  // Ghosts belong in the LABEL even though they belong in no total. A day whose
  // only entry is a carried-in crew announcing "nothing scheduled" is a lie to
  // anyone not looking at the pixels — and 28 days of FA26 are exactly that day.
  const said: string[] = []
  if (events.length > 0) {
    said.push(
      `${events.length} entr${events.length === 1 ? 'y' : 'ies'}, ${posted} of ${target} slots posted`,
    )
  }
  if (ghosts.length > 0) {
    said.push(`${ghostPosted} of ${ghostSlots} carried-in crew slots posted`)
  }
  if (said.length === 0) said.push('nothing scheduled')
  const label = `${dayNumeral(iso)}, ${said.join(', ')}`

  const shown = events.slice(0, 2)
  const overflow = events.length - shown.length + Math.max(ghosts.length - 2, 0)

  return (
    <button
      type="button"
      role="gridcell"
      aria-selected={selected}
      aria-disabled={inert || undefined}
      disabled={inert}
      tabIndex={focusable ? 0 : -1}
      data-iso={iso}
      aria-label={label}
      onClick={() => onSelect(iso)}
      className={`group relative flex min-h-24 w-full flex-col items-stretch gap-1 border-b border-r border-ink-700/30 px-2 py-1.5 text-left transition-colors ${
        inert ? 'cursor-default' : 'hover:bg-char-850/40'
      } ${selected ? 'bg-brass-300/40' : ''}`}
    >
      {/* The one binary alarm on the page: past-dated and still open. Same 3px
          oxblood edge, same meaning, as the Register's overdue rows. */}
      {overdue && (
        <span className="absolute left-0 top-0 h-full w-[3px] bg-oxblood-600" aria-hidden />
      )}
      <span
        className={`font-mono text-[11px] leading-none tabular-nums ${
          isToday
            ? 'w-fit border-b border-ink-100 pb-0.5 font-medium text-ink-100'
            : selected
              ? 'text-ink-100'
              : inTerm
                ? 'text-ink-500'
                : 'text-ink-700'
        }`}
      >
        {dayNumeral(iso)}
      </span>
      {shown.map((e) => (
        <EventLine key={e.ev.id} entry={e} today={today} />
      ))}
      {ghosts.slice(0, 2).map((g, i) => (
        <GhostLine key={`${g.parent.id}-${g.shiftTypeSlug}-${i}`} entry={g} />
      ))}
      {overflow > 0 && (
        <span className="font-mono text-[10px] text-ink-500">+{overflow} more</span>
      )}
    </button>
  )
}

export function MonthPanel({
  mk,
  days,
  bounds,
  today,
  selected,
  focusDate,
  onSelect,
  hint,
  children,
}: {
  mk: string
  days: Map<string, DayEntry[]>
  bounds: { from: string; to: string }
  today: string
  selected: string | null
  focusDate: string | null
  onSelect: (iso: string) => void
  hint: string
  children?: ReactNode
}) {
  return (
    <>
      <LedgerSection title={monthLabel(mk)} hint={hint}>
        <div className="overflow-x-auto">
          <div
            role="grid"
            aria-label={monthLabel(mk)}
            className="grid min-w-[700px] grid-cols-7 border-l border-t border-ink-700/30"
          >
            {WEEKDAYS.map((d) => (
              <div
                key={d}
                className="border-b border-r border-ink-700/30 px-2 py-1.5 font-mono text-[10px] uppercase tracking-[0.2em] text-ink-500"
              >
                {d}
              </div>
            ))}
            {weeksOfMonth(mk).map((week) => (
              <div key={week[0]} role="row" className="contents">
                {week.map((iso) => (
                  <DayCell
                    key={iso}
                    iso={iso}
                    entries={monthKey(iso) === mk ? (days.get(iso) ?? []) : []}
                    inTerm={monthKey(iso) === mk && iso >= bounds.from && iso <= bounds.to}
                    today={today}
                    selected={monthKey(iso) === mk && iso === selected}
                    focusable={monthKey(iso) === mk && iso === focusDate}
                    onSelect={onSelect}
                  />
                ))}
              </div>
            ))}
          </div>
        </div>
      </LedgerSection>
      {children}
    </>
  )
}
