import { fileURLToPath } from 'node:url'
import path from 'node:path'
import { defineConfig, devices } from '@playwright/test'

// Run through tools/run_test_suite.py review so data, credentials and ports are isolated.
const python = process.env.AI_GRADING_REVIEW_PYTHON
const artifacts = process.env.AI_GRADING_REVIEW_ARTIFACTS
const apiPort = Number(process.env.AI_GRADING_REVIEW_API_PORT)
const webPort = Number(process.env.AI_GRADING_REVIEW_WEB_PORT)
if (!python || !artifacts || !apiPort || !webPort) {
  throw new Error('Use runtime/python/python.exe tools/run_test_suite.py review')
}
const frontend = fileURLToPath(new URL('.', import.meta.url))
const viteCommand = `node --input-type=module -e "import { createServer } from 'vite'; const server = await createServer({ cacheDir: process.env.AI_GRADING_WORKTREE_DATA_DIR + '/vite-cache', server: { host: '127.0.0.1', port: ${webPort}, strictPort: true, proxy: { '/api': { target: 'http://127.0.0.1:${apiPort}' } } } }); await server.listen();"`

export default defineConfig({
  testDir: './e2e',
  testMatch: 'review-scoring.spec.ts',
  timeout: 90_000,
  expect: { timeout: 15_000 },
  workers: 1,
  retries: 0,
  reporter: 'line',
  outputDir: path.join(artifacts, 'browser'),
  use: {
    baseURL: `http://127.0.0.1:${webPort}`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    acceptDownloads: true,
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: `"${python}" -B -m tests.api_e2e.harness --port ${apiPort}`,
      cwd: path.dirname(frontend.replace(/[\\/]$/, '')),
      url: `http://127.0.0.1:${apiPort}/api/healthz`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: viteCommand,
      cwd: frontend,
      url: `http://127.0.0.1:${webPort}`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
})
