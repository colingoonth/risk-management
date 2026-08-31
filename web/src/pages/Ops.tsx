import { useState } from 'react'
import { LedgerDisclosure, PenButton } from '../components/ledger'
import { api } from '../lib/api'
import { addDays, mondayOf, todayLocal, weekdayMon0 } from '../lib/dates'
import type { GroupMeInboundMessage } from '../lib/types'
import { useAsync } from '../lib/useAsync'

const TODAY = todayLocal()
const TEXT_ACTION =
  'font-mono text-[11px] uppercase tracking-[0.16em] text-brass-400 transition-colors hover:text-brass-600 disabled:text-ink-700'
const QUIET_ACTION =
  'font-mono text-[11px] uppercase tracking-[0.16em] text-ink-500 transition-colors hover:text-ink-100 disabled:text-ink-700'

function sundayPostWindow(today = TODAY) {
  // On Sunday the chair is approving the week ahead. During the week, keep the
  // page anchored to the current Monday so a reload does not jump past crews
  // that are still working.
  const on_or_after = weekdayMon0(today) === 6 ? addDays(today, 1) : mondayOf(today)
  return { on_or_after, on_or_before: addDays(on_or_after, 6) }
}

const WINDOW = sundayPostWindow()

export function Ops() {
  const health = useAsync(() => api.groupmeHealth(), [])
  const inbound = useAsync(() => api.groupmeInbound({ limit: 50 }), [])
  const identities = useAsync(() => api.groupmeIdentities(), [])
  const membership = useAsync(
    () => api.groupmeMembershipPlan(WINDOW.on_or_after, WINDOW.on_or_before),
    [],
  )
  const preview = useAsync(
    () => api.groupmeAnnouncePreview(WINDOW.on_or_after, WINDOW.on_or_before),
    [],
  )

  const [busy, setBusy] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [actionNote, setActionNote] = useState<string | null>(null)
  const [confirmAnnouncement, setConfirmAnnouncement] = useState(false)
  const [confirmMembership, setConfirmMembership] = useState(false)
  const [replyTarget, setReplyTarget] = useState<{
    groupSlug: string
    senderName: string
  } | null>(null)
  const [replyText, setReplyText] = useState('')
  const [confirmReply, setConfirmReply] = useState(false)

  async function run(
    key: string,
    fn: () => Promise<unknown>,
    after?: () => void,
  ) {
    setBusy(key)
    setActionError(null)
    setActionNote(null)
    try {
      await fn()
      after?.()
    } catch (error) {
      setActionError(error instanceof Error ? error.message : String(error))
    } finally {
      setBusy(null)
    }
  }

  const messages = inbound.data ?? []
  const urgentMessages = messages.filter((message) => message.triage === 'urgent')
  const posts = preview.data?.posts ?? []
  const unlinked = identities.data?.unlinked ?? []
  const identityDrift = identities.data?.drift ?? []
  const additions = membership.data?.add ?? []
  const removals = membership.data?.remove ?? []
  const membershipPending = identityDrift.length + additions.length + removals.length
  const membershipAlarm = membershipPending > 0
  const staleGroups = health.data?.groups.filter(
    (group) => group.stale || group.consecutive_failures > 0,
  ) ?? []
  const forwarderAlarm = Boolean(
    health.error || (health.data && (!health.data.healthy || staleGroups.length > 0)),
  )
  const coverageAlarm = unlinked.length > 0
  const strikesAlarm = urgentMessages.length > 0
  const feedGroups = summarizeFeed(messages)

  return (
    <div className="mx-auto max-w-5xl">
      <header className="flex flex-col gap-2 border-b border-ink-700/50 pb-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-lg font-semibold uppercase tracking-[0.16em] text-ink-100">
            Operations Ledger
          </h1>
          <p className="mt-1 text-sm text-ink-500">
            Group communications, crew coverage, and entries awaiting the chair.
          </p>
        </div>
        <span className="font-mono text-[11px] uppercase tracking-[0.16em] text-ink-500 tabular-nums">
          Week {WINDOW.on_or_after} → {WINDOW.on_or_before}
        </span>
      </header>

      {(actionError || actionNote) && (
        <div
          aria-live="polite"
          className={`border-b px-1 py-3 text-sm ${
            actionError
              ? 'border-oxblood-600/60 text-oxblood-300'
              : 'border-brass-500/40 text-brass-600'
          }`}
        >
          {actionError ?? actionNote}
        </div>
      )}

      <LedgerDisclosure
        title="Forwarder health"
        summary={
          health.loading
            ? 'checking…'
            : health.error
              ? 'unavailable'
              : forwarderAlarm
                ? `${staleGroups.length || 1} stale`
                : 'nominal'
        }
        defaultOpen
        alarm={forwarderAlarm}
      >
        <div className="px-1 pb-4">
          <DataState loading={health.loading} error={health.error} />
          {health.data && (
            <>
              <div className="flex items-center justify-between gap-4 border-b border-ink-700/25 py-3">
                <span className="text-sm text-ink-500">
                  Heartbeat {formatAge(health.data.heartbeat_age_seconds)}
                </span>
                <PenButton
                  disabled={busy !== null}
                  onClick={() =>
                    run('poll', () => api.groupmePoll(), () => {
                      health.reload()
                      inbound.reload()
                      setActionNote('Poll cycle completed.')
                    })
                  }
                >
                  {busy === 'poll' ? 'Polling…' : 'Poll now'}
                </PenButton>
              </div>
              <ul>
                {health.data.groups.map((group) => {
                  const alarm = group.stale || group.consecutive_failures > 0
                  return (
                    <li
                      key={group.slug}
                      className="grid gap-1 border-b border-ink-700/20 py-3 sm:grid-cols-[minmax(0,1fr)_auto_auto] sm:gap-5"
                    >
                      <span className={alarm ? 'text-oxblood-300' : 'text-ink-100'}>
                        {group.label}
                      </span>
                      <span className="font-mono text-xs text-ink-500 tabular-nums">
                        last ok {formatMoment(group.last_ok_at)}
                      </span>
                      <span
                        className={`font-mono text-xs tabular-nums ${
                          alarm ? 'text-oxblood-300' : 'text-ink-500'
                        }`}
                      >
                        {group.consecutive_failures} failures
                      </span>
                      {group.last_error && (
                        <span className="text-sm text-oxblood-300 sm:col-span-3">
                          {group.last_error}
                        </span>
                      )}
                    </li>
                  )
                })}
              </ul>
            </>
          )}
        </div>
      </LedgerDisclosure>

      <LedgerDisclosure
        title="Inbound feed"
        summary={inbound.loading ? 'loading…' : `${messages.length} received`}
        defaultOpen={false}
        alarm={false}
      >
        <div className="px-1 pb-4">
          <DataState loading={inbound.loading} error={inbound.error} />
          {!inbound.loading && !inbound.error && feedGroups.length === 0 && (
            <EmptyLine>No inbound entries in the current feed.</EmptyLine>
          )}
          <ul>
            {feedGroups.map((group) => (
              <li
                key={group.slug}
                className="grid grid-cols-[minmax(0,1fr)_auto] gap-4 border-b border-ink-700/20 py-3"
              >
                <span className="text-sm text-ink-100">{group.slug}</span>
                <span className="font-mono text-xs text-ink-500 tabular-nums">
                  {group.count} · latest {formatMoment(group.latest)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      </LedgerDisclosure>

      <LedgerDisclosure
        title="Sunday post preview + approve"
        summary={preview.loading ? 'building…' : `${posts.length} posts`}
        defaultOpen
        alarm={false}
      >
        <div className="px-1 pb-4">
          <DataState loading={preview.loading} error={preview.error} />
          {!preview.loading && !preview.error && posts.length === 0 && (
            <EmptyLine>No routable crew posts in this window.</EmptyLine>
          )}
          <ul>
            {posts.map((post) => (
              <li key={`${post.group_slug}-${post.event_date}-${post.event_name}`} className="py-3">
                <div className="flex items-baseline justify-between gap-4 border-b border-ink-700/20 pb-2">
                  <span className="text-sm font-semibold text-ink-100">{post.event_name}</span>
                  <span className="font-mono text-xs text-ink-500 tabular-nums">
                    {post.label} · {post.event_date}
                  </span>
                </div>
                <pre className="whitespace-pre-wrap py-3 font-mono text-xs leading-6 text-ink-300">
                  {post.text}
                </pre>
              </li>
            ))}
          </ul>
          {posts.length > 0 && !confirmAnnouncement && (
            <div className="flex justify-end border-t border-ink-700/30 pt-3">
              <button
                type="button"
                className={TEXT_ACTION}
                disabled={busy !== null}
                onClick={() => setConfirmAnnouncement(true)}
              >
                Review approval →
              </button>
            </div>
          )}
          {confirmAnnouncement && (
            <ConfirmRow
              message={`Post ${posts.length} crew ${plural(posts.length, 'notice')} to GroupMe?`}
              busy={busy === 'announce'}
              confirmLabel="Confirm & post"
              onCancel={() => setConfirmAnnouncement(false)}
              onConfirm={() =>
                run(
                  'announce',
                  () =>
                    api.groupmeAnnounce({
                      ...WINDOW,
                      confirm: true,
                    }),
                  () => {
                    setConfirmAnnouncement(false)
                    preview.reload()
                    setActionNote('Crew announcements posted.')
                  },
                )
              }
            />
          )}
        </div>
      </LedgerDisclosure>

      <LedgerDisclosure
        title="Membership drift"
        summary={
          identities.loading || membership.loading
            ? 'checking…'
            : membershipAlarm
              ? `${membershipPending} pending`
              : 'aligned'
        }
        defaultOpen={false}
        alarm={membershipAlarm}
      >
        <div className="px-1 pb-4">
          <DataState loading={identities.loading || membership.loading} error={identities.error ?? membership.error} />
          {identities.data && membership.data && (
            <>
              <p className="border-b border-ink-700/20 py-3 text-sm text-ink-500">
                <span className="font-mono tabular-nums text-ink-300">{identities.data.linked}</span>{' '}
                identities linked · <span className="font-mono tabular-nums">{additions.length}</span>{' '}
                to add · <span className="font-mono tabular-nums">{removals.length}</span> to remove
              </p>
              <ul>
                {identityDrift.map((item) => (
                  <li
                    key={item.groupme_user_id}
                    className="flex items-baseline justify-between gap-4 border-b border-ink-700/20 py-3"
                  >
                    <span className="text-sm text-oxblood-300">Nickname drift</span>
                    <span className="truncate text-sm text-ink-300">{item.nickname}</span>
                  </li>
                ))}
                {additions.map((item) => (
                  <li
                    key={`add-${item.member_id}`}
                    className="flex items-baseline justify-between gap-4 border-b border-ink-700/20 py-3"
                  >
                    <span className="text-sm text-ink-100">{item.display_name}</span>
                    <span className="font-mono text-xs uppercase tracking-wider text-oxblood-300">
                      add to parent
                    </span>
                  </li>
                ))}
                {removals.map((item) => (
                  <li
                    key={`remove-${item.member_id}`}
                    className="grid gap-1 border-b border-ink-700/20 py-3 sm:grid-cols-[minmax(0,1fr)_auto]"
                  >
                    <span className="text-sm text-ink-100">{item.display_name}</span>
                    <span className="font-mono text-xs uppercase tracking-wider text-oxblood-300">
                      remove after shifts
                    </span>
                    <span className="text-xs text-ink-500 sm:col-span-2">{item.reason}</span>
                  </li>
                ))}
              </ul>
              {additions.length + removals.length > 0 && !confirmMembership && (
                <div className="flex justify-end pt-3">
                  <button
                    type="button"
                    className={TEXT_ACTION}
                    disabled={busy !== null}
                    onClick={() => setConfirmMembership(true)}
                  >
                    Review membership changes →
                  </button>
                </div>
              )}
              {confirmMembership && (
                <ConfirmRow
                  message={`Apply ${additions.length} ${plural(additions.length, 'addition')} and ${removals.length} ${plural(removals.length, 'removal')}?`}
                  busy={busy === 'membership'}
                  confirmLabel="Confirm membership"
                  onCancel={() => setConfirmMembership(false)}
                  onConfirm={() =>
                    run(
                      'membership',
                      () => api.groupmeMembershipApply({ ...WINDOW, confirm: true }),
                      () => {
                        setConfirmMembership(false)
                        membership.reload()
                        identities.reload()
                        setActionNote('Membership changes applied.')
                      },
                    )
                  }
                />
              )}
            </>
          )}
        </div>
      </LedgerDisclosure>

      <LedgerDisclosure
        title="This week's crews"
        summary={preview.loading ? 'loading…' : `${posts.length} crews`}
        defaultOpen
        alarm={false}
      >
        <div className="px-1 pb-4">
          <DataState loading={preview.loading} error={preview.error} />
          {!preview.loading && !preview.error && posts.length === 0 && (
            <EmptyLine>No crews scheduled in this window.</EmptyLine>
          )}
          <ul>
            {posts.map((post) => (
              <li
                key={`crew-${post.group_slug}-${post.event_date}-${post.event_name}`}
                className="grid gap-2 border-b border-ink-700/20 py-3 sm:grid-cols-[8rem_minmax(0,1fr)]"
              >
                <span className="font-mono text-xs text-ink-500 tabular-nums">
                  {post.event_date}
                </span>
                <div>
                  <p className="text-sm font-semibold text-ink-100">{post.event_name}</p>
                  <div className="mt-1 font-mono text-xs leading-5 text-ink-300">
                    {post.text.split('\n').slice(1).map((line, index) => (
                      <div key={`${line}-${index}`}>{line}</div>
                    ))}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </div>
      </LedgerDisclosure>

      <LedgerDisclosure
        title="Coverage alarm"
        summary={
          identities.loading
            ? 'checking…'
            : coverageAlarm
              ? `${unlinked.length} unlinked`
              : 'complete'
        }
        defaultOpen={false}
        alarm={coverageAlarm}
      >
        <div className="px-1 pb-4">
          <DataState loading={identities.loading} error={identities.error} />
          {identities.data && unlinked.length === 0 && (
            <EmptyLine>Every roster identity can be mentioned in a crew post.</EmptyLine>
          )}
          {unlinked.length > 0 && (
            <>
              <p className="border-b border-ink-700/20 py-3 text-sm text-oxblood-300">
                These roster entries cannot be mentioned in GroupMe until an identity is linked.
              </p>
              <ul>
                {unlinked.map((item) => (
                  <li
                    key={item.member_id}
                    className="flex items-baseline justify-between gap-4 border-b border-ink-700/20 py-3"
                  >
                    <span className="text-sm text-ink-100">{item.display_name}</span>
                    <span className="font-mono text-xs uppercase tracking-wider text-oxblood-300">
                      mention missing
                    </span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      </LedgerDisclosure>

      <LedgerDisclosure
        title="Strikes owed"
        summary={inbound.loading ? 'checking…' : `${urgentMessages.length} owed`}
        defaultOpen={false}
        alarm={strikesAlarm}
      >
        <div className="px-1 pb-4">
          <DataState loading={inbound.loading} error={inbound.error} />
          {!inbound.loading && !inbound.error && urgentMessages.length === 0 && (
            <EmptyLine>No inbound entries are marked for urgent strike follow-up.</EmptyLine>
          )}
          <MessageRows messages={urgentMessages} />
        </div>
      </LedgerDisclosure>

      <LedgerDisclosure
        title="Unified inbox"
        summary={inbound.loading ? 'loading…' : `${messages.length} entries`}
        defaultOpen={false}
        alarm={false}
      >
        <div className="px-1 pb-4">
          <DataState loading={inbound.loading} error={inbound.error} />
          {!inbound.loading && !inbound.error && messages.length === 0 && (
            <EmptyLine>The inbox is clear.</EmptyLine>
          )}
          <MessageRows
            messages={messages}
            onReply={(message) => {
              setReplyTarget({
                groupSlug: message.group_slug,
                senderName: message.sender_name,
              })
              setReplyText('')
              setConfirmReply(false)
            }}
          />
          {replyTarget && (
            <div className="border-t border-ink-700/40 py-4">
              <p className="font-mono text-[11px] uppercase tracking-[0.16em] text-ink-500">
                Reply in {replyTarget.groupSlug} after {replyTarget.senderName}
              </p>
              <textarea
                value={replyText}
                rows={3}
                onChange={(event) => {
                  setReplyText(event.target.value)
                  setConfirmReply(false)
                }}
                placeholder="Draft reply"
                className="mt-3 w-full resize-y border-b border-ink-700/50 bg-transparent px-1 py-2 text-sm text-ink-100 placeholder:text-ink-500/60 focus:border-brass-500 focus:outline-none"
              />
              {!confirmReply ? (
                <div className="mt-3 flex justify-end gap-4">
                  <button
                    type="button"
                    className={QUIET_ACTION}
                    onClick={() => {
                      setReplyTarget(null)
                      setReplyText('')
                    }}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    className={TEXT_ACTION}
                    disabled={!replyText.trim() || busy !== null}
                    onClick={() => setConfirmReply(true)}
                  >
                    Review reply →
                  </button>
                </div>
              ) : (
                <div className="mt-3 border-l-[3px] border-brass-500 pl-4">
                  <p className="text-sm text-ink-500">Confirm this outbound message:</p>
                  <p className="my-3 whitespace-pre-wrap text-sm text-ink-100">{replyText}</p>
                  <div className="flex justify-end gap-4">
                    <button
                      type="button"
                      className={QUIET_ACTION}
                      onClick={() => setConfirmReply(false)}
                    >
                      Back
                    </button>
                    <PenButton
                      disabled={busy !== null}
                      onClick={() =>
                        run(
                          'reply',
                          () =>
                            api.groupmeReply({
                              group_slug: replyTarget.groupSlug,
                              text: replyText.trim(),
                            }),
                          () => {
                            setReplyTarget(null)
                            setReplyText('')
                            setConfirmReply(false)
                            inbound.reload()
                            setActionNote('Reply sent.')
                          },
                        )
                      }
                    >
                      {busy === 'reply' ? 'Sending…' : 'Confirm & send'}
                    </PenButton>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </LedgerDisclosure>
    </div>
  )
}

function ConfirmRow({
  message,
  busy,
  confirmLabel,
  onCancel,
  onConfirm,
}: {
  message: string
  busy: boolean
  confirmLabel: string
  onCancel: () => void
  onConfirm: () => void
}) {
  return (
    <div className="flex flex-col gap-3 border-l-[3px] border-brass-500 py-2 pl-4 sm:flex-row sm:items-center sm:justify-between">
      <p className="text-sm text-ink-100">{message}</p>
      <div className="flex shrink-0 items-center justify-end gap-4">
        <button type="button" className={QUIET_ACTION} disabled={busy} onClick={onCancel}>
          Cancel
        </button>
        <PenButton disabled={busy} onClick={onConfirm}>
          {busy ? 'Working…' : confirmLabel}
        </PenButton>
      </div>
    </div>
  )
}

function MessageRows({
  messages,
  onReply,
}: {
  messages: GroupMeInboundMessage[]
  onReply?: (message: GroupMeInboundMessage) => void
}) {
  return (
    <ul>
      {messages.map((message) => (
        <li key={message.id} className="border-b border-ink-700/20 py-3">
          <div className="flex items-baseline justify-between gap-4">
            <span className="truncate text-sm font-semibold text-ink-100">
              {message.sender_name}
            </span>
            <span className="shrink-0 font-mono text-xs text-ink-500 tabular-nums">
              {message.group_slug} · {formatMoment(message.created_at)}
            </span>
          </div>
          <p className="mt-1 whitespace-pre-wrap text-sm text-ink-300">{message.text}</p>
          {(message.triage || message.triage_note || onReply) && (
            <div className="mt-2 flex items-end justify-between gap-4">
              <span
                className={`font-mono text-[11px] uppercase tracking-wider ${
                  message.triage === 'urgent' ? 'text-oxblood-300' : 'text-ink-500'
                }`}
              >
                {message.triage ?? 'untriaged'}
                {message.triage_note && ` · ${message.triage_note}`}
              </span>
              {onReply && (
                <button type="button" className={TEXT_ACTION} onClick={() => onReply(message)}>
                  Draft reply
                </button>
              )}
            </div>
          )}
        </li>
      ))}
    </ul>
  )
}

function DataState({ loading, error }: { loading: boolean; error: string | null }) {
  if (loading) {
    return <p className="py-4 font-mono text-xs text-ink-500">Posting entries…</p>
  }
  if (error) {
    return <p className="border-b border-oxblood-600/50 py-4 text-sm text-oxblood-300">{error}</p>
  }
  return null
}

function EmptyLine({ children }: { children: string }) {
  return <p className="py-5 text-sm italic text-ink-500">{children}</p>
}

function summarizeFeed(messages: GroupMeInboundMessage[]) {
  const groups = new Map<string, { slug: string; count: number; latest: string }>()
  for (const message of messages) {
    const current = groups.get(message.group_slug)
    if (!current) {
      groups.set(message.group_slug, {
        slug: message.group_slug,
        count: 1,
        latest: message.created_at,
      })
      continue
    }
    current.count += 1
    if (message.created_at > current.latest) current.latest = message.created_at
  }
  return [...groups.values()].sort((a, b) => b.latest.localeCompare(a.latest))
}

function formatAge(seconds: number | null) {
  if (seconds === null) return 'not recorded'
  if (seconds < 60) return `${seconds}s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  return `${Math.floor(seconds / 3600)}h ago`
}

function formatMoment(value: string | null) {
  if (!value) return 'never'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

function plural(count: number, singular: string) {
  return count === 1 ? singular : `${singular}s`
}
