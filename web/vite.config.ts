/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Dev: proxy /api to the local FastAPI server so the SPA and API share an
// origin (no CORS dance in dev). Backend: `risk-api` on :8000.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  // jsdom because the outbound-approval tests drive the real Ops page through
  // real clicks against a stubbed `fetch` — the bug they exist to catch (a
  // request body missing preview_id/digest) only shows up in what the client
  // actually sends, not in what a rendered string contains.
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    restoreMocks: true,
  },
})
