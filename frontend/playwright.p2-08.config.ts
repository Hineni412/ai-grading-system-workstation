import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  testMatch: 'p2-08-formal-gate.spec.ts',
  timeout: 30_000,
  expect: { timeout: 5_000 },
  forbidOnly: true,
  retries: 0,
  workers: 1,
  reporter: 'line',
  use: {
    baseURL: 'http://127.0.0.1:4188',
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: 'npm run demo:review-batch',
    url: 'http://127.0.0.1:4188/healthz',
    reuseExistingServer: false,
    timeout: 15_000,
  },
})
