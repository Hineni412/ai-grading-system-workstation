import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  testMatch: 'students-real-api.spec.ts',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  workers: 1,
  reporter: 'line',
  use: {
    baseURL: 'http://127.0.0.1:8013',
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    command: '..\\..\\..\\runtime\\python\\python.exe ../tools/p2_13_browser_server.py --data-root test-results/p2-13-real --port 8013',
    url: 'http://127.0.0.1:8013/api/healthz',
    reuseExistingServer: false,
    timeout: 60_000,
  },
})
