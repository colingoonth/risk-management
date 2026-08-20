import { useState } from 'react'
import { LedgerSection, PenButton, SegmentedToggle } from '../components/ledger'
import { ErrorNote } from '../components/ui'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import type { ChairNote, NoteKind } from '../lib/types'

// Two kinds, and the distinction is load-bearing rather than cosmetic. A
// one-off is actioned once and closed. A standing rule applies to EVERY
// rebuild until it is retired — closing one means "stop applying this", not
// "done", so they are listed apart and worded apart. Merging them into one list
// is how a rule quietly stops being a rule.
const KIND_OPTIONS: { value: NoteKind; label: string }[] = [
  { value: 'one_off', label: 'One-off' },
  { value: 'standing', label: 'Standing rule' },
]

function NoteRow({
  note,
  busy,
  onClose,
  onReopen,
  onDelete,
}: {
  note: ChairNote
  busy: boolean
  onClose: (id: number, closedNote: string) => void
  onReopen: (id: number) => void
  onDelete: (id: number) => void
}) {
  const [closing, setClosing] = useState(false)
  const [closedNote, setClosedNote] = useState('')
  const open = note.closed_at === null

  return (
    <div className="border-b border-ink-700/40 px-4 py-3">
      <div className="flex items-start gap-3">
        <span
          className={`mt-1 shrink-0 font-mono text-[10px] uppercase tracking-[0.15em] ${
            note.author === 'claude' ? 'text-brass-400' : 'text-ink-500'
          }`}
        >
          {note.author === 'claude' ? 'ASK' : note.created_at.slice(5, 10)}
        </span>
        <div className="min-w-0 flex-1">
          <p className={`text-sm ${open ? 'text-ink-100' : 'text-ink-500 line-through'}`}>
            {note.body}
          </p>
          {note.closed_note && (
            // What was actually DONE, kept beside what was asked. This is the
            // half that answers "why am I on this crew" in November.
            <p className="mt-1 text-xs text-brass-400">→ {note.closed_note}</p>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {open ? (
            closing ? (
              <>
                <input
                  autoFocus
                  value={closedNote}
                  onChange={(e) => setClosedNote(e.target.value)}
                  placeholder="what did you do?"
                  className="w-52 border-b border-ink-700/50 bg-transparent px-1 py-1 text-xs text-ink-100 placeholder:text-ink-500/60 focus:border-brass-500 focus:outline-none"
                />
                <PenButton
                  disabled={busy}
                  onClick={() => {
                    onClose(note.id, closedNote)
                    setClosing(false)
                    setClosedNote('')
                  }}
                >
                  Save
                </PenButton>
              </>
            ) : (
              <button
                disabled={busy}
                onClick={() => setClosing(true)}
                className="font-mono text-[10px] uppercase tracking-[0.15em] text-ink-500 hover:text-brass-400 disabled:opacity-40"
              >
                {note.kind === 'standing' ? 'Retire' : 'Done'}
              </button>
            )
          ) : (
            <button
              disabled={busy}
              onClick={() => onReopen(note.id)}
              className="font-mono text-[10px] uppercase tracking-[0.15em] text-ink-500 hover:text-brass-400 disabled:opacity-40"
            >
              Reopen
            </button>
          )}
          <button
            disabled={busy}
            onClick={() => onDelete(note.id)}
            className="font-mono text-[10px] uppercase tracking-[0.15em] text-ink-600 hover:text-oxblood-300 disabled:opacity-40"
          >
            ✕
          </button>
        </div>
      </div>
    </div>
  )
}

export function Notes() {
  const notes = useAsync(() => api.listNotes(), [])
  const [draft, setDraft] = useState('')
  const [kind, setKind] = useState<NoteKind>('one_off')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function run(fn: () => Promise<unknown>) {
    setBusy(true)
    setErr(null)
    try {
      await fn()
      notes.reload()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const all = notes.data ?? []
  const standing = all.filter((n) => n.kind === 'standing' && n.closed_at === null)
  const oneOff = all.filter((n) => n.kind === 'one_off' && n.closed_at === null)
  const closed = all.filter((n) => n.closed_at !== null)

  const handlers = {
    busy,
    onClose: (id: number, closedNote: string) =>
      run(() => api.closeNote(id, closedNote.trim() || undefined)),
    onReopen: (id: number) => run(() => api.reopenNote(id)),
    onDelete: (id: number) => run(() => api.deleteNote(id)),
  }

  return (
    <div className="mx-auto max-w-4xl">
      <LedgerSection
        title="Risk Notes"
        hint="things the schedule can't work out on its own"
      >
        <div className="space-y-3 px-4 py-4">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Nico can't do Oct 10, family thing"
            rows={2}
            className="w-full resize-y border-b border-ink-700/50 bg-transparent px-1 py-1.5 text-sm text-ink-100 placeholder:text-ink-500/60 focus:border-brass-500 focus:outline-none"
          />
          <div className="flex items-center justify-between gap-4">
            <div className="w-72">
              <SegmentedToggle options={KIND_OPTIONS} value={kind} onChange={setKind} />
            </div>
            <PenButton
              disabled={busy || !draft.trim()}
              onClick={() =>
                run(async () => {
                  await api.createNote({ body: draft, kind })
                  setDraft('')
                })
              }
            >
              Add note
            </PenButton>
          </div>
          <p className="text-xs text-ink-500">
            A <span className="text-ink-300">one-off</span> gets actioned once. A{' '}
            <span className="text-ink-300">standing rule</span> applies to every rebuild
            until you retire it.
          </p>
        </div>
      </LedgerSection>

      {err && (
        <div className="px-4 py-3">
          <ErrorNote message={err} />
        </div>
      )}
      {notes.error && (
        <div className="px-4 py-3">
          <ErrorNote message={notes.error} />
        </div>
      )}

      {standing.length > 0 && (
        <LedgerSection title="Standing rules" hint={`${standing.length} in force`} band>
          {standing.map((n) => (
            <NoteRow key={n.id} note={n} {...handlers} />
          ))}
        </LedgerSection>
      )}

      <LedgerSection title="Open" hint={oneOff.length ? `${oneOff.length} to action` : undefined}>
        {oneOff.length === 0 ? (
          <p className="px-4 py-6 text-sm text-ink-500">
            Nothing outstanding. Add anything the schedule should know about.
          </p>
        ) : (
          oneOff.map((n) => <NoteRow key={n.id} note={n} {...handlers} />)
        )}
      </LedgerSection>

      {closed.length > 0 && (
        <LedgerSection title="Done" hint={`${closed.length}`}>
          {closed.map((n) => (
            <NoteRow key={n.id} note={n} {...handlers} />
          ))}
        </LedgerSection>
      )}
    </div>
  )
}
