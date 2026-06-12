import { useState } from 'react'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { useSemester } from '../lib/SemesterContext'
import type { ArchiveReport, ArchiveResult, House, Shift } from '../lib/types'
import { ErrorNote } from '../components/ui'
import { DayStamp, LedgerField, LedgerSection, PenButton, StatusGlyph } from '../components/ledger'

export function Events() {
  const { data: events, loading, error, reload } = useAsync(() => api.listEvents(), [])
  const houses = useAsync(() => api.listHouses(), [])
  const [busy, setBusy] = useState<number | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [open, setOpen] = useState<number | null>(null)

  async function assign(eventId: number) {
    setBusy(eventId)
    setNote(null)
    try {
      const r = await api.autoAssign(eventId)
      const filled = r.assignments.filter((a) => a.reason === 'assigned').length
      setNote(`${r.event_name}: ${filled} posted (mode ${r.resolved_mode}).`)
      reload()
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="space-y-10">
      <h1 className="text-3xl font-semibold tracking-tight text-ink-100">Events</h1>
      {note && <ErrorNote message={note} />}

      <CreateEvent onCreated={reload} />

      <LedgerSection title="The Schedule">
        {loading ? (
          <p className="px-4 py-8 text-sm italic text-ink-500">Loading the schedule…</p>
        ) : error ? (
          <ErrorNote message={error} />
        ) : !events || events.length === 0 ? (
          <p className="px-4 py-8 text-center text-sm italic text-ink-500">
            No events entered. Post one above to open the term.
          </p>
        ) : (
          <ul>
            {events.map((e) => {
              const cancelled = e.status === 'cancelled'
              return (
                <li key={e.id} className="border-b border-ink-700/30 last:border-0">
                  <div
                    className={`flex items-center gap-4 py-3 pl-4 pr-4 transition-colors hover:bg-char-850/40 ${
                      cancelled ? 'opacity-50' : ''
                    }`}
                  >
                    <DayStamp iso={e.date} />
                    <button
                      className="min-w-0 flex-1 text-left"
                      onClick={() => setOpen(open === e.id ? null : e.id)}
                    >
                      <div className={`truncate text-ink-100 ${cancelled ? 'line-through' : ''}`}>
                        {e.display_name}
                      </div>
                      <div className="font-mono text-[11px] uppercase tracking-wider text-ink-500">
                        {e.event_type_slug}
                        {e.host_house_slug && ` · ${e.host_house_slug}`}
                        {e.resync_pending && !cancelled && (
                          <span className="ml-2 text-brass-400">· needs re-assign</span>
                        )}
                      </div>
                    </button>
                    <span className="w-24 font-mono text-[11px] uppercase tracking-wider text-ink-500">
                      {e.status}
                    </span>
                    <PenButton disabled={busy !== null || cancelled} onClick={() => assign(e.id)}>
                      {busy === e.id ? 'posting…' : 'auto-assign'}
                    </PenButton>
                  </div>
                  {open === e.id && (
                    <EventDrilldown
                      eventId={e.id}
                      currentHost={e.host_house_slug}
                      cancelled={cancelled}
                      houses={houses.data ?? []}
                      onChanged={reload}
                    />
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </LedgerSection>

      <ArchiveSection eventsReload={reload} />
    </div>
  )
}

function EventDrilldown({
  eventId,
  currentHost,
  cancelled,
  houses,
  onChanged,
}: {
  eventId: number
  currentHost: string | null
  cancelled: boolean
  houses: House[]
  onChanged: () => void
}) {
  const { data, loading } = useAsync<Shift[]>(() => api.eventShifts(eventId), [eventId])
  const [host, setHost] = useState(currentHost ?? '')
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)

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

  return (
    <div className="border-t border-ink-700/20 bg-char-900/50 px-4 py-3">
      {loading ? (
        <p className="text-sm italic text-ink-500">Loading shifts…</p>
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
          <button
            disabled={busy !== null}
            onClick={() => run('cancel', () => api.cancelEvent(eventId))}
            className="self-start font-mono text-[11px] uppercase tracking-[0.15em] text-ink-500 transition-colors hover:text-oxblood-300 disabled:text-ink-700 sm:self-auto"
          >
            {busy === 'cancel' ? 'cancelling…' : '✕ cancel event'}
          </button>
        </div>
      )}
    </div>
  )
}

function ArchiveSection({ eventsReload }: { eventsReload: () => void }) {
  const semCtx = useSemester()
  const semesters = useAsync(() => api.listSemesters(), [])
  const [target, setTarget] = useState('')
  const [report, setReport] = useState<ArchiveReport | null>(null)
  const [confirmName, setConfirmName] = useState('')
  const [carryTo, setCarryTo] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [result, setResult] = useState<ArchiveResult | null>(null)

  const all = semesters.data ?? []
  const archivable = all.filter((s) => !s.is_current && !s.archived_at)
  const carryTargets = all.filter((s) => !s.archived_at && s.name !== target)

  async function pickTarget(name: string) {
    setTarget(name)
    setReport(null)
    setResult(null)
    setConfirmName('')
    setErr(null)
    // Default carry-to to the current term when one exists.
    setCarryTo(all.find((s) => s.is_current && s.name !== name)?.name ?? '')
    if (!name) return
    try {
      setReport(await api.archiveCheck(name))
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }

  const eventBlocked = (report?.future_event_count ?? 0) > 0
  const needsForce = !!report?.is_blocked && !eventBlocked
  const confirmed = !needsForce || confirmName.trim() === target

  async function doArchive() {
    setBusy(true)
    setErr(null)
    try {
      const res = await api.archiveSemester(target, {
        force: needsForce,
        carry_to: carryTo || null,
      })
      setResult(res)
      setReport(null)
      setTarget('')
      setConfirmName('')
      semesters.reload()
      semCtx.reload()
      eventsReload()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <LedgerSection title="Close out a term" hint="archive a finished, non-current semester">
      <div className="space-y-4 px-4 py-4">
        {err && <ErrorNote message={err} />}
        {result && (
          <p className="font-mono text-xs text-ink-300">
            Archived. {result.strikes_closed} strike(s) closed,{' '}
            {result.strikes_carried_forward} carried,{' '}
            {result.consequences_carried_forward} consequence(s) carried,{' '}
            {result.swaps_cancelled} swap(s) cancelled.
          </p>
        )}

        {archivable.length === 0 ? (
          <p className="text-sm italic text-ink-500">
            No archivable terms. The current term can't be archived — set a different current
            semester first.
          </p>
        ) : (
          <label className="block sm:w-72">
            <span className="mb-1 block font-mono text-[10px] uppercase tracking-[0.18em] text-ink-500">
              Term to archive
            </span>
            <select
              value={target}
              onChange={(e) => pickTarget(e.target.value)}
              className="w-full border-b border-ink-700/50 bg-transparent px-1 py-1.5 text-sm text-ink-100 focus:border-brass-500 focus:outline-none"
            >
              <option value="">— select term —</option>
              {archivable.map((s) => (
                <option key={s.name} value={s.name}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>
        )}

        {report && (
          <div className="space-y-3 border-t border-ink-700/20 pt-3">
            <div className="grid grid-cols-2 gap-2 font-mono text-xs text-ink-300 sm:grid-cols-4">
              <Counter n={report.future_event_count} label="non-terminal events" alarm />
              <Counter n={report.open_strike_count} label="open strikes" />
              <Counter n={report.pending_consequence_count} label="pending sanctions" />
              <Counter n={report.open_swap_count} label="open swaps" />
            </div>

            {eventBlocked ? (
              <p className="text-sm text-oxblood-300">
                Blocked: cancel or complete the {report.future_event_count} non-terminal event(s)
                above first — force cannot bypass this.
              </p>
            ) : needsForce ? (
              <div className="space-y-3">
                <p className="text-sm text-oxblood-300">
                  This permanently closes the strikes/sanctions/swaps above. Unarchiving does NOT
                  restore them. Carry strikes forward to keep them active.
                </p>
                <label className="block sm:w-72">
                  <span className="mb-1 block font-mono text-[10px] uppercase tracking-[0.18em] text-ink-500">
                    Carry strikes to
                  </span>
                  <select
                    value={carryTo}
                    onChange={(e) => setCarryTo(e.target.value)}
                    className="w-full border-b border-ink-700/50 bg-transparent px-1 py-1.5 text-sm text-ink-100 focus:border-brass-500 focus:outline-none"
                  >
                    <option value="">— close them, don't carry —</option>
                    {carryTargets.map((s) => (
                      <option key={s.name} value={s.name}>
                        {s.name}
                      </option>
                    ))}
                  </select>
                </label>
                <LedgerField
                  label={`type "${target}" to confirm`}
                  value={confirmName}
                  onChange={setConfirmName}
                  placeholder={target}
                />
              </div>
            ) : (
              <p className="text-sm text-ink-500">Clean — no blockers. Ready to archive.</p>
            )}

            {!eventBlocked && (
              <PenButton disabled={busy || !confirmed} onClick={doArchive}>
                {busy ? 'archiving…' : `Archive ${target}`}
              </PenButton>
            )}
          </div>
        )}
      </div>
    </LedgerSection>
  )
}

function Counter({ n, label, alarm }: { n: number; label: string; alarm?: boolean }) {
  return (
    <div>
      <span
        className={`text-lg tabular-nums ${n > 0 && alarm ? 'text-oxblood-300' : 'text-ink-100'}`}
      >
        {n}
      </span>{' '}
      <span className="text-ink-500">{label}</span>
    </div>
  )
}

function CreateEvent({ onCreated }: { onCreated: () => void }) {
  const eventTypes = useAsync(() => api.listEventTypes(), [])
  const [type, setType] = useState('')
  const [name, setName] = useState('')
  const [date, setDate] = useState('')
  const [host, setHost] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const picked = (eventTypes.data ?? []).find((t) => t.slug === type)
  const noSlots = picked && !picked.has_shift_defaults

  async function submit() {
    setBusy(true)
    setErr(null)
    try {
      await api.createEvent({
        event_type_slug: type.trim(),
        display_name: name.trim(),
        date: date.trim(),
        host_house_slug: host.trim() || null,
      })
      setType('')
      setName('')
      setDate('')
      setHost('')
      onCreated()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const ready = type && name && date

  return (
    <LedgerSection title="Enter an Event" hint="ad-hoc addition">
      <div className="grid gap-4 px-4 py-4 sm:grid-cols-2 lg:grid-cols-5 lg:items-end">
        <label className="block">
          <span className="mb-1 block font-mono text-[10px] uppercase tracking-[0.18em] text-ink-500">
            Type
          </span>
          <select
            value={type}
            onChange={(e) => setType(e.target.value)}
            className="w-full border-b border-ink-700/50 bg-transparent px-1 py-1.5 text-sm text-ink-100 focus:border-brass-500 focus:outline-none"
          >
            <option value="">— select type —</option>
            {(eventTypes.data ?? []).map((t) => (
              <option key={t.slug} value={t.slug}>
                {t.display_name}
              </option>
            ))}
          </select>
        </label>
        <LedgerField label="Name" value={name} onChange={setName} placeholder="ZTA Mixer" />
        <LedgerField label="Date" value={date} onChange={setDate} placeholder="2026-09-12" />
        <LedgerField label="Host" value={host} onChange={setHost} placeholder="zta (optional)" />
        <PenButton disabled={!ready || busy} onClick={submit} className="justify-center">
          {busy ? 'entering…' : 'enter event'}
        </PenButton>
      </div>
      {noSlots && (
        <p className="px-4 pb-3 font-mono text-[11px] text-oxblood-300">
          ⚠ {picked!.display_name} has no shift slots configured — auto-assign will post nobody.
        </p>
      )}
      {err && <ErrorNote message={err} />}
    </LedgerSection>
  )
}
