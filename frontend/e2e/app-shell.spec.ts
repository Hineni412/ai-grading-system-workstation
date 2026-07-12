import { expect, test, type Page } from '@playwright/test'

const STORAGE_KEY = 'ai-grading:selected-session:v1'
const longSessionName =
  '2025—2026 学年度第二学期七年级数学期末质量监测与学情诊断测试（城北校区联合命题）'
const sessions = [
  {
    id: 1,
    name: '七年级数学阶段检测',
    status: 'pending',
    is_deleted: false,
    deleted_at: null,
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-07-01T00:00:00Z',
  },
  {
    id: 2,
    name: longSessionName,
    status: 'grading',
    is_deleted: false,
    deleted_at: null,
    created_at: '2026-07-02T00:00:00Z',
    updated_at: '2026-07-02T00:00:00Z',
  },
]
const desktopViewports = [
  { name: 'desktop', width: 1440, height: 900, navigationWidth: 232, inspectorWidth: 360 },
  { name: 'compact desktop', width: 1280, height: 800, navigationWidth: 232, inspectorWidth: 360 },
  { name: 'tablet landscape', width: 1024, height: 768, navigationWidth: 60, inspectorWidth: 320 },
]
const narrowViewports = [
  { name: 'tablet portrait', width: 768, height: 1024 },
  { name: 'mobile', width: 390, height: 844 },
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

async function openInspector(page: Page): Promise<void> {
  await page.getByTestId('inspector-toggle').click()
}

async function closeInspector(page: Page): Promise<void> {
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('inspector-toggle')).toBeFocused()
}

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

for (const viewport of desktopViewports) {
  test(`${viewport.name} preserves session context without shell overflow`, async ({ page }) => {
    const errors = trackBrowserErrors(page)
    await exerciseSessionContext(page, viewport, noop, noop)
    const navigationWidth = await page.getByTestId('app-navigation').evaluate(
      (element) => element.getBoundingClientRect().width,
    )
    const inspectorWidth = await page.getByTestId('session-inspector').evaluate(
      (element) => element.getBoundingClientRect().width,
    )
    expect(navigationWidth).toBe(viewport.navigationWidth)
    expect(inspectorWidth).toBe(viewport.inspectorWidth)

    expect(errors.pageErrors).toEqual([])
    expect(errors.consoleErrors).toEqual([])
  })
}

for (const viewport of narrowViewports) {
  test(`${viewport.name} preserves session context without shell overflow`, async ({ page }) => {
    const errors = trackBrowserErrors(page)
    await exerciseSessionContext(page, viewport, openInspector, closeInspector)
    const navigationToggle = page.getByTestId('navigation-toggle')
    const inspectorToggle = page.getByTestId('inspector-toggle')
    await page.keyboard.press('Escape')
    await navigationToggle.click()
    await expect(navigationToggle).toHaveAttribute('aria-expanded', 'true')
    await inspectorToggle.click()
    await expect(navigationToggle).toHaveAttribute('aria-expanded', 'false')
    await expect(inspectorToggle).toHaveAttribute('aria-expanded', 'true')
    await page.keyboard.press('Escape')
    await expect(inspectorToggle).toHaveAttribute('aria-expanded', 'false')
    await expect(inspectorToggle).toBeFocused()

    expect(errors.pageErrors).toEqual([])
    expect(errors.consoleErrors).toEqual([])
  })
}

test('removes a stale saved session after a successful load', async ({ page }) => {
  await page.addInitScript(([key]) => localStorage.setItem(key, '999'), [STORAGE_KEY])
  await fulfillSessions(page)
  await page.goto('/workbench')

  await expect(page.getByRole('combobox', { name: '当前考试' })).toHaveValue('')
  expect(await page.evaluate((key) => localStorage.getItem(key), STORAGE_KEY)).toBeNull()
})

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

test('future navigation remains disabled while settings and 404 return stay usable', async ({
  page,
}) => {
  await fulfillSessions(page)
  await page.goto('/workbench')

  const futureEntry = page.getByText('智能体与自动化', { exact: true })
  await expect(futureEntry).toBeVisible()
  await expect(futureEntry.locator('..')).toHaveAttribute('aria-disabled', 'true')

  await page.getByRole('link', { name: '设置' }).click()
  await expect(page).toHaveURL(/\/settings$/)
  await expect(page.getByRole('heading', { name: '设置' })).toBeVisible()

  await page.goto('/missing/deep/path')
  await expect(page.getByRole('heading', { name: '页面未找到' })).toBeVisible()
  await page.getByRole('button', { name: '返回工作台' }).click()
  await expect(page).toHaveURL(/\/workbench$/)
})
