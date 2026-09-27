import { expect, test, type Page } from '@playwright/test'

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

async function noop(): Promise<void> {}

async function exerciseSessionContext(
  page: Page,
  viewport: { width: number; height: number },
  showInspector: (page: Page) => Promise<void>,
  hideInspector: (page: Page) => Promise<void>,
): Promise<void> {
  await fulfillSessions(page)
  await page.setViewportSize(viewport)
  await page.goto('/workbench')

  const selector = page.getByRole('combobox', { name: '当前考试' })
  await expect(selector).toBeEnabled()
  await selector.selectOption('2')
  await showInspector(page)
  await expect(page.getByTestId('session-inspector')).toContainText(longSessionName)
  await expectNoHorizontalOverflow(page)

  const selectorBounds = await selector.boundingBox()
  expect(selectorBounds).not.toBeNull()
  expect(selectorBounds!.x).toBeGreaterThanOrEqual(0)
  expect(selectorBounds!.x + selectorBounds!.width).toBeLessThanOrEqual(viewport.width)

  await hideInspector(page)
  await page.reload()
  await expect(selector).toHaveValue('2')
  await showInspector(page)
  await expect(page.getByTestId('session-inspector')).toContainText(longSessionName)
  await expectNoHorizontalOverflow(page)
}

for (const viewport of viewports) {
  test(`${viewport.name} preserves session context without shell overflow`, async ({ page }) => {
    const errors = trackBrowserErrors(page)
    await exerciseSessionContext(page, viewport, noop, noop)
    const navigationWidth = await page.getByTestId('app-navigation').evaluate(
      (element) => element.getBoundingClientRect().width,
    )
    const inspectorWidth = await page.getByTestId('session-inspector').evaluate(
      (element) => element.getBoundingClientRect().width,
    )
    expect(navigationWidth).toBe(viewport.width === 1024 ? 60 : 232)
    expect(inspectorWidth).toBe(viewport.width === 1024 ? 320 : 360)

    expect(errors.pageErrors).toEqual([])
    expect(errors.consoleErrors).toEqual([])
  })
}

test('preserves an unverified saved candidate through failure and restores it on retry', async ({
  page,
}) => {
  let fail = true
  await page.addInitScript(([key]) => localStorage.setItem(key, '2'), [STORAGE_KEY])
  await page.route('**/api/sessions', (route) => {
    if (fail) return route.fulfill({ status: 503, body: 'temporarily unavailable' })
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: sessions, total: sessions.length }),
    })
  })
  await page.goto('/workbench')

  await expect(page.getByRole('heading', { name: '考试列表加载失败' })).toBeVisible()
  await expect(page.getByRole('combobox', { name: '当前考试' })).toHaveValue('')
  expect(await page.evaluate((key) => localStorage.getItem(key), STORAGE_KEY)).toBe('2')

  fail = false
  await page.getByRole('button', { name: '重新加载考试列表' }).click()
  await expect(page.getByRole('combobox', { name: '当前考试' })).toHaveValue('2')
  await expect(page.getByTestId('session-inspector')).toContainText(longSessionName)
})
