import { useState } from 'react'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { Button, Card, Empty, ErrorNote, SectionHeader, Spinner, Tag } from '../components/ui'

export function Dashboard() {
  const { data, loading, error, reload } = useAsync(() => api.dashboard(), [])
  const [busy, setBusy] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  async function run(key: string, fn: () => Promise<unknown>) {
    setBusy(key)
    setNote(null)
    try {
      await fn()
      reload()
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  if (loading) return <Spinner label="Loading dashboard" />
  if (error) return <ErrorNote message={error} />
  if (!data) return null

  const { unfilled_events, members_needing_shifts, pending_swaps, pending_consequences } = data

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">
          What needs your attention
          <span className="ml-3 font-mono text-sm text-ink-500">{data.semester_name}</span>
        </h1>
        <Button
          variant="primary"
          disabled={busy !== null || unfilled_events.length === 0}
          onClick={() => run('bulk', () => api.autoAssignBulk())}
        >
          {busy === 'bulk' ? 'Filling…' : 'Bulk fill all events'}
        </Button>
      </div>

      {note && <ErrorNote message={note} />}

      {/* Unfilled events — the #1 chair concern, dates always visible */}
      <Card>
        <SectionHeader
          title="Unfilled events"
          right={<Tag tone={unfilled_events.length ? 'open' : 'filled'}>{unfilled_events.length} open</Tag>}
        />
        {unfilled_events.length === 0 ? (
          <Empty>Every event is fully staffed. 🎉</Empty>
        ) : (
          <ul className="divide-y divide-brass-500/10">
            {unfilled_events.map((e) => (
              <li key={e.event_id} className="flex items-center gap-4 px-5 py-3">
                <span className="w-20 shrink-0 font-mono text-sm text-brass-300">{e.date}</span>
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium">{e.display_name}</div>
                  <div className="font-mono text-xs text-ink-500">
                    {e.event_type_slug}
                    {e.host_house_slug && ` · ${e.host_house_slug}`}
                    {e.resync_pending && <span className="ml-2 text-signal-warn">resync pending</span>}
                  </div>
                </div>
                <Tag tone="open">
                  {e.total_slots - e.open_slots}/{e.total_slots}
                </Tag>
                <Button
                  variant="ghost"
                  disabled={busy !== null}
                  onClick={() => run(`ev${e.event_id}`, () => api.autoAssign(e.event_id))}
                >
                  {busy === `ev${e.event_id}` ? 'Assigning…' : 'Assign →'}
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <div className="grid gap-6 md:grid-cols-2">
        {/* Who hasn't worked */}
        <Card>
          <SectionHeader title="Who hasn't worked" />
          {members_needing_shifts.length === 0 ? (
            <Empty>No active members.</Empty>
          ) : (
            <ul className="divide-y divide-brass-500/10">
              {members_needing_shifts.map((m) => (
                <li key={m.member_slug} className="flex items-center justify-between px-5 py-2.5">
                  <span className="truncate">{m.display_name}</span>
                  <Tag tone={m.shift_count === 0 ? 'open' : 'neutral'}>{m.shift_count} shifts</Tag>
                </li>
              ))}
            </ul>
          )}
        </Card>

        {/* Pending swaps */}
        <Card>
          <SectionHeader
            title="Pending swaps"
            right={<Tag tone={pending_swaps.length ? 'warn' : 'neutral'}>{pending_swaps.length}</Tag>}
          />
          {pending_swaps.length === 0 ? (
            <Empty>No open swap requests.</Empty>
          ) : (
            <ul className="divide-y divide-brass-500/10">
              {pending_swaps.map((s) => (
                <li key={s.id} className="flex items-center justify-between gap-3 px-5 py-2.5">
                  <span className="min-w-0 truncate font-mono text-sm">
                    {s.initiator_slug} → {s.counterparty_slug ?? 'open slot'}
                  </span>
                  <div className="flex gap-1.5">
                    <Button
                      variant="primary"
                      disabled={busy !== null}
                      onClick={() => run(`sw${s.id}`, () => api.acceptSwap(s.id))}
                    >
                      Accept
                    </Button>
                    <Button
                      variant="ghost"
                      disabled={busy !== null}
                      onClick={() => run(`sw${s.id}`, () => api.rejectSwap(s.id))}
                    >
                      Reject
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {/* Pending consequences */}
      {pending_consequences.length > 0 && (
        <Card>
          <SectionHeader
            title="Strike consequences owed"
            right={<Tag tone="warn">{pending_consequences.length}</Tag>}
          />
          <ul className="divide-y divide-brass-500/10">
            {pending_consequences.map((c) => (
              <li key={c.id} className="flex items-center justify-between px-5 py-2.5">
                <span className="font-mono text-sm">member #{c.member_id}</span>
                <Tag tone="open">{c.kind}</Tag>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  )
}
