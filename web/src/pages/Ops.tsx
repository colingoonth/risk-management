import { useState } from 'react'
import { LedgerDisclosure, PenButton } from '../components/ledger'
import { api, approvalOf } from '../lib/api'
import { classifyApprovalFailure } from '../lib/approval'
import type { ApprovalFailure } from '../lib/approval'
import { addDays, mondayOf, todayLocal, weekdayMon0 } from '../lib/dates'
import type {
  GroupMeAnnouncementPost,
  GroupMeAnnouncementPreview,
  GroupMeApprovable,
  GroupMeInboundMessage,
} from '../lib/types'
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
  // Approval refusals are NOT action errors. A 400 here usually means the gate
  // did its job, and the three reasons send the chair to three different
  // places, so they are held apart and rendered as their own alarm.
  const [announceFailure, setAnnounceFailure] = useState<ApprovalFailure | null>(null)
  const [membershipFailure, setMembershipFailure] = useState<ApprovalFailure | null>(null)
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

  /**
   * Send one approval and translate its refusal.
   *
   * `preview` is the response the chair was looking at — passed in whole so the
   * approval can only ever carry the digest of THAT plan. A refusal that leaves
   * the preview unapprovable (expired, drifted) closes the confirm step, because
   * a "try again" button over a dead preview would just fail the same way.
   */
  async function approve(
    key: string,
    preview: GroupMeApprovable,
    send: (approval: ReturnType<typeof approvalOf>) => Promise<unknown>,
    {
      onSent,
      onStale,
      onFailure,
    }: {
      onSent: () => void
      onStale: (failure: ApprovalFailure) => void
      onFailure: (f: ApprovalFailure | null) => void
    },
  ) {
    setBusy(key)
    setActionError(null)
    setActionNote(null)
    onFailure(null)
    try {
      await send(approvalOf(preview, WINDOW))
      onSent()
    } catch (error) {
      const failure = classifyApprovalFailure(error)
      onFailure(failure)
      if (failure.stale) onStale(failure)
    } finally {
      setBusy(null)
    }
  }

  const messages = inbound.data ?? []
  const urgentMessages = messages.filter((message) => message.triage === 'urgent')
  const posts = preview.data?.posts ?? []
  const taggedPeople = new Set(
    posts.flatMap((post) => post.mentions.map((mention) => mention.user_id)),
  ).size
  const unlinked = identities.data?.unlinked ?? []
  const identityDrift = identities.data?.drift ?? []
  const additions = membership.data?.add ?? []
  const removals = membership.data?.remove ?? []
  const membershipBlocks = membership.data?.blocked ?? []
  const hardExcluded = membership.data?.hard_excluded ?? []
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
        summary={
          announceFailure
            ? 'nothing sent'
            : preview.loading
              ? 'building…'
              : `${posts.length} posts`
        }
        defaultOpen
        // A refused approval IS an alarm: oxblood, forced open, not collapsible
        // until it is dealt with. Nothing outbound should be dismissable by
        // folding the section it happened in.
        alarm={announceFailure !== null}
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
          {announceFailure && (
            <ApprovalAlarm
              failure={announceFailure}
              busy={busy !== null}
              onRebuild={
                announceFailure.kind === 'expired'
                  ? () => {
                      setAnnounceFailure(null)
                      preview.reload()
                    }
                  : undefined
              }
            />
          )}
          {posts.length > 0 && !confirmAnnouncement && (
            <div className="flex justify-end border-t border-ink-700/30 pt-3">
              <button
                type="button"
                className={TEXT_ACTION}
                disabled={busy !== null || !preview.data}
                onClick={() => {
                  setAnnounceFailure(null)
                  setConfirmAnnouncement(true)
                }}
              >
                Review approval →
              </button>
            </div>
          )}
          {confirmAnnouncement && preview.data && (
            <AnnounceConfirm
              preview={preview.data}
              taggedPeople={taggedPeople}
              busy={busy === 'announce'}
              disabled={busy !== null}
              onCancel={() => setConfirmAnnouncement(false)}
              onConfirm={() => {
                const approved = preview.data
                if (!approved) return
                approve('announce', approved, (approval) => api.groupmeAnnounce(approval), {
                  onSent: () => {
                    setConfirmAnnouncement(false)
                    preview.reload()
                    setActionNote(
                      `Posted ${approved.posts.length} crew ${plural(approved.posts.length, 'notice')}.`,
                    )
                  },
                  // Drift and expiry both leave this preview unapprovable, so the
                  // confirm step closes either way. Drift ALSO rebuilds on the
                  // spot: the plan on screen describes a schedule that no longer
                  // exists, and the chair must read the new one.
                  onStale: (failure) => {
                    setConfirmAnnouncement(false)
                    if (failure.kind === 'drift') preview.reload()
                  },
                  onFailure: setAnnounceFailure,
                })
              }}
            />
          )}
        </div>
      </LedgerDisclosure>

      <LedgerDisclosure
        title="Membership drift"
        summary={
          membershipFailure
            ? 'nothing applied'
            : identities.loading || membership.loading
              ? 'checking…'
              : membershipAlarm
                ? `${membershipPending} pending`
                : 'aligned'
        }
        defaultOpen={false}
        alarm={membershipAlarm || membershipFailure !== null}
      >
        <div className="px-1 pb-4">
          <DataState loading={identities.loading || membership.loading} error={identities.error ?? membership.error} />
          {identities.data && membership.data && (
            <>
              <p className="border-b border-ink-700/20 py-3 text-sm text-ink-500">
                <span className="font-mono tabular-nums text-ink-300">{identities.data.linked}</span>{' '}
                identities linked · <span className="font-mono tabular-nums">{additions.length}</span>{' '}
                to add · <span className="font-mono tabular-nums">{removals.length}</span> to remove ·{' '}
                <span className="font-mono tabular-nums">{hardExcluded.length}</span> protected
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
                {membershipBlocks.map((item) => (
                  <li
                    key={`blocked-${item.groupme_user_id}`}
                    className="grid gap-1 border-b border-ink-700/20 py-3 sm:grid-cols-[minmax(0,1fr)_auto]"
                  >
                    <span className="text-sm text-ink-100">{item.display_name}</span>
                    <span className="font-mono text-xs uppercase tracking-wider text-oxblood-300">
                      removal blocked
                    </span>
                    <span className="text-xs text-ink-500 sm:col-span-2">
                      {item.reason}: {item.detail}
                    </span>
                  </li>
                ))}
                {hardExcluded.map((item) => (
                  <li
                    key={`hard-excluded-${item.groupme_user_id}`}
                    className="grid gap-1 border-b border-ink-700/20 py-3 sm:grid-cols-[minmax(0,1fr)_auto]"
                  >
                    <span className="text-sm text-ink-100">{item.display_name}</span>
                    <span className="font-mono text-xs uppercase tracking-wider text-ink-500">
                      removal skipped
                    </span>
                    <span className="text-xs text-ink-500 sm:col-span-2">
                      {item.reason}: {item.detail}
                    </span>
                  </li>
                ))}
              </ul>
              {membershipFailure && (
                <ApprovalAlarm
                  failure={membershipFailure}
                  busy={busy !== null}
                  onRebuild={
                    membershipFailure.kind === 'expired'
                      ? () => {
                          setMembershipFailure(null)
                          membership.reload()
                        }
                      : undefined
                  }
                />
              )}
              {additions.length + removals.length > 0 && !confirmMembership && (
                <div className="flex justify-end pt-3">
                  <button
                    type="button"
                    className={TEXT_ACTION}
                    disabled={busy !== null || !membership.data}
                    onClick={() => {
                      setMembershipFailure(null)
                      setConfirmMembership(true)
                    }}
                  >
                    Review membership changes →
                  </button>
                </div>
              )}
              {confirmMembership && membership.data && (
                <ConfirmRow
                  message={`Add ${additions.length} and remove ${removals.length} in the parent group?`}
                  busy={busy === 'membership'}
                  confirmLabel="Confirm membership"
                  onCancel={() => setConfirmMembership(false)}
                  onConfirm={() => {
                    const approved = membership.data
                    if (!approved) return
                    approve(
                      'membership',
                      approved,
                      (approval) => api.groupmeMembershipApply(approval),
                      {
                        onSent: () => {
                          setConfirmMembership(false)
                          membership.reload()
                          identities.reload()
                          setActionNote(
                            `Added ${approved.add.length} and removed ${approved.remove.length}.`,
                          )
                        },
                        onStale: (failure) => {
                          setConfirmMembership(false)
                          if (failure.kind === 'drift') membership.reload()
                        },
                        onFailure: setMembershipFailure,
                      },
                    )
                  }}
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

/**
 * A refused approval, in the ledger's alarm register: oxblood, one hairline
 * rule, no card and no pill. The chair gets three lines — what happened, what to
 * do, and the API's own words — because a refusal he cannot act on is the same
 * as no message at all.
 */
function ApprovalAlarm({
  failure,
  busy,
  onRebuild,
}: {
  failure: ApprovalFailure
  busy: boolean
  onRebuild?: () => void
}) {
  return (
    <div
      role="alert"
      data-failure={failure.kind}
      className="mt-3 border-l-[3px] border-oxblood-600 py-3 pl-4"
    >
      <p className="font-mono text-[11px] uppercase tracking-[0.16em] text-oxblood-300">
        {failure.kind === 'bug' ? 'Dashboard bug — nothing sent' : 'Nothing was sent'}
      </p>
      <p className="mt-2 text-sm text-oxblood-300">{failure.headline}</p>
      <p className="mt-1 text-sm text-ink-300">{failure.guidance}</p>
      <p className="mt-3 border-t border-ink-700/30 pt-2 font-mono text-xs leading-5 text-ink-500">
        {failure.detail}
      </p>
      {onRebuild && (
        <div className="mt-3 flex justify-end">
          <button type="button" className={TEXT_ACTION} disabled={busy} onClick={onRebuild}>
            Rebuild preview →
          </button>
        </div>
      )}
    </div>
  )
}

/**
 * The last screen before anything leaves the building.
 *
 * It shows the THREE things the chair is actually approving — the exact text of
 * every message, the topic each one lands in, and how many people it @-mentions
 * — because "Post 4 crew notices?" asks him to approve a number, and the number
 * is not the thing that gets sent.
 */
function AnnounceConfirm({
  preview,
  taggedPeople,
  busy,
  disabled,
  onCancel,
  onConfirm,
}: {
  preview: GroupMeAnnouncementPreview
  taggedPeople: number
  busy: boolean
  disabled: boolean
  onCancel: () => void
  onConfirm: () => void
}) {
  const posts = preview.posts
  return (
    <div data-confirm="announce" className="mt-3 border-l-[3px] border-brass-500 py-3 pl-4">
      <p className="font-mono text-[11px] uppercase tracking-[0.16em] text-ink-500">
        Sending {posts.length} {plural(posts.length, 'message')} · {taggedPeople}{' '}
        {taggedPeople === 1 ? 'person' : 'people'} tagged
      </p>

      {preview.identity_check !== 'live' && (
        <p className="mt-3 text-sm text-oxblood-300">
          Membership could not be checked against GroupMe, so these mentions are read from the
          database alone. Anyone who has left the group will not be tagged.
        </p>
      )}
      {preview.oversize.length > 0 && (
        <p className="mt-3 text-sm text-oxblood-300">
          {preview.oversize.length} {plural(preview.oversize.length, 'message')} exceeded GroupMe's
          1000-character limit and {preview.oversize.length === 1 ? 'is' : 'are'} not included.
        </p>
      )}
      {preview.unroutable.length > 0 && (
        <p className="mt-3 text-sm text-oxblood-300">
          {preview.unroutable.length} {plural(preview.unroutable.length, 'event')} in this window
          {preview.unroutable.length === 1 ? ' has' : ' have'} no topic and will not be announced.
        </p>
      )}

      <ul className="mt-1">
        {posts.map((post) => (
          <li
            key={`approve-${post.group_slug}-${post.event_date}-${post.event_name}`}
            className="border-b border-ink-700/20 py-3"
          >
            <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
              <span className="text-sm text-ink-100">
                To <span className="font-semibold">{post.label}</span>
              </span>
              <span className="font-mono text-xs text-ink-500 tabular-nums">
                {post.mentions.length} {post.mentions.length === 1 ? 'person' : 'people'} tagged ·{' '}
                {post.char_count} chars
              </span>
            </div>
            <pre className="mt-2 whitespace-pre-wrap font-mono text-xs leading-6 text-ink-100">
              {post.text}
            </pre>
            {post.unlinked.length > 0 && <UnmentionableLine post={post} />}
          </li>
        ))}
      </ul>

      <div className="mt-3 flex items-center justify-end gap-4">
        <button type="button" className={QUIET_ACTION} disabled={busy} onClick={onCancel}>
          Cancel
        </button>
        <PenButton disabled={disabled} onClick={onConfirm}>
          {busy ? 'Posting…' : 'Confirm & post'}
        </PenButton>
      </div>
    </div>
  )
}

// Crew on the post who have no linked GroupMe identity: their line still says
// their name, but the @ does not reach them. Named, not counted — the chair has
// to know WHO to chase.
function UnmentionableLine({ post }: { post: GroupMeAnnouncementPost }) {
  return (
    <p className="mt-2 text-xs text-oxblood-300">
      Not tagged: {post.unlinked.map((person) => person.display_name).join(', ')} — no linked
      GroupMe identity, so they will not be notified.
    </p>
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
