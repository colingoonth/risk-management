import { useRef, useState } from 'react'
import { api } from '../lib/api'
import { useSemester } from '../lib/SemesterContext'
import { useAsync } from '../lib/useAsync'
import type { RosterIngestResult } from '../lib/types'
import { Button, Card, Empty, ErrorNote, SectionHeader, Spinner, Tag } from '../components/ui'

export function Roster() {
  const { current } = useSemester()
  const { data: members, loading, error, reload } = useAsync(() => api.listMembers(), [])
  const [semester, setSemester] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [result, setResult] = useState<RosterIngestResult | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

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
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">Roster</h1>

      <Card>
        <SectionHeader title="Import Google Form roster (CSV)" />
        <div className="space-y-4 px-5 py-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block">
              <span className="mb-1 block font-mono text-xs uppercase tracking-wider text-ink-500">
                Target semester
              </span>
              <input
                value={semester}
                onChange={(e) => setSemester(e.target.value)}
                placeholder={current?.name ?? 'FA26'}
                className="w-full rounded-[3px] border border-brass-500/25 bg-char-950 px-3 py-1.5 text-sm focus:border-brass-500/70 focus:outline-none"
              />
            </label>
            <label className="block">
              <span className="mb-1 block font-mono text-xs uppercase tracking-wider text-ink-500">
                CSV file
              </span>
              <input
                ref={inputRef}
                type="file"
                accept=".csv,text/csv"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                className="block w-full text-sm text-ink-300 file:mr-3 file:rounded-[3px] file:border-0 file:bg-brass-500 file:px-3 file:py-1.5 file:font-medium file:uppercase file:tracking-wider file:text-char-950 hover:file:bg-brass-400"
              />
            </label>
          </div>
          <div className="flex gap-2">
            <Button variant="ghost" disabled={!file || !targetSemester || busy} onClick={() => ingest(true)}>
              Preview
            </Button>
            <Button variant="primary" disabled={!file || !targetSemester || busy} onClick={() => ingest(false)}>
              {busy ? 'Importing…' : 'Import'}
            </Button>
          </div>
          {err && <ErrorNote message={err} />}
          {result && <IngestSummary result={result} />}
        </div>
      </Card>

      <Card>
        <SectionHeader
          title="Members"
          right={<Tag>{members?.length ?? 0} total</Tag>}
        />
        {loading ? (
          <Spinner label="Loading members" />
        ) : error ? (
          <ErrorNote message={error} />
        ) : !members || members.length === 0 ? (
          <Empty>No members yet — import a roster above.</Empty>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-brass-500/20 text-left font-mono text-xs uppercase tracking-wider text-ink-500">
                <th className="px-5 py-2 font-medium">Name</th>
                <th className="px-5 py-2 font-medium">Slug</th>
                <th className="px-5 py-2 font-medium">Grad</th>
                <th className="px-5 py-2 font-medium">PC</th>
                <th className="px-5 py-2 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {members.map((m) => (
                <tr key={m.id} className="border-b border-brass-500/5 last:border-0">
                  <td className="px-5 py-2">{m.display_name}</td>
                  <td className="px-5 py-2 font-mono text-ink-300">{m.slug}</td>
                  <td className="px-5 py-2 font-mono">{m.class_year ?? '—'}</td>
                  <td className="px-5 py-2 font-mono text-brass-300">{m.pledge_class ?? '—'}</td>
                  <td className="px-5 py-2">
                    <Tag tone={m.status_slug === 'active' ? 'filled' : 'neutral'}>{m.status_slug}</Tag>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  )
}

function IngestSummary({ result }: { result: RosterIngestResult }) {
  const p = result.preview
  return (
    <div className="rounded-[3px] border border-brass-500/25 bg-char-850 px-4 py-3 font-mono text-sm">
      <div className="mb-2 text-brass-300">
        {result.dry_run ? 'Preview' : 'Imported'} · {p.semester} · base year {p.base_year}
      </div>
      <ul className="space-y-1 text-ink-300">
        <li>{p.total_rows} rows · {p.new_members} new · {p.existing_members} existing</li>
        <li>{p.exec_assignments} exec assignments</li>
        {result.inserted_members !== undefined && (
          <li className="text-signal-filled">
            {result.inserted_members} members inserted · {result.exec_roles_set} exec roles set
          </li>
        )}
        {p.unmapped_rising_class.length > 0 && (
          <li className="text-signal-warn">
            ⚠ unmapped class: {p.unmapped_rising_class.join(', ')}
          </li>
        )}
        {p.unmapped_pledge_class.length > 0 && (
          <li className="text-signal-warn">
            ⚠ non-Greek PC: {p.unmapped_pledge_class.join(', ')}
          </li>
        )}
      </ul>
    </div>
  )
}
