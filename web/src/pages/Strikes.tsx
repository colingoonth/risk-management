import { useState } from 'react'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { ErrorNote } from '../components/ui'
import { DayStamp, LedgerField, LedgerSection, PenButton } from '../components/ledger'

const TODAY = new Date().toISOString().slice(0, 10)
const PROBATION_AT = 4
const EXPULSION_REVIEW_AT = 5

export function Strikes() {
  const members = useAsync(() => api.listMembers(), [])
  const removalMethods = useAsync(() => api.listRemovalMethods(), [])

  const [member, setMember] = useState('')
  const strikes = useAsync(
    () => (member ? api.listStrikes(member) : Promise.resolve([])),
    [member],
  )
  const consequences = useAsync(() => api.listConsequences('pending'), [])

  // Issue form
  const [issuedOn, setIssuedOn] = useState(TODAY)
  const [reason, setReason] = useState('')
  // Removal form
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [method, setMethod] = useState('')
  const [performedOn, setPerformedOn] = useState(TODAY)
  const [removalNotes, setRemovalNotes] = useState('')

  const [busy, setBusy] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

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

  // Selecting a different brother starts a clean ledger — drop any stale
  // checkbox selection so removal never targets the previous member's rows.
  function pickMember(slug: string) {
    setMember(slug)
    setSelected(new Set())
  }

  function toggle(id: number) {
    setSelected((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const activeCount = strikes.data?.length ?? 0
  const standing =
    activeCount >= EXPULSION_REVIEW_AT
      ? { label: 'EXPULSION REVIEW', cls: 'text-oxblood-300' }
      : activeCount >= PROBATION_AT
        ? { label: 'PROBATION', cls: 'text-oxblood-300' }
        : { label: 'IN GOOD STANDING', cls: 'text-ink-500' }

  const methodList = removalMethods.data ?? []

  return (
    <div>
      {note && <ErrorNote message={note} />}

      {/* ── THE SUBJECT — pick whose ledger to open ─────────────── */}
      <div className="flex items-baseline justify-between border-b border-ink-700/40 px-1 py-2.5">
        <div className="flex items-baseline gap-3">
          <h2 className="ledger-cap">The Subject</h2>
          <span className="text-xs text-ink-500">open a brother's strike ledger</span>
        </div>
        <select
          value={member}
          onChange={(e) => pickMember(e.target.value)}
          className="border-b border-ink-700/50 bg-transparent px-1 py-1 font-mono text-sm text-ink-100 focus:border-brass-500 focus:outline-none"
        >
          <option value="">— select brother —</option>
          {(members.data ?? []).map((m) => (
            <option key={m.slug} value={m.slug}>
              {m.display_name}
            </option>
          ))}
        </select>
      </div>

      {!member ? (
        <p className="px-1 py-10 text-center text-sm italic text-ink-500">
          No subject selected. Choose a brother to review his standing.
        </p>
      ) : (
        <div className="mt-8 flex flex-col gap-8 lg:flex-row">
          {/* ── THE LEDGER — the strike ladder ───────────────────── */}
          <section className="lg:flex-[1.6]">
            <div className="flex items-baseline justify-between border-b border-ink-700/40 px-1 py-2.5">
              <div className="flex items-baseline gap-3">
                <h2 className="ledger-cap">The Ledger</h2>
                <span className="text-xs text-ink-500">{member}</span>
              </div>
              <span className={`font-mono text-[11px] uppercase tracking-[0.18em] ${standing.cls}`}>
                {activeCount} active · {standing.label}
              </span>
            </div>

            {strikes.loading ? (
              <p className="px-1 py-6 font-mono text-xs text-ink-700">opening ledger…</p>
            ) : activeCount === 0 ? (
              <p className="px-1 py-6 text-sm italic text-ink-500">
                A clean ledger. No active strikes.
              </p>
            ) : (
              <ul>
                {strikes.data!.map((s) => {
                  const crosses =
                    s.strike_number >= EXPULSION_REVIEW_AT
                      ? 'bg-oxblood-600'
                      : s.strike_number >= PROBATION_AT
                        ? 'bg-oxblood-600/60'
                        : 'bg-ink-700/40'
                  return (
                    <li
                      key={s.id}
                      className="group relative flex items-center gap-4 border-b border-ink-700/25 py-3 pl-5 pr-2 transition-colors hover:bg-char-850/40"
                    >
                      <span className={`absolute left-0 top-0 h-full w-[3px] ${crosses}`} />
                      <label className="flex cursor-pointer items-center">
                        <input
                          type="checkbox"
                          checked={selected.has(s.id)}
                          onChange={() => toggle(s.id)}
                          className="h-3.5 w-3.5 accent-brass-500"
                        />
                      </label>
                      <span className="w-8 shrink-0 text-center font-mono text-lg font-medium tabular-nums text-ink-100">
                        {s.strike_number}
                      </span>
                      <DayStamp iso={s.issued_on} />
                      <span className="min-w-0 flex-1 truncate text-ink-100">{s.reason}</span>
                    </li>
                  )
                })}
              </ul>
            )}
          </section>

          {/* ── THE PEN — issue + remove ─────────────────────────── */}
          <aside className="border-ink-700/40 lg:flex-1 lg:border-l lg:pl-8">
            <LedgerSection title="Issue a strike" hint={member} band>
              <div className="space-y-3 px-4 py-3">
                <LedgerField label="Issued on" value={issuedOn} onChange={setIssuedOn} />
                <LedgerField
                  label="Reason"
                  value={reason}
                  onChange={setReason}
                  placeholder="e.g. no-show, late, left early"
                />
                <PenButton
                  disabled={busy !== null || !reason.trim() || !issuedOn}
                  onClick={() =>
                    run(
                      'issue',
                      () =>
                        api.issueStrike({
                          member_slug: member,
                          issued_on: issuedOn,
                          reason: reason.trim(),
                        }),
                      () => {
                        setReason('')
                        strikes.reload()
                        consequences.reload()
                      },
                    )
                  }
                >
                  {busy === 'issue' ? 'issuing…' : 'Issue strike'}
                </PenButton>
              </div>
            </LedgerSection>

            <div className="mt-6">
              <h3 className="border-b border-ink-700/40 pb-2 font-mono text-[10px] uppercase tracking-[0.2em] text-ink-500">
                Record a removal
              </h3>
              {methodList.length === 0 ? (
                <p className="py-3 text-sm italic text-ink-500">
                  No active removal methods configured.
                </p>
              ) : (
                <div className="space-y-3 pt-3">
                  <p className="font-mono text-[11px] text-ink-500">
                    {selected.size === 0
                      ? 'check strikes in the ledger to remove them'
                      : `${selected.size} strike${selected.size > 1 ? 's' : ''} checked`}
                  </p>
                  <label className="block">
                    <span className="mb-1 block font-mono text-[10px] uppercase tracking-[0.18em] text-ink-500">
                      Method
                    </span>
                    <select
                      value={method}
                      onChange={(e) => setMethod(e.target.value)}
                      className="w-full border-b border-ink-700/50 bg-transparent px-1 py-1.5 text-sm text-ink-100 focus:border-brass-500 focus:outline-none"
                    >
                      <option value="">— select method —</option>
                      {methodList.map((m) => (
                        <option key={m.slug} value={m.slug}>
                          {m.display_name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <LedgerField label="Performed on" value={performedOn} onChange={setPerformedOn} />
                  <LedgerField
                    label="Notes (optional)"
                    value={removalNotes}
                    onChange={setRemovalNotes}
                    placeholder="context for the record"
                  />
                  <PenButton
                    disabled={busy !== null || selected.size === 0 || !method || !performedOn}
                    onClick={() =>
                      run(
                        'remove',
                        () =>
                          api.removeStrikes({
                            member_slug: member,
                            removal_method_slug: method,
                            performed_on: performedOn,
                            strike_ids: [...selected],
                            notes: removalNotes.trim() || undefined,
                          }),
                        () => {
                          setSelected(new Set())
                          setRemovalNotes('')
                          strikes.reload()
                        },
                      )
                    }
                  >
                    {busy === 'remove' ? 'recording…' : 'Record removal'}
                  </PenButton>
                </div>
              )}
            </div>
          </aside>
        </div>
      )}

      {/* ── CONSEQUENCES OWED — chapter-wide, decoupled from count ── */}
      <section className="mt-12">
        <div className="flex items-baseline gap-3 border-b border-ink-700/40 px-1 py-2.5">
          <h2 className="ledger-cap">Consequences Owed</h2>
          <span className="text-xs text-ink-500">
            crossed thresholds awaiting resolution — they persist even after strikes are removed
          </span>
        </div>
        {consequences.loading ? (
          <p className="px-1 py-4 font-mono text-xs text-ink-700">loading…</p>
        ) : (consequences.data?.length ?? 0) === 0 ? (
          <p className="px-1 py-4 text-sm italic text-ink-500">No standing sanctions.</p>
        ) : (
          <ul>
            {consequences.data!.map((c) => (
              <li
                key={c.id}
                className="flex items-center justify-between gap-4 border-b border-ink-700/20 py-2.5 pl-1 pr-2"
              >
                <span className="font-mono text-sm text-ink-300">{c.member_slug ?? `#${c.member_id}`}</span>
                <span className="flex items-center gap-4">
                  <span className="font-mono text-[11px] uppercase tracking-wider text-oxblood-300">
                    {c.kind}
                  </span>
                  <PenButton
                    disabled={busy !== null}
                    onClick={() =>
                      run(`pc${c.id}`, () => api.resolveConsequence(c.id), () => consequences.reload())
                    }
                  >
                    {busy === `pc${c.id}` ? '…' : 'Resolve'}
                  </PenButton>
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {(members.error || strikes.error || consequences.error) && (
        <ErrorNote message={members.error || strikes.error || consequences.error || ''} />
      )}
    </div>
  )
}
