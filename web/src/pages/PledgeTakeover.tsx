import { useMemo, useState } from 'react'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { useSemester } from '../lib/SemesterContext'
import { ErrorNote } from '../components/ui'
import { LedgerSection, SegmentedToggle } from '../components/ledger'

// Locked closed set; the resolver (services/pledge_mode.py) hard-codes these slugs.
// Ordered as the degradation ladder: pledges-heavy → brothers-only.
type Mode = 'pledge_takeover_full' | 'pledge_takeover_partial' | 'normal'
const MODES: { value: Mode; label: string }[] = [
  { value: 'pledge_takeover_full', label: 'Pledges only' },
  { value: 'pledge_takeover_partial', label: 'Mixed' },
  { value: 'normal', label: 'Brothers only' },
]

export function PledgeTakeover() {
  const { current, loading: semLoading } = useSemester()
  const semName = current?.name

  const houses = useAsync(() => api.listHouses(), [])
  const houseModes = useAsync(
    () => (semName ? api.listHouseModes(semName) : Promise.resolve([])),
    [semName],
  )

  const [busy, setBusy] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  const modeMap = useMemo(
    () => new Map((houseModes.data ?? []).map((m) => [m.house_slug, m.pledge_mode_slug as Mode])),
    [houseModes.data],
  )

  async function setMode(houseSlug: string, mode: Mode) {
    if (!semName) return
    setBusy(houseSlug)
    setNote(null)
    try {
      await api.setHouseMode(semName, houseSlug, mode)
      houseModes.reload()
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  if (!semName) {
    return semLoading ? (
      <p className="px-1 py-10 font-mono text-xs text-ink-700">loading term…</p>
    ) : (
      <p className="px-1 py-10 text-center text-sm italic text-ink-500">
        No active term set. Set a current semester to configure pledge takeover.
      </p>
    )
  }

  return (
    <div>
      {note && <ErrorNote message={note} />}

      {/* ── HOW IT WORKS — the honest explainer ──────────────────── */}
      <LedgerSection title="How takeover resolves" hint="configured target, not a guarantee" band>
        <div className="space-y-3 px-4 py-3 text-sm text-ink-300">
          <div className="flex items-center gap-2 font-mono text-[11px] uppercase tracking-[0.14em]">
            <span className="text-ink-100">Pledges only</span>
            <span className="text-ink-500">→ falls to →</span>
            <span className="text-ink-100">Mixed</span>
            <span className="text-ink-500">→ falls to →</span>
            <span className="text-ink-100">Brothers only</span>
          </div>
          <p className="text-ink-500">
            You set a <span className="text-ink-300">target</span> per host house. Actual coverage
            is decided <span className="text-ink-300">per event</span> by pledge supply: a
            <span className="text-ink-300"> Pledges-only</span> event with too few eligible pledges
            silently falls back to <span className="text-ink-300">Mixed</span>, and to
            <span className="text-oxblood-300"> Brothers-only</span> if no pledges are eligible.
          </p>
          <p className="font-mono text-[11px] text-ink-500">
            e.g. target Pledges-only · 8 slots · 5 eligible pledges → resolves to Mixed (pledges 5,
            brothers 3).
          </p>
          <p className="text-[13px] text-ink-500">
            Applies <span className="text-ink-300">only to events hosted by that house</span>.
            Off-site / no-host events always use Brothers-only regardless of these settings.
          </p>
        </div>
      </LedgerSection>

      {/* ── PER-HOUSE TARGETS ────────────────────────────────────── */}
      <section className="mt-8">
        <div className="flex items-baseline justify-between border-b border-ink-700/40 px-1 py-2.5">
          <div className="flex items-baseline gap-3">
            <h2 className="ledger-cap">Host House Targets</h2>
            <span className="text-xs text-ink-500">{semName} term</span>
          </div>
          <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-500">
            configured target
          </span>
        </div>

        {houses.loading ? (
          <p className="px-1 py-6 font-mono text-xs text-ink-700">loading houses…</p>
        ) : (houses.data?.length ?? 0) === 0 ? (
          <p className="px-1 py-6 text-sm italic text-ink-500">No host houses on record.</p>
        ) : (
          <ul>
            {houses.data!.map((h) => {
              const current = modeMap.get(h.slug) ?? null // null = unset (defaults to normal)
              return (
                <li
                  key={h.slug}
                  className="flex flex-col gap-2 border-b border-ink-700/25 py-3 pl-1 pr-2 sm:flex-row sm:items-center sm:gap-4"
                >
                  <div className="min-w-0 sm:flex-1">
                    <div className="truncate text-ink-100">{h.display_name}</div>
                    {current === null && (
                      <div className="font-mono text-[10px] uppercase tracking-wider text-ink-500">
                        not set · defaults to brothers-only
                      </div>
                    )}
                  </div>
                  <div className="sm:w-80">
                    <SegmentedToggle<Mode>
                      value={current}
                      onChange={(m) => setMode(h.slug, m)}
                      disabled={busy !== null}
                      options={MODES}
                    />
                  </div>
                </li>
              )
            })}
          </ul>
        )}
      </section>

      {(houses.error || houseModes.error) && (
        <ErrorNote message={houses.error || houseModes.error || ''} />
      )}
    </div>
  )
}
