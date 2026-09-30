import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  testMatch: 'training-recommendations-real-api.spec.ts',
  timeout: 90_000,
  expect: { timeout: 12_000 },
  workers: 1,
  reporter: 'line',
  use: {
    baseURL: 'http://127.0.0.1:8018',
    trace: 'on-first-retry',
    acceptDownloads: true,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    command: '..\\runtime\\python\\python.exe ../tools/p2_18_browser_server.py --data-root test-results/p2-18-real --port 8018',
    url: 'http://127.0.0.1:8018/api/healthz',
    reuseExistingServer: false,
    timeout: 60_000,
  },
})
