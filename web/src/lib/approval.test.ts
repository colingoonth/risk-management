import { describe, expect, it } from 'vitest'
import { ApiError } from './api'
import { classifyApprovalFailure } from './approval'

// The service raises two different exception types and the API flattens both to
// 400, so these assertions pin the prose the classifier reads. If a message on
// the Python side is reworded, one of these fails — which is the intent: the
// chair being sent to the wrong place is worse than a red test.
describe('classifyApprovalFailure', () => {
  it('reads drift as drift even though its message also says "refresh the preview"', () => {
    const failure = classifyApprovalFailure(
      new ApiError(
        400,
        'the schedule changed since this preview was generated — nothing was sent. ' +
          'Refresh the preview and read it again.',
      ),
    )
    expect(failure.kind).toBe('drift')
    expect(failure.stale).toBe(true)
  })

  it('reads an expired preview as expired, not as drift', () => {
    const failure = classifyApprovalFailure(
      new ApiError(400, 'preview expired after 15 minutes — refresh it and re-read'),
    )
    expect(failure.kind).toBe('expired')
    expect(failure.stale).toBe(true)
    expect(failure.guidance).toContain('nothing has changed')
  })

  it('reads a restart-invalidated preview_id as expired', () => {
    const failure = classifyApprovalFailure(
      new ApiError(400, 'preview_id does not match the digest sent with it — refresh the preview'),
    )
    expect(failure.kind).toBe('expired')
  })

  it('keeps the cap separate — nothing is stale, the window is just too wide', () => {
    const failure = classifyApprovalFailure(
      new ApiError(400, 'window is 90 days; the cap is 31. Announce one block at a time.'),
    )
    expect(failure.kind).toBe('cap')
    expect(failure.stale).toBe(false)
  })

  it('calls a 422 a bug in the client, because that is what it is', () => {
    const failure = classifyApprovalFailure(
      new ApiError(422, 'preview_id: Field required; digest: Field required'),
    )
    expect(failure.kind).toBe('bug')
    expect(failure.stale).toBe(false)
    expect(failure.detail).toContain('preview_id')
  })

  it('does not dress a dead API up as a refusal', () => {
    const failure = classifyApprovalFailure(new TypeError('Failed to fetch'))
    expect(failure.kind).toBe('unreachable')
    expect(failure.detail).toBe('Failed to fetch')
  })
})
