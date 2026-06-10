import { useState } from 'react'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import type { Shift } from '../lib/types'
import { Button, Card, Empty, ErrorNote, SectionHeader, Spinner, Tag } from '../components/ui'

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
      setNote(`${r.event_name}: ${filled} assigned (mode ${r.resolved_mode}).`)
      reload()
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">Events</h1>
      {note && <ErrorNote message={note} />}

      <CreateEvent onCreated={reload} />

      <Card>
        <SectionHeader title="Schedule" />
        {loading ? (
          <Spinner label="Loading events" />
        ) : error ? (
          <ErrorNote message={error} />
        ) : !events || events.length === 0 ? (
          <Empty>No events yet. Add one above to start the semester.</Empty>
        ) : (
          <ul className="divide-y divide-brass-500/10">
            {events.map((e) => (
              <li key={e.id}>
                <div className="flex items-center gap-4 px-5 py-3">
                  <span className="w-20 shrink-0 font-mono text-sm text-brass-300">{e.date}</span>
                  <button
                    className="min-w-0 flex-1 text-left"
                    onClick={() => setOpen(open === e.id ? null : e.id)}
                  >
                    <div className="truncate font-medium">{e.display_name}</div>
                    <div className="font-mono text-xs text-ink-500">
                      {e.event_type_slug}
                      {e.host_house_slug && ` · ${e.host_house_slug}`}
                    </div>
                  </button>
                  <Tag tone={e.status === 'cancelled' ? 'neutral' : 'filled'}>{e.status}</Tag>
                  <Button variant="ghost" disabled={busy !== null} onClick={() => assign(e.id)}>
                    {busy === e.id ? 'Assigning…' : 'Auto-assign'}
                  </Button>
                </div>
                {open === e.id && <ShiftList eventId={e.id} />}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}

function ShiftList({ eventId }: { eventId: number }) {
  const { data, loading } = useAsync<Shift[]>(() => api.eventShifts(eventId), [eventId])
  if (loading) return <Spinner label="Loading shifts" />
  if (!data || data.length === 0)
    return <Empty>No shift slots snapshotted for this event type.</Empty>
  return (
    <div className="border-t border-brass-500/10 bg-char-850 px-5 py-3">
      <table className="w-full font-mono text-sm">
        <tbody>
          {data.map((s) => (
            <tr key={s.id} className="border-b border-brass-500/5 last:border-0">
              <td className="py-1.5 text-ink-300">
                {s.shift_type_slug} #{s.slot_index}
              </td>
              <td className="py-1.5 text-right">
                {s.assigned_member_slug ? (
                  <span className="text-ink-100">{s.assigned_member_slug}</span>
                ) : (
                  <span className="text-signal-open">— open —</span>
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
    <Card>
      <SectionHeader title="Add event (ad-hoc)" />
      <div className="grid gap-3 px-5 py-4 sm:grid-cols-2 lg:grid-cols-5">
        <Field label="Type" value={type} onChange={setType} placeholder="mixer / dage / philanthropy" />
        <Field label="Name" value={name} onChange={setName} placeholder="ZTA Mixer" />
        <Field label="Date" value={date} onChange={setDate} placeholder="2026-09-12" />
        <Field label="Host (opt)" value={host} onChange={setHost} placeholder="zta" />
        <div className="flex items-end">
          <Button variant="primary" disabled={!ready || busy} onClick={submit} className="w-full justify-center">
            {busy ? 'Adding…' : 'Add event'}
          </Button>
        </div>
      </div>
      {err && <ErrorNote message={err} />}
    </Card>
  )
}

function Field({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  placeholder?: string
}) {
  return (
    <label className="block">
      <span className="mb-1 block font-mono text-xs uppercase tracking-wider text-ink-500">{label}</span>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full rounded-[3px] border border-brass-500/25 bg-char-950 px-3 py-1.5 text-sm text-ink-100 placeholder:text-ink-500/60 focus:border-brass-500/70 focus:outline-none"
      />
    </label>
  )
}
