import { useState } from 'react'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import type { Shift } from '../lib/types'
import { ErrorNote } from '../components/ui'
import {
  DayStamp,
  LedgerField,
  LedgerSection,
  PenButton,
  StatusGlyph,
} from '../components/ledger'

export function Events() {
  const { data: events, loading, error, reload } = useAsync(() => api.listEvents(), [])
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
            {events.map((e) => (
              <li key={e.id} className="border-b border-ink-700/30 last:border-0">
                <div className="flex items-center gap-4 py-3 pl-4 pr-4 transition-colors hover:bg-char-850/40">
                  <DayStamp iso={e.date} />
                  <button
                    className="min-w-0 flex-1 text-left"
                    onClick={() => setOpen(open === e.id ? null : e.id)}
                  >
                    <div className="truncate text-ink-100">{e.display_name}</div>
                    <div className="font-mono text-[11px] uppercase tracking-wider text-ink-500">
                      {e.event_type_slug}
                      {e.host_house_slug && ` · ${e.host_house_slug}`}
                    </div>
                  </button>
                  <span className="w-24 font-mono text-[11px] uppercase tracking-wider text-ink-500">
                    {e.status === 'cancelled' ? 'cancelled' : e.status}
                  </span>
                  <PenButton disabled={busy !== null} onClick={() => assign(e.id)}>
                    {busy === e.id ? 'posting…' : 'auto-assign'}
                  </PenButton>
                </div>
                {open === e.id && <ShiftList eventId={e.id} />}
              </li>
            ))}
          </ul>
        )}
      </LedgerSection>
    </div>
  )
}

function ShiftList({ eventId }: { eventId: number }) {
  const { data, loading } = useAsync<Shift[]>(() => api.eventShifts(eventId), [eventId])
  if (loading) return <p className="px-4 py-3 text-sm italic text-ink-500">Loading shifts…</p>
  if (!data || data.length === 0)
    return (
      <p className="border-t border-ink-700/20 bg-char-900/40 px-4 py-3 text-sm italic text-ink-500">
        No shift slots snapshotted for this event type.
      </p>
    )
  return (
    <div className="border-t border-ink-700/20 bg-char-900/50 px-4 py-3">
      <table className="w-full font-mono text-sm">
        <tbody>
          {data.map((s) => (
            <tr key={s.id} className="border-b border-ink-700/15 last:border-0">
              <td className="py-1.5 text-ink-500">
                {s.shift_type_slug} #{s.slot_index}
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
    </div>
  )
}

function CreateEvent({ onCreated }: { onCreated: () => void }) {
  const [type, setType] = useState('')
  const [name, setName] = useState('')
  const [date, setDate] = useState('')
  const [host, setHost] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

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
        <LedgerField label="Type" value={type} onChange={setType} placeholder="mixer / dage" />
        <LedgerField label="Name" value={name} onChange={setName} placeholder="ZTA Mixer" />
        <LedgerField label="Date" value={date} onChange={setDate} placeholder="2026-09-12" />
        <LedgerField label="Host" value={host} onChange={setHost} placeholder="zta (optional)" />
        <PenButton disabled={!ready || busy} onClick={submit} className="justify-center">
          {busy ? 'entering…' : 'enter event'}
        </PenButton>
      </div>
      {err && <ErrorNote message={err} />}
    </LedgerSection>
  )
}
