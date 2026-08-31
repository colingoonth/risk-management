import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { LedgerDisclosure } from './ledger'

describe('LedgerDisclosure', () => {
  it('forces an alarm open even when defaultOpen is false', () => {
    const html = renderToStaticMarkup(
      <LedgerDisclosure title="Coverage alarm" summary="1 short" defaultOpen={false} alarm>
        <p>Test Alpha needs coverage.</p>
      </LedgerDisclosure>,
    )

    expect(html).toContain('aria-expanded="true"')
    expect(html).toContain('disabled=""')
    expect(html).toContain('data-alarm="true"')
    expect(html).toContain('Test Alpha needs coverage.')
    expect(html).toContain('text-oxblood-300')
  })

  it('respects a closed default when there is no alarm', () => {
    const html = renderToStaticMarkup(
      <LedgerDisclosure title="Inbound feed" summary="quiet" defaultOpen={false}>
        <p>Hidden entry</p>
      </LedgerDisclosure>,
    )

    expect(html).toContain('aria-expanded="false"')
    expect(html).not.toContain('disabled=""')
    expect(html).not.toContain('Hidden entry')
  })
})
