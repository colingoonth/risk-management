// The Term Grid — every month of the term as a Monday-first hairline grid, with
// the detail dock spliced in directly below the month you clicked in.
//
// Two questions this answers that neither the Events list nor the Register can:
// "how bad is this weekend" and "are these two parties back to back". Density and
// adjacency are spatial facts, and a list destroys them.
//
// The reading, without clicking anything: red means a missing person, and that is
// the only thing red means anywhere in this app. A fully-staffed month renders in
// nothing but espresso on baby-blue and is visually silent, so the chair is not
// reading the calendar — she is scanning it for warmth.

import { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { useSemester } from '../lib/SemesterContext'
import type { EventSummaryRow } from '../lib/types'
import { buildDays, cleanName, isOverdue, planningOf, rollup } from '../lib/calendar'
import type { DayEntry } from '../lib/calendar'
import {
  addDays,
  isValidIso,
  longDate,
  monthKey,
  monthLabel,
  monthsBetween,
  todayLocal,
} from '../lib/dates'
import { ErrorNote } from '../components/ui'
import { FillBar, FillRule, LedgerSection, PenButton, StatusGlyph } from '../components/ledger'
import { MonthPanel } from '../components/calendar'
import { EventDetail } from '../components/EventDetail'

const TODAY = todayLocal()

export function Calendar() {
  const { current, loading: semLoading } = useSemester()
  const { data: events, loading, error, reload } = useAsync(() => api.listEventSummaries(), [])
  const windows = useAsync(() => api.listShiftTypeWindows(), [])
  const houses = useAsync(() => api.listHouses(), [])
  const [params, setParams] = useSearchParams()
  const [note, setNote] = useState<string | null>(null)
  const dockRef = useRef<HTMLDivElement | null>(null)

  const selected = params.get('d')
  const rows = useMemo(() => events ?? [], [events])

  const bounds = useMemo(
    () => ({
      from: current?.starts_on ?? '0000-01-01',
      to: current?.ends_on ?? '9999-12-31',
    }),
    [current],
  )

  const { days, strays } = useMemo(
    () => buildDays(rows, windows.data ?? [], bounds),
    [rows, windows.data, bounds],
  )

  // Which months to draw. The term's own span, widened to cover anything the day
  // map actually placed — a cleanup crew works the day after the last party, and
  // FA26's runs into a month the term does not otherwise reach. Clamped so one
  // stray event dated 2027 cannot stretch the page across nine empty months.
  const months = useMemo(() => {
    if (!current) return []
    const placed = [...days.keys()].filter(
      (k) => k >= current.starts_on && k <= addDays(current.ends_on, 45),
    )
    const last = placed.reduce((a, b) => (b > a ? b : a), current.ends_on)
    return monthsBetween(monthKey(current.starts_on), monthKey(last))
  }, [current, days])

  const term = useMemo(() => rollup(rows), [rows])

  // Roving tabindex: exactly one cell in the page is tabbable. Prefer the
  // selection, then today if it falls inside the term, then the first day of the
  // term — so Tab always lands somewhere meaningful and never walks 200 squares.
  const focusDate = useMemo(() => {
    const fallback =
      current && TODAY >= current.starts_on && TODAY <= current.ends_on
        ? TODAY
        : (current?.starts_on ?? null)
    if (!selected) return fallback
    // An inert cell renders `disabled`, so parking the page's only tabIndex=0 on
    // one makes every grid unreachable by Tab. A selection can be inert two ways:
    // its month is not drawn at all, or it is drawn but out of term and empty.
    // Mirror DayCell's own `inert` rule rather than inventing a second one.
    const rendered = months.includes(monthKey(selected))
    const inert =
      !(selected >= bounds.from && selected <= bounds.to) && !days.has(selected)
    return rendered && !inert ? selected : fallback
  }, [selected, current, months, bounds, days])

  function select(iso: string) {
    // Clicking the open date closes it. `replace` always: the dock is not a modal
    // and the desktop shell is a chromeless window with no Back button, so twelve
    // inspections must not cost twelve Back presses to leave the page.
    const next = new URLSearchParams(params)
    if (selected === iso) next.delete('d')
    else next.set('d', iso)
    setParams(next, { replace: true })
  }

  // Arrow-key traversal, once a date is open. Mirrors a native date grid.
  useEffect(() => {
    if (!selected) return
    function onKey(e: KeyboardEvent) {
      // Never steal keys from a form control. The dock ships a host-house
      // <select>, and Down/Escape belong to it while it has focus — without this
      // the select is unusable by keyboard and Down silently jumps the calendar
      // a week instead of changing the value.
      const t = e.target as HTMLElement | null
      if (t && (t.isContentEditable || /^(INPUT|SELECT|TEXTAREA)$/.test(t.tagName))) return
      // Leave the browser's own Cmd/Alt+Arrow (history, word nav) alone.
      if (e.metaKey || e.ctrlKey || e.altKey) return

      const step =
        e.key === 'ArrowLeft' ? -1
        : e.key === 'ArrowRight' ? 1
        : e.key === 'ArrowUp' ? -7
        : e.key === 'ArrowDown' ? 7
        : 0
      if (step !== 0) {
        e.preventDefault()
        const target = addDays(selected as string, step)
        // Refuse to walk off the drawn months. Otherwise the URL names a date
        // with no cell: no highlight, no dock, no tabbable cell, and no way back
        // except editing the address bar.
        if (!months.includes(monthKey(target))) return
        const next = new URLSearchParams(params)
        next.set('d', target)
        setParams(next, { replace: true })
        // Move DOM focus with the selection, but only when the keypress came
        // FROM a cell — i.e. the user is actually navigating the grid. Stealing
        // focus after a mouse click would yank it away from whatever they were
        // doing.
        if (t?.getAttribute('role') === 'gridcell') {
          requestAnimationFrame(() => {
            document.querySelector<HTMLElement>(`[data-iso="${target}"]`)?.focus()
          })
        }
      } else if (e.key === 'Escape') {
        const next = new URLSearchParams(params)
        next.delete('d')
        setParams(next, { replace: true })
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selected, params, setParams, months])

  // `nearest` only: nudge the dock into view if it is off-screen, do nothing at
  // all if it is already visible. Never yank the page under the cursor.
  useEffect(() => {
    if (selected) dockRef.current?.scrollIntoView({ block: 'nearest' })
  }, [selected])

  async function assign(eventId: number) {
    setNote(null)
    try {
      const r = await api.autoAssign(eventId)
      const filled = r.assignments.filter((a) => a.reason === 'assigned').length
      setNote(`${r.event_name}: ${filled} posted (mode ${r.resolved_mode}).`)
      reload()
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e))
    }
  }

  if (semLoading) {
    return <p className="px-4 py-8 text-sm italic text-ink-500">Loading the term…</p>
  }
  if (!current) {
    return (
      <div className="space-y-10">
        <h1 className="text-3xl font-semibold tracking-tight text-ink-100">Calendar</h1>
        <p className="px-4 py-8 text-sm italic text-ink-500">
          The term is not set. Open one on the Events page.
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-10">
      <h1 className="text-3xl font-semibold tracking-tight text-ink-100">Calendar</h1>
      {note && <ErrorNote message={note} />}
      {error && <ErrorNote message={error} />}

      <LedgerSection
        band
        title="The Term"
        hint={`${term.eventCount} events · ${term.assigned}/${term.target} posted`}
        action={
          <span className="block w-[200px]">
            <FillRule filled={term.assigned} total={term.target} />
          </span>
        }
      >
        <div className="space-y-1 px-4 py-2.5">
          <p className="font-mono text-[10px] text-ink-500">
            each fraction counts the whole event · ↳ repeats a crew on the morning it works,
            never counted twice
            {term.unstaffed > 0 && (
              <>
                {' · '}
                <span className="text-oxblood-300">{term.unstaffed} unstaffed</span>
              </>
            )}
          </p>
          <div className="flex flex-wrap gap-3">
            {months.map((mk) => (
              <a
                key={mk}
                href={`#month-${mk}`}
                className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-500 transition-colors hover:text-ink-300"
              >
                {monthLabel(mk).slice(0, 3)}
              </a>
            ))}
          </div>
        </div>
      </LedgerSection>

      {loading && !events ? (
        <p className="px-4 py-8 text-sm italic text-ink-500">Loading the schedule…</p>
      ) : (
        <>
          {rows.length === 0 && (
            <p className="px-4 text-sm italic text-ink-500">
              No events entered. Post one on the Events page to open the term.
            </p>
          )}
          {months.map((mk) => {
            const m = rollup(rows, mk)
            return (
              <div key={mk} id={`month-${mk}`}>
                <MonthPanel
                  mk={mk}
                  days={days}
                  bounds={bounds}
                  today={TODAY}
                  selected={selected}
                  focusDate={focusDate}
                  onSelect={select}
                  hint={`${m.eventCount} entries · ${m.assigned}/${m.target} posted`}
                >
                  {selected && monthKey(selected) === mk && (
                    <DayDock
                      ref={dockRef}
                      iso={selected}
                      entries={days.get(selected) ?? []}
                      houses={houses.data ?? []}
                      windows={windows.data ?? []}
                      onClose={() => select(selected)}
                      onChanged={reload}
                      onAssign={assign}
                      onGoTo={(iso) => {
                        const next = new URLSearchParams(params)
                        next.set('d', iso)
                        setParams(next, { replace: true })
                      }}
                    />
                  )}
                </MonthPanel>
              </div>
            )
          })}

          {strays.length > 0 && (
            <LedgerSection
              title="Outside the term"
              hint="dated outside this semester, or not a valid date"
            >
              <ul className="px-4 py-3">
                {strays.map((ev) => (
                  <li key={ev.id} className="flex justify-between gap-4 py-1 text-sm">
                    <span className="truncate text-ink-100">{ev.display_name}</span>
                    <span className="shrink-0 font-mono text-[11px] text-ink-500">
                      {isValidIso(ev.date) ? ev.date : `${ev.date} (unreadable)`}
                    </span>
                  </li>
                ))}
              </ul>
            </LedgerSection>
          )}
        </>
      )}
    </div>
  )
}

function DayDock({
  ref,
  iso,
  entries,
  houses,
  windows,
  onClose,
  onChanged,
  onAssign,
  onGoTo,
}: {
  ref: React.Ref<HTMLDivElement>
  iso: string
  entries: DayEntry[]
  houses: { slug: string; display_name: string }[]
  windows: import('../lib/types').ShiftTypeWindow[]
  onClose: () => void
  onChanged: () => void
  onAssign: (eventId: number) => void
  onGoTo: (iso: string) => void
}) {
  const events = entries.filter((e): e is Extract<DayEntry, { kind: 'event' }> => e.kind === 'event')
  const ghosts = entries.filter((e): e is Extract<DayEntry, { kind: 'ghost' }> => e.kind === 'ghost')
  const posted = events.reduce((n, e) => n + e.ev.assigned_slots, 0)
  const target = events.reduce((n, e) => n + e.ev.target_slots, 0)

  return (
    <div ref={ref}>
      <LedgerSection
        title={longDate(iso)}
        hint={
          // A day with only a carried-in crew is NOT "nothing on the books" —
          // 28 days of FA26 are exactly that, carrying 100 unfilled crew slots
          // between them. The crews are not counted in the party fractions
          // (they belong to their own night) but they must be announced.
          events.length === 0 && ghosts.length === 0
            ? 'nothing on the books'
            : [
                events.length > 0
                  ? `${events.length} ${events.length === 1 ? 'party' : 'parties'} · ${posted}/${target} posted`
                  : null,
                ghosts.length > 0
                  ? `${ghosts.length} crew${ghosts.length === 1 ? '' : 's'} carried in`
                  : null,
              ]
                .filter(Boolean)
                .join(' · ')
        }
        action={
          <button
            onClick={onClose}
            className="font-mono text-[11px] uppercase tracking-[0.15em] text-ink-500 transition-colors hover:text-oxblood-300"
          >
            close ✕
          </button>
        }
      >
        {events.length === 0 && ghosts.length === 0 ? (
          <p className="px-4 py-6 text-sm italic text-ink-500">Nothing on the books.</p>
        ) : (
          <>
            {events.map((e) => (
              <DockEvent
                key={e.ev.id}
                ev={e.ev}
                houses={houses}
                windows={windows}
                onChanged={onChanged}
                onAssign={onAssign}
              />
            ))}
            {ghosts.length > 0 && (
              <div className="border-t border-ink-700/30 px-4 py-3">
                <div className="mb-2 font-mono text-[10px] uppercase tracking-[0.2em] text-ink-500">
                  carried in · crews working this morning for another night
                </div>
                <ul className="space-y-1">
                  {ghosts.map((g, i) => (
                    <li
                      key={`${g.parent.id}-${g.shiftTypeSlug}-${i}`}
                      className="flex flex-wrap items-baseline justify-between gap-2 font-mono text-xs text-ink-300"
                    >
                      <span>
                        {g.shiftTypeSlug} ×{g.target} —{' '}
                        {cleanName(g.parent.display_name, g.parent.date)}
                      </span>
                      <span className="flex items-center gap-3">
                        <span className="tabular-nums text-ink-500">
                          {g.assigned}/{g.target}
                        </span>
                        <button
                          onClick={() => onGoTo(g.parent.date)}
                          className="uppercase tracking-[0.15em] text-ink-500 transition-colors hover:text-brass-400"
                        >
                          → go to {g.parent.date.slice(5)}
                        </button>
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}
      </LedgerSection>
    </div>
  )
}

function DockEvent({
  ev,
  houses,
  windows,
  onChanged,
  onAssign,
}: {
  ev: EventSummaryRow
  houses: { slug: string; display_name: string }[]
  windows: import('../lib/types').ShiftTypeWindow[]
  onChanged: () => void
  onAssign: (eventId: number) => void
}) {
  const cancelled = ev.status === 'cancelled'
  const planning = planningOf(ev.notes)
  return (
    <div className="border-b border-ink-700/30 last:border-0">
      <div className="flex flex-wrap items-center gap-3 px-4 py-3">
        <div className="min-w-0 flex-1">
          <div className={`text-ink-100 ${cancelled ? 'line-through opacity-50' : ''}`}>
            {ev.display_name}
          </div>
          <div className="font-mono text-[11px] uppercase tracking-wider text-ink-500">
            {ev.event_type_slug}
            {ev.host_house_slug ? ` · ${ev.host_house_slug}` : ' · off-site'}
            {planning && planning !== 'confirmed' && ` · ${planning}`}
            {ev.resync_pending && !cancelled && (
              <span className="ml-2 text-brass-400">· needs re-assign</span>
            )}
          </div>
        </div>
        {!cancelled && <FillBar filled={ev.assigned_slots} total={ev.target_slots} />}
        {isOverdue(ev, TODAY) && <StatusGlyph state="overdue" />}
        {ev.orphan_slots > 0 && (
          <span className="font-mono text-[11px] text-oxblood-300">
            ⚠ {ev.orphan_slots} assigned beyond target
          </span>
        )}
        <PenButton disabled={cancelled} onClick={() => onAssign(ev.id)}>
          auto-assign
        </PenButton>
      </div>
      <EventDetail
        eventId={ev.id}
        currentHost={ev.host_house_slug}
        cancelled={cancelled}
        houses={houses}
        onChanged={onChanged}
        groups={ev.by_type}
        windows={windows}
        eventDate={ev.date}
      />
    </div>
  )
}