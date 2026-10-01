import { defineConfig, devices } from '@playwright/test'

const dataRoot = `test-results/TEST-question-bank-${Date.now()}`
const frontendDist = process.env.QB_BROWSER_FRONTEND_DIST
process.env.QB_BROWSER_DATA_ROOT = dataRoot
process.env.PYTHONUTF8 = '1'
export default defineConfig({
  outputDir: `../output/TEST-question-bank-browser-${Date.now()}`,
  testDir: './e2e',
  testMatch: 'question-bank-real-api.spec.ts',
  timeout: 180_000,
  expect: { timeout: 12_000 },
  workers: 1,
  reporter: 'line',
  use: {
    baseURL: 'http://127.0.0.1:8016',
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
    command: `..\\runtime\\python\\python.exe ../tools/p2_16_browser_server.py --data-root ${dataRoot} --fresh --port 8016${frontendDist ? ` --frontend-dist ${frontendDist}` : ''}`,
    url: 'http://127.0.0.1:8016/api/healthz',
    reuseExistingServer: false,
    timeout: 60_000,
  },
})
