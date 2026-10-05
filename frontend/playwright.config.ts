import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  // Real and demo flows run through their dedicated isolated-server configs.
  testMatch: ['app-shell.spec.ts', 'session-config.spec.ts', 'knowledge-graph.spec.ts',
    'template-region-editor.spec.ts', 'review-evidence.spec.ts', 'review-queue.spec.ts',
    'workbench-overview.spec.ts', 'training-recommendations-real-api.spec.ts'],
  timeout: 30_000,
  expect: { timeout: 5_000 },
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : 2,
  reporter: 'html',
  use: {
    baseURL: 'http://127.0.0.1:5173',
    trace: 'on-first-retry',
    serviceWorkers: 'block',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    // The readiness check follows a complete build outside shared dist.
    command: 'node --input-type=module -e "import {build,preview} from \'vite\'; import {randomUUID} from \'node:crypto\'; const outDir = \'../output/TEST-e2e-mock-\' + randomUUID(); await build({build:{outDir}}); await preview({build:{outDir},preview:{host:\'127.0.0.1\',port:5173,strictPort:true}});"',
    env: { AI_GRADING_E2E_MOCK: '1' },
    url: 'http://127.0.0.1:5173',
    reuseExistingServer: false,
  },
})
