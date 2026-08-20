import { NavLink, Outlet } from 'react-router-dom'
import { useSemester } from '../lib/SemesterContext'
import { WaxSeal } from './ledger'

const NAV = [
  { to: '/', label: 'Register', end: true },
  { to: '/events', label: 'Events' },
  // Events is where you author the schedule; Calendar is how you read it.
  // Adjacent so the pair reads as one idea.
  { to: '/calendar', label: 'Calendar' },
  { to: '/swaps', label: 'Swaps' },
  { to: '/strikes', label: 'Strikes' },
  { to: '/pledges', label: 'Pledges' },
  { to: '/roster', label: 'Roster' },
  { to: '/notes', label: 'Notes' },
]

export function Shell() {
  const { current, version } = useSemester()
  return (
    <div className="min-h-screen">
      {/* Ledger head — flat charcoal, brass hairline, off-grid wax seal. */}
      <header className="relative overflow-hidden border-b border-brass-500/30 bg-char-950">
        <div className="mx-auto flex max-w-6xl items-end justify-between px-6 pb-3 pt-5">
          <div>
            <div className="flex items-baseline gap-3">
              <h1 className="text-lg font-semibold uppercase tracking-[0.16em] text-ink-100">
                Duty Register
              </h1>
              <span className="font-mono text-xs uppercase tracking-[0.25em] text-brass-400">
                Kappa Sigma · ΦΑ
              </span>
            </div>
            <p className="mt-1 font-mono text-[11px] uppercase tracking-[0.2em] text-ink-500">
              {current ? (
                <>
                  {current.name} Term
                  {current.archived_at && <span className="ml-2 text-ink-700">· archived</span>}
                </>
              ) : (
                'no term set'
              )}
            </p>
          </div>
          {/* Wax seal bleeds off the top-right edge. */}
          <div className="absolute right-5 top-2">
            <WaxSeal />
          </div>
        </div>
      </header>

      {/* Nav — small-caps ledger tabs */}
      <nav className="border-b border-ink-700/40 bg-char-950">
        <div className="mx-auto flex max-w-6xl gap-6 px-6">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              className={({ isActive }) =>
                `-mb-px border-b py-3 font-mono text-xs uppercase tracking-[0.18em] transition-colors ${
                  isActive
                    ? 'border-brass-500 text-brass-300'
                    : 'border-transparent text-ink-500 hover:text-ink-300'
                }`
              }
            >
              {n.label}
            </NavLink>
          ))}
        </div>
      </nav>

      <main className="mx-auto max-w-6xl px-6 py-8">
        <Outlet />
      </main>

      <footer className="mx-auto max-w-6xl px-6 py-6 font-mono text-[11px] uppercase tracking-[0.18em] text-ink-700">
        risk register {version && `· v${version}`}
      </footer>
    </div>
  )
}
