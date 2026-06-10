import { useState } from 'react'
import { api } from '../lib/api'
import { useSemester } from '../lib/SemesterContext'
import { useAsync } from '../lib/useAsync'
import type { RosterIngestResult } from '../lib/types'
import { ErrorNote } from '../components/ui'
import { LedgerField, LedgerSection, PenButton } from '../components/ledger'

export function Roster() {
  const { current } = useSemester()
  const { data: members, loading, error, reload } = useAsync(() => api.listMembers(), [])
  const [semester, setSemester] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [result, setResult] = useState<RosterIngestResult | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const targetSemester = semester.trim() || current?.name || ''

  async function ingest(dryRun: boolean) {
    if (!file || !targetSemester) return
    setBusy(true)
    setErr(null)
    setResult(null)
    try {
      const r = await api.ingestRoster(file, targetSemester, dryRun)
      setResult(r)
      if (!dryRun) reload()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-10">
      <h1 className="text-3xl font-medium tracking-tight text-ink-100">Roster</h1>

      <LedgerSection title="Enroll from Google Form" hint="CSV import">
        <div className="space-y-4 px-4 py-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <LedgerField
              label="Target term"
              value={semester}
              onChange={setSemester}
              placeholder={current?.name ?? 'FA26'}
            />
            <label className="block">
              <span className="mb-1 block font-mono text-[10px] uppercase tracking-[0.18em] text-ink-500">
                CSV file
              </span>
              <input
                type="file"
                accept=".csv,text/csv"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                className="block w-full py-1 text-sm text-ink-300 file:mr-3 file:rounded-[2px] file:border-0 file:bg-brass-500 file:px-3 file:py-1.5 file:font-mono file:text-[11px] file:font-medium file:uppercase file:tracking-wider file:text-char-950 hover:file:bg-brass-400"
              />
            </label>
          </div>
          <div className="flex gap-3">
            <button
              disabled={!file || !targetSemester || busy}
              onClick={() => ingest(true)}
              className="font-mono text-[11px] uppercase tracking-[0.18em] text-ink-500 transition-colors hover:text-ink-100 disabled:text-ink-700"
            >
              preview
            </button>
            <PenButton disabled={!file || !targetSemester || busy} onClick={() => ingest(false)}>
              {busy ? 'enrolling…' : 'enroll'}
            </PenButton>
          </div>
          {err && <ErrorNote message={err} />}
          {result && <IngestSummary result={result} />}
        </div>
      </LedgerSection>

      <LedgerSection title="The Roster" hint={`${members?.length ?? 0} brothers`}>
        {loading ? (
          <p className="px-4 py-8 text-sm italic text-ink-500">Loading the roster…</p>
        ) : error ? (
          <ErrorNote message={error} />
        ) : !members || members.length === 0 ? (
          <p className="px-4 py-8 text-center text-sm italic text-ink-500">
            No brothers enrolled — import a roster above.
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-ink-700/40 text-left font-mono text-[10px] uppercase tracking-[0.18em] text-ink-500">
                <th className="px-4 py-2 font-medium">Name</th>
                <th className="px-4 py-2 font-medium">Slug</th>
                <th className="px-4 py-2 font-medium">Grad</th>
                <th className="px-4 py-2 font-medium">PC</th>
                <th className="px-4 py-2 font-medium">Standing</th>
              </tr>
            </thead>
            <tbody>
              {members.map((m) => (
                <tr
                  key={m.id}
                  className="border-b border-ink-700/20 transition-colors last:border-0 hover:bg-char-850/40"
                >
                  <td className="px-4 py-2 text-ink-100">{m.display_name}</td>
                  <td className="px-4 py-2 font-mono text-xs text-ink-500">{m.slug}</td>
                  <td className="px-4 py-2 font-mono tabular-nums text-ink-300">
                    {m.class_year ?? '—'}
                  </td>
                  <td className="px-4 py-2 font-mono text-brass-400">{m.pledge_class ?? '—'}</td>
                  <td className="px-4 py-2 font-mono text-[11px] uppercase tracking-wider text-ink-500">
                    {m.status_slug}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </LedgerSection>
    </div>
  )
}

function IngestSummary({ result }: { result: RosterIngestResult }) {
  const p = result.preview
  return (
    <div className="border-l-2 border-brass-500/60 bg-char-900/50 px-4 py-3 font-mono text-sm">
      <div className="mb-2 text-brass-400">
        {result.dry_run ? 'Preview' : 'Enrolled'} · {p.semester} · base year {p.base_year}
      </div>
      <ul className="space-y-1 text-ink-300">
        <li>
          {p.total_rows} rows · {p.new_members} new · {p.existing_members} existing
        </li>
        <li>{p.exec_assignments} exec assignments</li>
        {result.inserted_members !== undefined && (
          <li className="text-ink-100">
            {result.inserted_members} enrolled · {result.exec_roles_set} exec roles set
          </li>
        )}
        {p.unmapped_rising_class.length > 0 && (
          <li className="text-oxblood-300">⚠ unmapped class: {p.unmapped_rising_class.join(', ')}</li>
        )}
        {p.unmapped_pledge_class.length > 0 && (
          <li className="text-oxblood-300">⚠ non-Greek PC: {p.unmapped_pledge_class.join(', ')}</li>
        )}
      </ul>
    </div>
  )
}
