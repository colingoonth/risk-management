import { useMemo, useState } from 'react'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { ErrorNote } from '../components/ui'
import { LedgerSection, PenButton } from '../components/ledger'
import type { EventRow, Shift } from '../lib/types'

type Target = 'counterparty' | 'open'

const STATE_FILTERS = ['all', 'open', 'accepted', 'rejected', 'cancelled'] as const

const STATE_CLS: Record<string, string> = {
  open: 'text-brass-400',
  accepted: 'text-ink-300',
  rejected: 'text-oxblood-300',
  cancelled: 'text-ink-500',
}

export function Swaps() {
  const members = useAsync(() => api.listMembers(), [])
  const events = useAsync(() => api.listEvents(), [])
  const allShifts = useAsync(() => api.listShifts({}), [])

  const [stateFilter, setStateFilter] = useState<(typeof STATE_FILTERS)[number]>('open')
  const swaps = useAsync(
    () => api.listSwaps(stateFilter === 'all' ? undefined : stateFilter),
    [stateFilter],
  )

  // Raise-a-swap form
  const [initiator, setInitiator] = useState('')
  const [fromShiftId, setFromShiftId] = useState('')
  const [target, setTarget] = useState<Target>('counterparty')
  const [counterparty, setCounterparty] = useState('')
  const [toShiftId, setToShiftId] = useState('')

  const [busy, setBusy] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  const eventMap = useMemo(
    () => new Map<number, EventRow>((events.data ?? []).map((e) => [e.id, e])),
    [events.data],
  )
  const shiftMap = useMemo(
    () => new Map<number, Shift>((allShifts.data ?? []).map((s) => [s.id, s])),
    [allShifts.data],
  )

  function shiftLabel(s: Shift | undefined): string {
    if (!s) return '—'
    const ev = eventMap.get(s.event_id)
    const head = ev ? `${ev.display_name} · ${ev.date}` : `event #${s.event_id}`
    return `${head} · ${s.shift_type_slug} #${s.slot_index + 1}`
  }

  const mine = (allShifts.data ?? []).filter(
    (s) => s.assigned_member_slug === initiator && s.status === 'assigned',
  )
  const openSlots = (allShifts.data ?? []).filter((s) => s.status === 'open')

  async function run(key: string, fn: () => Promise<unknown>, after: () => void) {
    setBusy(key)
    setNote(null)
    try {
      await fn()
      after()
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  function pickInitiator(slug: string) {
    setInitiator(slug)
    setFromShiftId('') // a new subject starts with no shift chosen
  }

  const targetReady = target === 'counterparty' ? !!counterparty : !!toShiftId
  const canRaise = !!fromShiftId && targetReady && busy === null

  function raise() {
    const body =
      target === 'counterparty'
        ? { from_shift_id: Number(fromShiftId), counterparty_member_slug: counterparty }
        : { from_shift_id: Number(fromShiftId), to_shift_id: Number(toShiftId) }
    run('raise', () => api.createSwap(body), () => {
      setFromShiftId('')
      setCounterparty('')
      setToShiftId('')
      allShifts.reload()
      swaps.reload()
    })
  }

  const selectCls =
    'w-full border-b border-ink-700/50 bg-transparent px-1 py-1.5 text-sm text-ink-100 focus:border-brass-500 focus:outline-none'

  return (
    <div>
      {note && <ErrorNote message={note} />}

      <div className="flex flex-col gap-8 lg:flex-row">
        {/* ── RAISE A SWAP ─────────────────────────────────────── */}
        <aside className="lg:flex-1">
          <LedgerSection title="Raise a swap" hint="trade a brother off his shift" band>
            <div className="space-y-3 px-4 py-3">
              <label className="block">
                <span className="mb-1 block font-mono text-[10px] uppercase tracking-[0.18em] text-ink-500">
                  Brother
                </span>
                <select value={initiator} onChange={(e) => pickInitiator(e.target.value)} className={selectCls}>
                  <option value="">— select brother —</option>
                  {(members.data ?? []).map((m) => (
                    <option key={m.slug} value={m.slug}>
                      {m.display_name}
                    </option>
                  ))}
                </select>
              </label>

              <label className="block">
                <span className="mb-1 block font-mono text-[10px] uppercase tracking-[0.18em] text-ink-500">
                  His shift
                </span>
                <select
                  value={fromShiftId}
                  onChange={(e) => setFromShiftId(e.target.value)}
                  disabled={!initiator}
                  className={selectCls}
                >
                  <option value="">
                    {!initiator ? '— pick a brother first —' : mine.length ? '— select shift —' : 'no assigned shifts'}
                  </option>
                  {mine.map((s) => (
                    <option key={s.id} value={s.id}>
                      {shiftLabel(s)}
                    </option>
                  ))}
                </select>
              </label>

              {/* Target toggle — the two real swap shapes. */}
              <div className="flex gap-1 pt-1">
                {(['counterparty', 'open'] as Target[]).map((t) => (
                  <button
                    key={t}
                    onClick={() => setTarget(t)}
                    className={`flex-1 rounded-[2px] border py-1.5 font-mono text-[10px] uppercase tracking-[0.15em] transition-colors ${
                      target === t
                        ? 'border-brass-500 bg-brass-300/40 text-ink-100'
                        : 'border-ink-700/40 text-ink-500 hover:text-ink-300'
                    }`}
                  >
                    {t === 'counterparty' ? 'Swap with a brother' : 'Move to open slot'}
                  </button>
                ))}
              </div>

              {target === 'counterparty' ? (
                <label className="block">
                  <span className="mb-1 block font-mono text-[10px] uppercase tracking-[0.18em] text-ink-500">
                    Counterparty
                  </span>
                  <select value={counterparty} onChange={(e) => setCounterparty(e.target.value)} className={selectCls}>
                    <option value="">— select brother —</option>
                    {(members.data ?? [])
                      .filter((m) => m.slug !== initiator)
                      .map((m) => (
                        <option key={m.slug} value={m.slug}>
                          {m.display_name}
                        </option>
                      ))}
                  </select>
                </label>
              ) : (
                <label className="block">
                  <span className="mb-1 block font-mono text-[10px] uppercase tracking-[0.18em] text-ink-500">
                    Open slot
                  </span>
                  <select value={toShiftId} onChange={(e) => setToShiftId(e.target.value)} className={selectCls}>
                    <option value="">{openSlots.length ? '— select open slot —' : 'no open slots'}</option>
                    {openSlots.map((s) => (
                      <option key={s.id} value={s.id}>
                        {shiftLabel(s)}
                      </option>
                    ))}
                  </select>
                </label>
              )}

              <PenButton disabled={!canRaise} onClick={raise}>
                {busy === 'raise' ? 'raising…' : 'Raise swap'}
              </PenButton>
            </div>
          </LedgerSection>
        </aside>

        {/* ── THE SWAPS — full register ────────────────────────── */}
        <section className="lg:flex-[1.7]">
          <div className="flex items-baseline justify-between border-b border-ink-700/40 px-1 py-2.5">
            <div className="flex items-baseline gap-3">
              <h2 className="ledger-cap">The Swaps</h2>
              <span className="text-xs text-ink-500">trades on the register</span>
            </div>
            <div className="flex gap-3">
              {STATE_FILTERS.map((f) => (
                <button
                  key={f}
                  onClick={() => setStateFilter(f)}
                  className={`font-mono text-[10px] uppercase tracking-[0.16em] transition-colors ${
                    stateFilter === f ? 'text-brass-300' : 'text-ink-500 hover:text-ink-300'
                  }`}
                >
                  {f}
                </button>
              ))}
            </div>
          </div>

          {swaps.loading ? (
            <p className="px-1 py-6 font-mono text-xs text-ink-700">loading…</p>
          ) : (swaps.data?.length ?? 0) === 0 ? (
            <p className="px-1 py-6 text-sm italic text-ink-500">No swaps on the register.</p>
          ) : (
            <ul>
              {swaps.data!.map((s) => {
                const towards = s.counterparty_slug ?? (s.to_shift_id != null ? 'open slot' : '—')
                return (
                  <li
                    key={s.id}
                    className="flex items-center gap-4 border-b border-ink-700/25 py-3 pl-1 pr-2 transition-colors hover:bg-char-850/40"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="truncate font-mono text-sm text-ink-100">
                        {s.initiator_slug} <span className="text-brass-400">⇄</span> {towards}
                      </div>
                      <div className="truncate font-mono text-[11px] text-ink-500">
                        {shiftLabel(shiftMap.get(s.from_shift_id))}
                      </div>
                    </div>
                    <span
                      className={`w-20 font-mono text-[11px] uppercase tracking-wider ${STATE_CLS[s.state] ?? 'text-ink-500'}`}
                    >
                      {s.state}
                    </span>
                    {s.state === 'open' ? (
                      <span className="flex shrink-0 gap-3 font-mono text-[11px]">
                        <button
                          disabled={busy !== null}
                          onClick={() => run(`a${s.id}`, () => api.acceptSwap(s.id), () => { allShifts.reload(); swaps.reload() })}
                          className="text-brass-400 hover:text-brass-300 disabled:text-ink-700"
                        >
                          accept
                        </button>
                        <button
                          disabled={busy !== null}
                          onClick={() => run(`r${s.id}`, () => api.rejectSwap(s.id), () => swaps.reload())}
                          className="text-ink-500 hover:text-oxblood-300 disabled:text-ink-700"
                        >
                          reject
                        </button>
                        <button
                          disabled={busy !== null}
                          onClick={() => run(`c${s.id}`, () => api.cancelSwap(s.id), () => swaps.reload())}
                          className="text-ink-500 hover:text-ink-300 disabled:text-ink-700"
                        >
                          cancel
                        </button>
                      </span>
                    ) : (
                      <span className="w-[8.5rem] shrink-0" />
                    )}
                  </li>
                )
              })}
            </ul>
          )}
        </section>
      </div>

      {(members.error || swaps.error || allShifts.error) && (
        <ErrorNote message={members.error || swaps.error || allShifts.error || ''} />
      )}
    </div>
  )
}
