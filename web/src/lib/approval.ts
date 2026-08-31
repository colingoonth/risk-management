// What an outbound approval can fail with, in the chair's language.
//
// Every outbound GroupMe route rebuilds its plan from live data and compares it
// against the preview being approved, so a refusal is USUALLY not a bug — it is
// the gate working. The three refusals send the chair to three different places,
// and a raw "400 Bad Request" sends him nowhere:
//
//   expired  the approval is older than the preview's fifteen-minute life (or
//            the API restarted). Nothing moved; re-preview and approve again.
//   drift    something DID move — a shift, a nickname, an event date, a topic.
//            The preview on screen is describing a schedule that no longer
//            exists and must be rebuilt and re-read, not retried.
//   422      the dashboard sent a body without `preview_id`/`digest`. That is
//            the client's fault, no retry helps, and the UI should say so
//            instead of dressing a bug up as a server mood.

import { ApiError } from './api'

export type ApprovalFailureKind =
  | 'expired'
  | 'drift'
  | 'cap'
  | 'bug'
  | 'unreachable'
  | 'refused'

export interface ApprovalFailure {
  kind: ApprovalFailureKind
  /** One line: what happened. */
  headline: string
  /** What the chair does next. */
  guidance: string
  /** True when the preview on screen can no longer be approved at all. */
  stale: boolean
  /** The server's own words, kept verbatim rather than paraphrased away. */
  detail: string
}

// Matched against the API's `detail`. The service raises two distinct exception
// types and `service_errors()` flattens BOTH to 400, so the prose is the only
// signal on the wire — hence matching it here rather than pretending 400 means
// one thing. DRIFT IS TESTED FIRST: its message ends "Refresh the preview and
// read it again", which the expiry patterns would otherwise claim.
const DRIFT = ['changed since this preview', 'schedule changed']
const EXPIRED = [
  'preview expired',
  'preview_id',
  'refresh the preview',
  'refresh it and re-read',
  'stamped in the future',
]
const CAP = ['the cap is']

const has = (haystack: string, needles: string[]) =>
  needles.some((needle) => haystack.includes(needle))

export function classifyApprovalFailure(error: unknown): ApprovalFailure {
  if (!(error instanceof ApiError)) {
    return {
      kind: 'unreachable',
      headline: 'The API did not answer.',
      guidance:
        'Nothing was sent. Check that the risk-api server is running, then preview again.',
      stale: false,
      detail: error instanceof Error ? error.message : String(error),
    }
  }

  const detail = error.detail ?? ''
  const lower = detail.toLowerCase()

  if (error.status === 422) {
    return {
      kind: 'bug',
      headline: 'The dashboard sent the wrong shape. This is a bug, not a refusal.',
      guidance:
        'The approval reached the API without the preview it was approving, so nothing was sent ' +
        'and retrying this screen will not help. The missing field is named below.',
      stale: false,
      detail,
    }
  }

  if (error.status === 400 && has(lower, DRIFT)) {
    return {
      kind: 'drift',
      headline: 'The schedule changed underneath this preview.',
      guidance:
        'A shift, a nickname, an event date or a topic moved between the preview you read and ' +
        'this approval. Nothing was sent. The preview has been rebuilt — read it again before ' +
        'approving.',
      stale: true,
      detail,
    }
  }

  if (error.status === 400 && has(lower, EXPIRED)) {
    return {
      kind: 'expired',
      headline: 'That preview has expired.',
      guidance:
        'An approval is good for fifteen minutes, and never across an API restart. Nothing was ' +
        'sent, and nothing has changed. Rebuild the preview, read it, and approve the new one.',
      stale: true,
      detail,
    }
  }

  if (error.status === 400 && has(lower, CAP)) {
    return {
      kind: 'cap',
      headline: 'This is over the send cap.',
      guidance:
        'One approval may carry at most 10 posts across at most 31 days. Nothing was sent. ' +
        'Narrow the window and preview that block on its own.',
      stale: false,
      detail,
    }
  }

  return {
    kind: 'refused',
    headline: `The send was refused (${error.status}).`,
    guidance: 'Nothing was sent.',
    stale: false,
    detail,
  }
}
