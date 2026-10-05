import { expect, type Page } from '@playwright/test'
import { test } from './mock-fixtures'

const STORAGE_KEY = 'ai-grading:selected-session:v1'
const longSessionName =
  '2025—2026 学年度第二学期七年级数学期末质量监测与学情诊断测试（城北校区联合命题）'
const sessions = [
  {
    id: 1,
    name: '七年级数学阶段检测',
    status: 'pending',
    curriculum_volume_id: null,
    is_deleted: false,
    deleted_at: null,
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-07-01T00:00:00Z',
  },
  {
    id: 2,
    name: longSessionName,
    status: 'grading',
    curriculum_volume_id: null,
    is_deleted: false,
    deleted_at: null,
    created_at: '2026-07-02T00:00:00Z',
    updated_at: '2026-07-02T00:00:00Z',
  },
]
const viewports = [
  { name: 'large desktop', width: 1920, height: 1080 },
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'standard workstation', width: 1366, height: 768 },
  { name: 'compact desktop', width: 1280, height: 800 },
  { name: 'minimum desktop', width: 1024, height: 768 },
]

async function fulfillSessions(page: Page): Promise<void> {
  await page.route(/\/api\/sessions\/\d+\/(?:config\/editor|config\/sources\/active)$/, route =>
    route.fulfill({ status: 404, json: { error: { code: 'config_not_found', message: 'TEST-empty', request_id: 'TEST' } } }))
  await page.route(/\/api\/sessions\/\d+\/regions\/readiness$/, route =>
    route.fulfill({ json: { session_id: Number(new URL(route.request().url()).pathname.split('/')[3]),
      scoring_configured: false, template_present: false, template_ready: false } }))
  await page.route(/\/api\/sessions\/\d+\/review\/questions(?:\?.*)?$/, route =>
    route.fulfill({ json: { items: [], total: 0 } }))
  await page.route(/\/api\/sessions\/\d+\/config$/, route => route.fulfill({ json: { rubric: { questions: [] } } }))
  await page.route('**/api/sessions', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: sessions, total: sessions.length }),
    }),
  )
}

async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    ),
  ).toBe(true)
}

function trackBrowserErrors(page: Page) {
  const pageErrors: Error[] = []
  const consoleErrors: string[] = []
  page.on('pageerror', (error) => pageErrors.push(error))
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  return { pageErrors, consoleErrors }
}

async function exerciseSessionContext(
  page: Page,
  viewport: { width: number; height: number },
): Promise<void> {
  await fulfillSessions(page)
  await page.addInitScript(key => {
    if (localStorage.getItem(key) === null) localStorage.setItem(key, '1')
  }, STORAGE_KEY)
  await page.setViewportSize(viewport)
  await page.goto('/grading')

  const selector = page.locator('.exam-switcher > [data-state]').first()
  await expect(selector).toBeEnabled()
  await expect(selector).toHaveAccessibleName(/七年级数学阶段检测/)
  await selector.click()
  await page.getByRole('option').filter({ hasText: longSessionName }).click()
  await expect(selector).toHaveAccessibleName(new RegExp(longSessionName))
  expect(await page.evaluate(key => localStorage.getItem(key), STORAGE_KEY)).toBe('2')
  await expectNoHorizontalOverflow(page)

  const selectorBounds = await selector.boundingBox()
  expect(selectorBounds).not.toBeNull()
  expect(selectorBounds!.x).toBeGreaterThanOrEqual(0)
  expect(selectorBounds!.x + selectorBounds!.width).toBeLessThanOrEqual(viewport.width)

  await page.reload()
  await expect(selector).toHaveAccessibleName(new RegExp(longSessionName))
  await selector.click()
  await expect(page.getByRole('option').filter({ hasText: longSessionName })).toHaveAttribute('aria-selected', 'true')
  await page.getByLabel('搜索考试').press('Escape')
  await expectNoHorizontalOverflow(page)
}

for (const viewport of viewports) {
  test(`${viewport.name} preserves session context without shell overflow`, async ({ page }) => {
    const errors = trackBrowserErrors(page)
    await exerciseSessionContext(page, viewport)
    await expect(page.getByTestId('app-navigation')).toBeVisible()
    await expect(page.getByRole('button', { name: /当前考试/ })).toHaveAccessibleName(new RegExp(longSessionName))

    expect(errors.pageErrors).toEqual([])
    expect(errors.consoleErrors).toEqual([])
  })
}

test('preserves an unverified saved candidate through failure and restores it on retry', async ({
  page,
}) => {
  let fail = true
  await page.addInitScript(([key]) => localStorage.setItem(key, '2'), [STORAGE_KEY])
  await fulfillSessions(page)
  await page.route('**/api/sessions', (route) => {
    if (fail) return route.fulfill({ status: 503, body: 'temporarily unavailable' })
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: sessions, total: sessions.length }),
    })
  })
  await page.goto('/grading')

  const selector = page.getByRole('button', { name: /当前考试/ })
  await selector.click()
  await expect(page.getByRole('alert').filter({ hasText: '考试列表加载失败' })).toBeVisible()
  expect(await page.evaluate((key) => localStorage.getItem(key), STORAGE_KEY)).toBe('2')

  fail = false
  await page.getByRole('alert').filter({ hasText: '考试列表加载失败' })
    .getByRole('button', { name: '重新加载', exact: true }).click()
  await expect(selector).toHaveAccessibleName(new RegExp(longSessionName))
  expect(await page.evaluate(key => localStorage.getItem(key), STORAGE_KEY)).toBe('2')
})
