import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  testMatch: 'template-region-real-api.spec.ts',
  timeout: 45_000,
  expect: { timeout: 8_000 },
  workers: 1,
  reporter: 'line',
  use: {
    baseURL: 'http://127.0.0.1:5174',
    trace: 'on-first-retry',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: '..\\runtime\\python\\python.exe ../tools/p2_10_browser_server.py --data-root test-results/p2-10-real',
      url: 'http://127.0.0.1:8000/api/healthz',
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: 'npm run dev -- --port 5174',
      url: 'http://127.0.0.1:5174',
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
})
