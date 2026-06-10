import type { ButtonHTMLAttributes, ReactNode } from 'react'

// --- Card ---------------------------------------------------------------

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <section
      className={`rounded-card border border-brass-500/20 bg-char-900 ${className}`}
    >
      {children}
    </section>
  )
}

export function SectionHeader({ title, right }: { title: string; right?: ReactNode }) {
  return (
    <header className="flex items-center justify-between border-b border-brass-500/20 px-5 py-3">
      <h2 className="crest-tick text-sm font-medium uppercase tracking-[0.18em] text-ink-300">
        {title}
      </h2>
      {right}
    </header>
  )
}

// --- Button -------------------------------------------------------------

type Variant = 'primary' | 'ghost' | 'danger'

const VARIANTS: Record<Variant, string> = {
  primary:
    'bg-brass-500 text-char-950 hover:bg-brass-400 disabled:bg-brass-600/40 disabled:text-ink-500',
  ghost:
    'border border-brass-500/30 text-ink-100 hover:border-brass-500/70 hover:bg-char-800 disabled:opacity-40',
  danger:
    'bg-oxblood-600 text-ink-100 hover:bg-oxblood-500 disabled:opacity-40',
}

export function Button({
  variant = 'primary',
  className = '',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  return (
    <button
      className={`inline-flex items-center gap-1.5 rounded-[3px] px-3 py-1.5 text-sm font-medium uppercase tracking-wider transition-colors disabled:cursor-not-allowed ${VARIANTS[variant]} ${className}`}
      {...props}
    />
  )
}

// --- Status / pills -----------------------------------------------------

export function Tag({
  tone = 'neutral',
  children,
}: {
  tone?: 'open' | 'filled' | 'warn' | 'neutral'
  children: ReactNode
}) {
  const tones = {
    open: 'bg-signal-open/15 text-signal-open border-signal-open/30',
    filled: 'bg-signal-filled/15 text-signal-filled border-signal-filled/30',
    warn: 'bg-signal-warn/15 text-signal-warn border-signal-warn/30',
    neutral: 'bg-char-800 text-ink-300 border-brass-500/20',
  }
  return (
    <span
      className={`inline-flex items-center rounded-[3px] border px-2 py-0.5 font-mono text-xs ${tones[tone]}`}
    >
      {children}
    </span>
  )
}

// --- States -------------------------------------------------------------

export function Spinner({ label = 'Loading' }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 px-5 py-8 text-sm text-ink-500">
      <span className="h-2 w-2 animate-pulse rounded-full bg-brass-500" />
      {label}…
    </div>
  )
}

export function ErrorNote({ message }: { message: string }) {
  return (
    <div className="m-5 rounded-[3px] border border-oxblood-500/50 bg-oxblood-700/30 px-4 py-3 text-sm text-oxblood-300">
      {message}
    </div>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="px-5 py-8 text-sm text-ink-500">{children}</div>
}
