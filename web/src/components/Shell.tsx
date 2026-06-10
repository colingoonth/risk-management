import { NavLink, Outlet } from 'react-router-dom'
import { useSemester } from '../lib/SemesterContext'

const NAV = [
  { to: '/', label: 'Dashboard', end: true },
  { to: '/events', label: 'Events' },
  { to: '/roster', label: 'Roster' },
]

export function Shell() {
  const { current, version } = useSemester()
  return (
    <div className="min-h-screen">
      {/* Oxblood identity band */}
      <header className="border-b border-brass-500/30 bg-gradient-to-r from-oxblood-700 to-oxblood-600">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-3">
          <div className="flex items-baseline gap-3">
            <span className="text-brass-300">◆</span>
            <span className="text-lg font-semibold uppercase tracking-[0.2em] text-ink-100">
              Kappa Sigma
            </span>
            <span className="font-mono text-sm uppercase tracking-[0.3em] text-brass-300">
              Risk
            </span>
          </div>
          <div className="font-mono text-xs text-ink-100/80">
            {current ? (
              <span>
                <span className="text-brass-300">{current.name}</span>
                {current.archived_at && <span className="ml-2 text-ink-100/60">(archived)</span>}
              </span>
            ) : (
              <span className="text-ink-100/60">no current semester</span>
            )}
          </div>
        </div>
      </header>

      {/* Nav rail */}
      <nav className="border-b border-brass-500/20 bg-char-900">
        <div className="mx-auto flex max-w-6xl gap-1 px-6">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              className={({ isActive }) =>
                `border-b-2 px-4 py-3 text-sm font-medium uppercase tracking-wider transition-colors ${
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

      <footer className="mx-auto max-w-6xl px-6 py-6 font-mono text-xs text-ink-500">
        risk-management {version && `· v${version}`}
      </footer>
    </div>
  )
}
