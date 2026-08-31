// The approve button, end to end, against a stubbed API.
//
// These tests exist because the dashboard and the GroupMe routes were built in
// parallel and the client posted `{on_or_after, on_or_before, confirm}` — a body
// the API answers with 422, forever. The only assertion that would have caught
// that is one that reads the REQUEST the page actually sent, so that is what
// these do, through real clicks on the real page.

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Ops } from './Ops'

const PREVIEW_ID = '1756600000.0123456789abcdef0123456789abcdef'
const DIGEST = 'a'.repeat(64)

// Invented chapter, invented people, invented ids. Nothing here is real and
// nothing here may become real: this repo is public.
const ANNOUNCE_TEXT =
  'Test Mixer — Fri 4 Sep\n@Test Alpha on door\n@Test Bravo on rides'

const PREVIEW = {
  preview_id: PREVIEW_ID,
  digest: DIGEST,
  posts: [
    {
      group_slug: 'friday-topic',
      label: 'Friday Crew',
      event_date: '2026-09-04',
      event_name: 'Test Mixer',
      text: ANNOUNCE_TEXT,
      mentions: [
        { user_id: 'user-alpha', display_name: 'Test Alpha', offset: 23, length: 11 },
        { user_id: 'user-bravo', display_name: 'Test Bravo', offset: 43, length: 11 },
      ],
      unlinked: [],
      char_count: ANNOUNCE_TEXT.length,
      too_long: false,
    },
  ],
  unroutable: [],
  oversize: [],
  identity_check: 'live',
}

const HEALTH = { groups: [], heartbeat_age_seconds: 12, healthy: true }
const IDENTITIES = { linked: 2, unlinked: [], drift: [], rows: [], blocked: [] }
const MEMBERSHIP = {
  preview_id: PREVIEW_ID,
  digest: DIGEST,
  add: [],
  remove: [],
  unrecognised: [],
  blocked: [],
  unlinked_workers: [],
}

interface Reply {
  status: number
  body: unknown
}

const ok = (body: unknown): Reply => ({ status: 200, body })

interface Call {
  url: string
  method: string
  body: unknown
}

/** Stub `fetch` for the five GETs the page opens with, plus the announce POST. */
function stubApi(announce: () => Reply, preview: unknown = PREVIEW) {
  const calls: Call[] = []
  const previewFetches = { count: 0 }

  const fetchMock = vi.fn(async (input: unknown, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    calls.push({
      url,
      method,
      body: typeof init?.body === 'string' ? JSON.parse(init.body) : null,
    })

    let reply: Reply
    if (url.startsWith('/api/groupme/health')) reply = ok(HEALTH)
    else if (url.startsWith('/api/groupme/inbound')) reply = ok([])
    else if (url.startsWith('/api/groupme/identities')) reply = ok(IDENTITIES)
    else if (url.startsWith('/api/groupme/membership-plan')) reply = ok(MEMBERSHIP)
    else if (url.startsWith('/api/groupme/announce-preview')) {
      previewFetches.count += 1
      reply = ok(preview)
    } else if (url === '/api/groupme/announce') reply = announce()
    else reply = { status: 404, body: { detail: `unstubbed ${method} ${url}` } }

    return {
      ok: reply.status < 400,
      status: reply.status,
      statusText: `stub ${reply.status}`,
      json: async () => reply.body,
    }
  })

  vi.stubGlobal('fetch', fetchMock)
  return {
    calls,
    previewFetches,
    announceBody: () => calls.find((c) => c.url === '/api/groupme/announce')?.body,
    announceCount: () => calls.filter((c) => c.url === '/api/groupme/announce').length,
  }
}

/** Open the preview, then the confirm step. */
async function openConfirmStep() {
  fireEvent.click(await screen.findByRole('button', { name: /Review approval/ }))
  return screen.getByRole('button', { name: /Confirm & post/ })
}

const alarm = () => document.querySelector('[role="alert"][data-failure]')

beforeEach(() => {
  vi.unstubAllGlobals()
})

describe('Ops — approving an announcement', () => {
  it('sends back the preview_id and digest it was given, for the window it previewed', async () => {
    const api = stubApi(() => ok({ posted: [] }))
    render(<Ops />)

    fireEvent.click(await openConfirmStep())

    await waitFor(() => expect(api.announceBody()).toBeTruthy())
    const previewUrl = new URL(
      api.calls.find((c) => c.url.startsWith('/api/groupme/announce-preview'))!.url,
      'http://test.invalid',
    )
    expect(api.announceBody()).toEqual({
      on_or_after: previewUrl.searchParams.get('on_or_after'),
      on_or_before: previewUrl.searchParams.get('on_or_before'),
      preview_id: PREVIEW_ID,
      digest: DIGEST,
      confirm: true,
    })
    expect(await screen.findByText(/Posted 1 crew notice/)).toBeTruthy()
    expect(alarm()).toBeNull()
  })

  it('shows the exact text, the destination topic and the person count before sending', async () => {
    stubApi(() => ok({ posted: [] }))
    render(<Ops />)
    await openConfirmStep()

    const confirmStep = document.querySelector('[data-confirm="announce"]')!
    expect(confirmStep.textContent).toContain(ANNOUNCE_TEXT)
    expect(confirmStep.textContent).toContain('Friday Crew')
    expect(confirmStep.textContent).toContain('2 people tagged')
    expect(confirmStep.textContent).toContain('Sending 1 message')
  })

  it('names the crew who will not be tagged', async () => {
    const withUnlinked = {
      ...PREVIEW,
      posts: [
        {
          ...PREVIEW.posts[0],
          unlinked: [{ member_id: 7, display_name: 'Test Charlie', reason: 'no link' }],
        },
      ],
    }
    stubApi(() => ok({ posted: [] }), withUnlinked)
    render(<Ops />)
    await openConfirmStep()

    expect(screen.getByText(/Not tagged: Test Charlie/)).toBeTruthy()
  })
})

describe('Ops — a refused approval', () => {
  it('tells the chair to re-preview when the preview has expired', async () => {
    const api = stubApi(() => ({
      status: 400,
      body: { detail: 'preview expired after 15 minutes — refresh it and re-read' },
    }))
    render(<Ops />)
    fireEvent.click(await openConfirmStep())

    const box = await waitFor(() => {
      const found = alarm()
      expect(found).toBeTruthy()
      return found!
    })
    expect(box.getAttribute('data-failure')).toBe('expired')
    expect(box.textContent).toContain('That preview has expired.')
    expect(box.textContent).toContain('Rebuild the preview')
    // The server's own words are kept, but they are not the whole message.
    expect(box.textContent).toContain('preview expired after 15 minutes')
    expect(box.textContent).toContain('Nothing was sent')
    // Oxblood, hairline, no card and no pill — the ledger's alarm register.
    expect(box.className).toContain('border-oxblood-600')

    // The dead preview is no longer approvable...
    expect(screen.queryByRole('button', { name: /Confirm & post/ })).toBeNull()
    // ...and the way out is offered, not left to be guessed.
    const previewsBefore = api.previewFetches.count
    fireEvent.click(screen.getByRole('button', { name: /Rebuild preview/ }))
    await waitFor(() => expect(api.previewFetches.count).toBe(previewsBefore + 1))
    await waitFor(() => expect(alarm()).toBeNull())
  })

  it('says the schedule moved and forces a re-preview on drift', async () => {
    const api = stubApi(() => ({
      status: 400,
      body: {
        detail:
          'the schedule changed since this preview was generated — nothing was sent. ' +
          'Refresh the preview and read it again.',
      },
    }))
    render(<Ops />)
    await openConfirmStep()
    const previewsBefore = api.previewFetches.count
    fireEvent.click(screen.getByRole('button', { name: /Confirm & post/ }))

    const box = await waitFor(() => {
      const found = alarm()
      expect(found).toBeTruthy()
      return found!
    })
    expect(box.getAttribute('data-failure')).toBe('drift')
    expect(box.textContent).toContain('The schedule changed underneath this preview.')
    expect(box.textContent).toContain('read it again before approving')

    // Forced, not offered: the plan on screen is rebuilt without being asked,
    // and the confirm step is gone so the new one has to be read.
    await waitFor(() => expect(api.previewFetches.count).toBe(previewsBefore + 1))
    expect(screen.queryByRole('button', { name: /Confirm & post/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /Rebuild preview/ })).toBeNull()
    expect(api.announceCount()).toBe(1)
  })

  it('calls a 422 what it is — a bug in the dashboard', async () => {
    stubApi(() => ({
      status: 422,
      body: {
        detail: [
          { type: 'missing', loc: ['body', 'preview_id'], msg: 'Field required' },
          { type: 'missing', loc: ['body', 'digest'], msg: 'Field required' },
        ],
      },
    }))
    render(<Ops />)
    fireEvent.click(await openConfirmStep())

    const box = await waitFor(() => {
      const found = alarm()
      expect(found).toBeTruthy()
      return found!
    })
    expect(box.getAttribute('data-failure')).toBe('bug')
    expect(box.textContent).toContain('This is a bug, not a refusal.')
    // The validation errors are readable, not "[object Object]".
    expect(box.textContent).toContain('preview_id: Field required')
    expect(box.textContent).toContain('digest: Field required')
    expect(box.textContent).not.toContain('[object Object]')
  })
})
