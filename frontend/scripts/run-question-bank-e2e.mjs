import { mkdtempSync, mkdirSync, rmSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const frontend = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const parent = resolve(frontend, '../.test-runs')
mkdirSync(parent, { recursive: true })
const buildRoot = mkdtempSync(join(parent, 'TEST-question-bank-build-'))
const environment = { ...process.env, QB_BROWSER_FRONTEND_DIST: buildRoot }
let status = 1
try {
  const build = spawnSync(process.execPath, [join(frontend, 'node_modules/vite/bin/vite.js'), 'build', '--outDir', buildRoot], {
    cwd: frontend, env: environment, stdio: 'inherit',
  })
  if (build.status === 0) {
    const browser = spawnSync(process.execPath, [join(frontend, 'node_modules/@playwright/test/cli.js'), 'test', '--config', 'playwright.p2-16-real.config.ts'], {
      cwd: frontend, env: environment, stdio: 'inherit',
    })
    status = browser.status ?? 1
    if (browser.error) throw browser.error
  } else if (build.error) {
    throw build.error
  }
} finally {
  rmSync(buildRoot, { recursive: true, force: true })
}
process.exitCode = status
