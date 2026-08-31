import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'

// One mounted page per test. Without this the Ops page from a previous test is
// still in the document and `getByText` finds two of everything.
afterEach(cleanup)
