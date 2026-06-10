import type { ButtonHTMLAttributes, ReactNode } from 'react'

// Duty Ledger primitives: status encoded WITHOUT pills — fill bars, tally
// marks, glyph+label, and the one wax-seal signature.

// A ruled ledger region: caption + hint + optional action, hairline underneath.
// Replaces the old bordered Card across the app.
export function LedgerSection({
  title,
  hint,
  action,
  band,
  children,
}: {
  title: string
  hint?: string
  action?: ReactNode
  band?: boolean
  children: ReactNode
}) {
  return (
    <section className={band ? 'bg-brass-300/25' : undefined}>
      <div className="flex items-baseline justify-between border-b border-ink-700/40 px-4 py-2.5">
        <div className="flex items-baseline gap-3">
          <h2 className="ledger-cap">{title}</h2>
          {hint && <span className="text-xs text-ink-500">{hint}</span>}
        </div>
        {action}
      </div>
      {children}
    </section>
  )
}

// A ledger-styled text field (label above, hairline box).
export function LedgerField({
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
      <span className="mb-1 block font-mono text-[10px] uppercase tracking-[0.18em] text-ink-500">
        {label}
      </span>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full border-b border-ink-700/50 bg-transparent px-1 py-1.5 text-sm text-ink-100 placeholder:text-ink-500/60 focus:border-brass-500 focus:outline-none"
      />
    </label>
  )
}

// Rust primary action — "the pen".
export function PenButton({
  children,
  className = '',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      className={`inline-flex shrink-0 items-center rounded-[2px] bg-brass-500 px-3 py-1.5 font-mono text-[11px] font-medium uppercase tracking-[0.15em] text-char-950 transition-colors hover:bg-brass-400 disabled:bg-brass-600/40 disabled:text-ink-500 ${className}`}
      {...props}
    >
      {children}
    </button>
  )
}

const MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']

// Big mono day-numeral over a small month — the ledger's date column.
export function DayStamp({ iso }: { iso: string }) {
  const [, m, d] = iso.split('-')
  return (
    <span className="block w-12 shrink-0 text-center font-mono leading-none">
      <span className="block text-lg tabular-nums text-ink-100">{d ?? '--'}</span>
      <span className="block text-[10px] tracking-widest text-ink-500">
        {MONTHS[(Number(m) || 1) - 1] ?? '---'}
      </span>
    </span>
  )
}

export function FillBar({ filled, total }: { filled: number; total: number }) {
  const segs = Array.from({ length: Math.max(total, 1) }, (_, i) => i < filled)
  return (
    <span className="inline-flex items-center gap-2">
      <span className="flex gap-px" aria-hidden>
        {segs.map((on, i) => (
          <span
            key={i}
            className={`h-3 w-1.5 ${on ? 'bg-ink-300' : 'bg-oxblood-600'}`}
          />
        ))}
      </span>
      <span className="font-mono text-xs text-ink-300 tabular-nums">
        {filled}/{total}
      </span>
    </span>
  )
}

// Hand tally: groups of five (卌) + remainder strokes. Sparse rows read as guilt.
export function Tally({ count }: { count: number }) {
  if (count <= 0) return <span className="font-mono text-ink-700">·</span>
  const fives = Math.floor(count / 5)
  const rem = count % 5
  return (
    <span className="font-mono text-ink-300/90" aria-label={`${count} shifts`}>
      {'卌 '.repeat(fives)}
      {'丨'.repeat(rem)}
    </span>
  )
}

type State = 'unfilled' | 'overdue' | 'posted' | 'swap'

const GLYPHS: Record<State, { glyph: string; label: string; cls: string }> = {
  unfilled: { glyph: '✕', label: 'UNFILLED', cls: 'text-oxblood-300' },
  overdue: { glyph: '⚑', label: 'OVERDUE', cls: 'text-oxblood-300' },
  posted: { glyph: '✓', label: 'POSTED', cls: 'text-ink-500' },
  swap: { glyph: '⇄', label: 'SWAP', cls: 'text-brass-400' },
}

export function StatusGlyph({ state }: { state: State }) {
  const s = GLYPHS[state]
  return (
    <span className={`inline-flex items-center gap-1.5 font-mono text-xs tracking-wide ${s.cls}`}>
      <span aria-hidden>{s.glyph}</span>
      {s.label}
    </span>
  )
}

// The single heritage anchor — a wax-seal crest, off-grid, used exactly once.
export function WaxSeal() {
  return (
    <div
      className="pointer-events-none select-none"
      style={{ transform: 'rotate(7deg)' }}
      aria-hidden
    >
      <div className="flex h-16 w-16 items-center justify-center rounded-full border-2 border-oxblood-500/70 bg-oxblood-600 shadow-[inset_0_0_14px_rgba(80,30,10,0.55)]">
        <div className="flex h-12 w-12 items-center justify-center rounded-full border border-oxblood-300/50">
          <span className="font-mono text-base font-medium tracking-tighter text-brass-300">ΚΣ</span>
        </div>
      </div>
    </div>
  )
}
