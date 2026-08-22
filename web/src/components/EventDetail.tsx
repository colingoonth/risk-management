// The expanded detail for one event: its roster, the auto-assign preview, and
// the host/cancel controls.
//
// Lifted verbatim out of pages/Events.tsx (where it was `EventDrilldown`) so the
// calendar's day dock can open the same drawer rather than growing a second,
// drifting one. Events passes no new props, so its behaviour is unchanged.
//
// The two optional props fix a real gap when supplied. `shifts` rows are created
// LAZILY by auto-assign, so an event nobody has assigned has none — and the
// original code read `data.length === 0` as "no shift slots snapshotted for this
// event type", which is false for every un-assigned event and, right now, that is
// all of them. Given `groups`, the roster is rendered from TARGETS and the shifts
// merely fill it in, so an untouched krush reads 0/16 with sixteen visible holes
// instead of an empty block.

import { useState } from 'react'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { addDays, shortDate } from '../lib/dates'
import type {
  AutoAssignResult,
  House,
  Member,
  Shift,
  ShiftSlotGroup,
  ShiftTypeWindow,
} from '../lib/types'
import { ErrorNote } from './ui'
import { PenButton, StatusGlyph } from './ledger'

// Per-slot auto-assign reason → chair-readable label. pool_below_min is never
// emitted as a slot reason (it surfaces in result.warnings), kept for safety.
const REASON: Record<string, { label: string; cls: string }> = {
  assigned: { label: 'auto-picked', cls: 'text-ink-300' },
  preserved: { label: 'kept', cls: 'text-ink-500' },
  pool_empty: { label: 'no eligible pool', cls: 'text-oxblood-300' },
  pool_below_min: { label: 'pool below min', cls: 'text-oxblood-300' },
}

/**
 * A shift type's window in words, driven from the seeded data rather than
 * hardcoded, so a migration that moves cleanup to +2 needs no edit here.
 *
 * A window that SPANS days is a task with a deadline, not an appointment —
 * stamping it on any single one of its three dates would fabricate precision the
 * schema does not hold. So setup is described as a span and cleanup as a morning.
 */
function windowPhrase(w: ShiftTypeWindow, eventDate?: string): string {
  const span = `${w.window_start_time}–${w.window_end_time}`
  if (w.offset_days_start === w.offset_days_end) {
    if (w.offset_days_start === 0) return `event night, ${span}`
    if (!eventDate) {
      const rel = w.offset_days_start > 0 ? `+${w.offset_days_start}d` : `${w.offset_days_start}d`
      return `${rel}, ${span}`
    }
    return `${shortDate(addDays(eventDate, w.offset_days_start))}, ${span}`
  }
  const block = w.min_contiguous_minutes ? `any ${w.min_contiguous_minutes / 60}h block` : 'any time'
  if (!eventDate) return `${block}, ${w.offset_days_start}d…${w.offset_days_end}d`
  return `${block}, ${shortDate(addDays(eventDate, w.offset_days_start))} – ${shortDate(
    addDays(eventDate, w.offset_days_end),
  )}`
}

/** Assign, replace or clear one slot.
 *
 * Collapsed to a single affordance until clicked, because a 13-slot event with
 * a dropdown on every row is unreadable — and reading the roster is what this
 * drawer is mostly for. The picker is unfiltered on purpose: the server does
 * the eligibility check and gives a reason, which is a better answer than a
 * name silently missing from a list.
 */
function SlotControl({
  shift,
  members,
  busy,
  onAssign,
  onClear,
}: {
  shift: Shift
  members: Member[]
  busy: boolean
  onAssign: (slug: string, force: boolean) => void
  onClear: () => void
}) {
  const [open, setOpen] = useState(false)
  const [pick, setPick] = useState('')

  if (!open) {
    return (
      <button
        disabled={busy}
        onClick={() => setOpen(true)}
        className="font-mono text-[10px] uppercase tracking-[0.15em] text-ink-600 hover:text-brass-400 disabled:opacity-40"
      >
        {shift.assigned_member_slug ? 'change' : 'assign'}
      </button>
    )
  }
  return (
    <div className="flex items-center justify-end gap-1">
      <select
        autoFocus
        value={pick}
        onChange={(e) => setPick(e.target.value)}
        className="max-w-[9rem] border-b border-ink-700/50 bg-char-950 py-0.5 text-xs text-ink-100 focus:border-brass-500 focus:outline-none"
      >
        <option value="">pick…</option>
        {members.map((m) => (
          <option key={m.slug} value={m.slug}>
            {m.display_name}
          </option>
        ))}
      </select>
      <button
        disabled={busy || !pick}
        onClick={() => {
          onAssign(pick, false)
          setOpen(false)
          setPick('')
        }}
        className="font-mono text-[10px] uppercase tracking-[0.15em] text-brass-400 disabled:opacity-40"
      >
        ok
      </button>
      {shift.assigned_member_slug && (
        <button
          disabled={busy}
          onClick={() => {
            onClear()
            setOpen(false)
          }}
          className="font-mono text-[10px] uppercase tracking-[0.15em] text-oxblood-300 disabled:opacity-40"
        >
          clear
        </button>
      )}
      <button
        onClick={() => setOpen(false)}
        className="font-mono text-[10px] uppercase tracking-[0.15em] text-ink-600"
      >
        ✕
      </button>
    </div>
  )
}

export function EventDetail({
  eventId,
  currentHost,
  cancelled,
  houses,
  onChanged,
  groups,
  windows,
  eventDate,
}: {
  eventId: number
  currentHost: string | null
  cancelled: boolean
  houses: House[]
  onChanged: () => void
  /** From EventSummaryRow.by_type. Supplied, the roster renders from targets. */
  groups?: ShiftSlotGroup[]
  /** Supplied alongside `groups`, each shift type gets its window in words. */
  windows?: ShiftTypeWindow[]
  eventDate?: string
}) {
  const { data, loading } = useAsync<Shift[]>(() => api.eventShifts(eventId), [eventId])
  // Fetched here rather than threaded down as a prop: the drawer already owns
  // its own reads, and the roster is the same for every slot in it.
  const { data: members } = useAsync<Member[]>(() => api.listMembers(), [])
  const [host, setHost] = useState(currentHost ?? '')
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [preview, setPreview] = useState<AutoAssignResult | null>(null)

  async function run(key: string, fn: () => Promise<unknown>) {
    setBusy(key)
    setErr(null)
    try {
      await fn()
      onChanged()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  // A non-committing dry run: a preview of what auto-assign WOULD do now, with
  // the per-slot reason. Pinned seed so repeated clicks are stable.
  async function explain() {
    setBusy('explain')
    setErr(null)
    try {
      setPreview(await api.autoAssign(eventId, { seed: 42, reassign: false }, true))
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  const byWindow = new Map((windows ?? []).map((w) => [w.shift_type_slug, w]))
  const assignedAt = new Map(
    (data ?? []).map((s) => [`${s.shift_type_slug}#${s.slot_index}`, s]),
  )

  return (
    <div className="border-t border-ink-700/20 bg-char-900/50 px-4 py-3">
      {loading ? (
        <p className="text-sm italic text-ink-500">Loading shifts…</p>
      ) : groups && groups.length > 0 ? (
        // Roster driven by TARGETS — every required slot shows, filled or not.
        <div className="space-y-2">
          {groups.map((g) => {
            const w = byWindow.get(g.shift_type_slug)
            return (
              <div key={g.shift_type_slug}>
                <div className="flex items-baseline justify-between gap-2 font-mono text-[10px] uppercase tracking-wider text-ink-500">
                  <span>
                    {g.shift_type_slug}
                    {w && <span className="ml-2 normal-case tracking-normal">{windowPhrase(w, eventDate)}</span>}
                  </span>
                  <span className="shrink-0 tabular-nums">
                    {g.assigned_count}/{g.target_count}
                  </span>
                </div>
                <table className="w-full font-mono text-sm">
                  <tbody>
                    {Array.from({ length: g.target_count }, (_, i) => {
                      const s = assignedAt.get(`${g.shift_type_slug}#${i}`)
                      return (
                        <tr
                          key={i}
                          className="border-b border-ink-700/15 last:border-0"
                        >
                          <td className="py-1 text-ink-500">#{i + 1}</td>
                          <td className="py-1 text-right">
                            {s?.assigned_member_slug ? (
                              <span className="text-ink-100">
                                {s.assigned_member_display_name ?? s.assigned_member_slug}
                                {/* A pen mark: this one was placed by hand and
                                    a rebuild will not touch it. Without it the
                                    chair cannot tell which of his decisions the
                                    schedule is still carrying. */}
                                {s.chair_set && (
                                  <span
                                    title="Set by you — a rebuild keeps it"
                                    className="ml-1.5 text-brass-400"
                                  >
                                    ✎
                                  </span>
                                )}
                              </span>
                            ) : (
                              <StatusGlyph state="unfilled" />
                            )}
                          </td>
                          <td className="w-24 py-1 pl-2 text-right">
                            {s && (
                              <SlotControl
                                shift={s}
                                members={members ?? []}
                                busy={busy === `slot-${s.id}`}
                                onAssign={(slug, force) =>
                                  run(`slot-${s.id}`, async () => {
                                    const r = await api.assignShift(s.id, slug, force)
                                    if (r.warnings.length) setErr(r.warnings.join('; '))
                                  })
                                }
                                onClear={() =>
                                  run(`slot-${s.id}`, () => api.unassignShift(s.id))
                                }
                              />
                            )}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )
          })}
        </div>
      ) : !data || data.length === 0 ? (
        <p className="text-sm italic text-ink-500">No shift slots snapshotted for this event type.</p>
      ) : (
        <table className="w-full font-mono text-sm">
          <tbody>
            {data.map((s) => (
              <tr key={s.id} className="border-b border-ink-700/15 last:border-0">
                <td className="py-1.5 text-ink-500">
                  {s.shift_type_slug} #{s.slot_index + 1}
                </td>
                <td className="py-1.5 text-right">
                  {s.assigned_member_slug ? (
                    <span className="text-ink-100">{s.assigned_member_slug}</span>
                  ) : (
                    <StatusGlyph state="unfilled" />
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {err && <ErrorNote message={err} />}

      {preview && (
        <div className="mt-3 border-t border-ink-700/20 pt-3">
          <div className="mb-2 font-mono text-[10px] uppercase tracking-[0.2em] text-ink-500">
            auto-assign preview · why each slot lands (dry run)
          </div>
          <table className="w-full font-mono text-sm">
            <tbody>
              {preview.assignments.map((a) => {
                const r = REASON[a.reason] ?? { label: a.reason, cls: 'text-ink-500' }
                return (
                  <tr
                    key={`${a.shift_type_slug}-${a.slot_index}`}
                    className="border-b border-ink-700/15 last:border-0"
                  >
                    <td className="py-1.5 text-ink-500">
                      {a.shift_type_slug} #{a.slot_index + 1}
                    </td>
                    <td className="py-1.5 text-ink-100">{a.member_slug ?? '—'}</td>
                    <td className={`py-1.5 text-right text-[12px] ${r.cls}`}>{r.label}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          {preview.warnings.length > 0 && (
            <ul className="mt-2 space-y-0.5">
              {preview.warnings.map((w, i) => (
                <li key={i} className="font-mono text-[11px] text-oxblood-300">
                  ⚠ {w}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {!cancelled && (
        <div className="mt-3 flex flex-col gap-3 border-t border-ink-700/20 pt-3 sm:flex-row sm:items-end sm:justify-between">
          <div className="flex items-end gap-2">
            <label className="block">
              <span className="mb-1 block font-mono text-[10px] uppercase tracking-[0.18em] text-ink-500">
                Host house
              </span>
              <select
                value={host}
                onChange={(e) => setHost(e.target.value)}
                className="border-b border-ink-700/50 bg-transparent px-1 py-1.5 text-sm text-ink-100 focus:border-brass-500 focus:outline-none"
              >
                <option value="">— none (off-site) —</option>
                {houses.map((h) => (
                  <option key={h.slug} value={h.slug}>
                    {h.display_name}
                  </option>
                ))}
              </select>
            </label>
            <PenButton
              disabled={busy !== null || host === (currentHost ?? '')}
              onClick={() => run('host', () => api.setEventHost(eventId, host || null))}
            >
              {busy === 'host' ? 'setting…' : 'Set host'}
            </PenButton>
            <span className="pb-1.5 font-mono text-[10px] text-ink-500">flips re-assign</span>
          </div>
          <div className="flex items-center gap-4 self-start sm:self-auto">
            <button
              disabled={busy !== null}
              onClick={explain}
              className="font-mono text-[11px] uppercase tracking-[0.15em] text-ink-500 transition-colors hover:text-brass-400 disabled:text-ink-700"
            >
              {busy === 'explain' ? 'explaining…' : 'explain (dry run)'}
            </button>
            <button
              disabled={busy !== null}
              onClick={() => run('cancel', () => api.cancelEvent(eventId))}
              className="font-mono text-[11px] uppercase tracking-[0.15em] text-ink-500 transition-colors hover:text-oxblood-300 disabled:text-ink-700"
            >
              {busy === 'cancel' ? 'cancelling…' : '✕ cancel event'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
