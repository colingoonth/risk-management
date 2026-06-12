import { useState } from 'react'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { ErrorNote } from '../components/ui'
import { DayStamp, FillBar, StatusGlyph, Tally } from '../components/ledger'

const TODAY = new Date().toISOString().slice(0, 10)

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

  if (loading) return <LedgerSkeleton />
  if (error) return <ErrorNote message={error} />
  if (!data) return null

  const { unfilled_events, members_needing_shifts, pending_swaps, pending_consequences } = data

  return (
    <div>
      {note && <ErrorNote message={note} />}

      {/* ── THE DOCKET (beige band) ────────────────────────────── */}
      <section className="bg-brass-300/30">
        <div className="flex items-baseline justify-between border-b border-ink-700/40 px-4 py-2.5">
          <div className="flex items-baseline gap-3">
            <h2 className="ledger-cap">The Docket</h2>
            <span className="text-xs text-ink-500">what needs your signature</span>
          </div>
          <button
            disabled={busy !== null || unfilled_events.length === 0}
            onClick={() => run('bulk', () => api.autoAssignBulk())}
            className="font-mono text-[11px] uppercase tracking-[0.18em] text-brass-400 transition-colors hover:text-brass-300 disabled:text-ink-700"
          >
            {busy === 'bulk' ? 'posting…' : 'post all ⇥'}
          </button>
        </div>

        {unfilled_events.length === 0 ? (
          <p className="px-4 py-8 text-center text-sm italic text-ink-500">
            The register is clear. No shifts await your signature.
          </p>
        ) : (
          <ul>
            {unfilled_events.map((e) => {
              const overdue = e.date < TODAY
              const filled = e.total_slots - e.open_slots
              return (
                <li
                  key={e.event_id}
                  className="group relative flex items-center gap-4 border-b border-ink-700/30 py-3 pl-5 pr-4 transition-colors hover:bg-brass-300/40"
                >
                  <span className="absolute left-0 top-0 h-full w-[3px] bg-oxblood-600 transition-all group-hover:w-1" />
                  <DayStamp iso={e.date} />
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-medium text-ink-100">{e.display_name}</div>
                    <div className="font-mono text-[11px] uppercase tracking-wider text-ink-500">
                      {e.event_type_slug}
                      {e.host_house_slug && ` · ${e.host_house_slug}`}
                      {e.resync_pending && <span className="ml-2 text-brass-400">· resync</span>}
                    </div>
                  </div>
                  <FillBar filled={filled} total={e.total_slots} />
                  <span className="w-28">
                    <StatusGlyph state={overdue ? 'overdue' : 'unfilled'} />
                  </span>
                  <button
                    disabled={busy !== null}
                    onClick={() => run(`ev${e.event_id}`, () => api.autoAssign(e.event_id))}
                    className="shrink-0 rounded-[2px] bg-brass-500 px-3 py-1.5 font-mono text-[11px] font-medium uppercase tracking-[0.15em] text-char-950 transition-colors hover:bg-brass-400 disabled:bg-brass-600/40 disabled:text-ink-500"
                  >
                    {busy === `ev${e.event_id}` ? 'posting…' : 'assign'}
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      </section>

      {/* ── THE ROLL + THE MARGIN (asymmetric) ─────────────────── */}
      <div className="mt-10 flex flex-col gap-8 lg:flex-row">
        {/* THE ROLL — who hasn't worked (worst first) */}
        <section className="lg:flex-[1.7]">
          <div className="flex items-baseline gap-3 border-b border-ink-700/40 px-1 py-2.5">
            <h2 className="ledger-cap">The Roll</h2>
            <span className="text-xs text-ink-500">who hasn't worked</span>
          </div>
          {members_needing_shifts.length === 0 ? (
            <p className="px-1 py-6 text-sm italic text-ink-500">No brothers on the roll.</p>
          ) : (
            <ul>
              {members_needing_shifts.map((m) => (
                <li
                  key={m.member_slug}
                  className="flex items-center gap-4 border-b border-ink-700/20 py-2.5 pl-1 pr-2 transition-colors hover:bg-char-850/40"
                >
                  <span className="flex-1 truncate text-ink-100">{m.display_name}</span>
                  <span className="min-w-[6rem] text-right">
                    <Tally count={m.shift_count} />
                  </span>
                  <span
                    className={`w-10 text-right font-mono text-sm tabular-nums ${
                      m.shift_count === 0 ? 'text-oxblood-300' : 'text-ink-500'
                    }`}
                  >
                    ({m.shift_count})
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* THE MARGIN — swaps + consequences (marginalia) */}
        <aside className="border-ink-700/40 lg:flex-1 lg:border-l lg:pl-8">
          <div className="border-b border-ink-700/40 py-2.5">
            <h2 className="ledger-cap">The Margin</h2>
          </div>

          <h3 className="mt-4 font-mono text-[10px] uppercase tracking-[0.2em] text-ink-500">
            Swaps pending
          </h3>
          {pending_swaps.length === 0 ? (
            <p className="py-2 text-sm italic text-ink-500">None.</p>
          ) : (
            <ul className="mt-1">
              {pending_swaps.map((s) => (
                <li key={s.id} className="flex items-center justify-between gap-2 py-2">
                  <span className="min-w-0 truncate font-mono text-xs text-ink-300">
                    <span className="text-brass-400">⇄</span> {s.initiator_slug} →{' '}
                    {s.counterparty_slug ?? 'open'}
                  </span>
                  <span className="flex shrink-0 gap-2 font-mono text-[11px]">
                    <button
                      disabled={busy !== null}
                      onClick={() => run(`sw${s.id}`, () => api.acceptSwap(s.id))}
                      className="text-brass-400 hover:text-brass-300 disabled:text-ink-700"
                    >
                      [Y]
                    </button>
                    <button
                      disabled={busy !== null}
                      onClick={() => run(`sw${s.id}`, () => api.rejectSwap(s.id))}
                      className="text-ink-500 hover:text-oxblood-300 disabled:text-ink-700"
                    >
                      [N]
                    </button>
                  </span>
                </li>
              ))}
            </ul>
          )}

          <h3 className="mt-6 font-mono text-[10px] uppercase tracking-[0.2em] text-ink-500">
            Consequences owed
          </h3>
          {pending_consequences.length === 0 ? (
            <p className="py-2 text-sm italic text-ink-500">No standing sanctions.</p>
          ) : (
            <ul className="mt-1">
              {pending_consequences.map((c) => (
                <li key={c.id} className="flex items-center justify-between py-2">
                  <span className="font-mono text-xs text-ink-300">
                    {c.member_slug ?? `member #${c.member_id}`}
                  </span>
                  <span className="font-mono text-[11px] uppercase tracking-wider text-oxblood-300">
                    {c.kind}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </aside>
      </div>

      {/* Posting line */}
      <p className="mt-10 border-t border-ink-700/30 pt-3 font-mono text-[11px] uppercase tracking-[0.18em] text-ink-700">
        register posted {TODAY} · {unfilled_events.length} open · {members_needing_shifts.length} on
        roll · {pending_swaps.length} swaps
      </p>
    </div>
  )
}

function LedgerSkeleton() {
  return (
    <div className="animate-pulse">
      <div className="border-b border-ink-700/40 py-2.5">
        <span className="ledger-cap">The Docket</span>
      </div>
      {[0, 1, 2].map((i) => (
        <div key={i} className="flex items-center gap-4 border-b border-ink-700/20 py-4 pl-5">
          <div className="h-8 w-12 bg-char-800" />
          <div className="h-4 flex-1 bg-char-800" />
          <div className="h-3 w-20 bg-char-800" />
        </div>
      ))}
      <p className="mt-6 font-mono text-xs text-ink-700">posting entries…</p>
    </div>
  )
}
