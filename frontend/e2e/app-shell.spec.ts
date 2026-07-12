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
  await expect(page.locator('.app-topbar__route')).toContainText('设置')
  await expect(page.getByText('设置工作区尚未迁移', { exact: true })).toBeVisible()

  await page.goto('/missing/deep/path')
  await expect(page.getByRole('heading', { name: '页面未找到' })).toBeVisible()
  await expect(page.getByText('请求的地址不存在；当前考试选择和业务数据都没有改变。')).toBeVisible()
  await page.getByRole('button', { name: '返回工作台' }).click()
  await expect(page).toHaveURL(/\/workbench$/)
})

test('1024 compact navigation state and geometry stay synchronized', async ({ page }) => {
  await fulfillSessions(page)
  await page.setViewportSize({ width: 1024, height: 768 })
  await page.goto('/workbench')
  const navigation = page.getByTestId('app-navigation')
  const toggle = page.getByTestId('navigation-toggle')
  const inspector = page.getByTestId('session-inspector')
  const inspectorToggle = page.getByTestId('inspector-toggle')
  const workbenchLabel = navigation.getByText('工作台', { exact: true })

  await expect(toggle).toHaveAttribute('aria-expanded', 'false')
  await expect(workbenchLabel).toBeHidden()
  expect(await navigation.evaluate((element) => element.getBoundingClientRect().width)).toBe(60)
  expect(await inspector.evaluate((element) => element.getBoundingClientRect().width)).toBe(320)

  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-expanded', 'true')
  await expect(workbenchLabel).toBeVisible()
  expect(await navigation.evaluate((element) => element.getBoundingClientRect().width)).toBe(232)

  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-expanded', 'false')
  expect(await navigation.evaluate((element) => element.getBoundingClientRect().width)).toBe(60)

  await inspectorToggle.click()
  await expect(inspectorToggle).toHaveAttribute('aria-expanded', 'false')
  await expect(inspector).toBeHidden()
  await inspectorToggle.click()
  await expect(inspectorToggle).toHaveAttribute('aria-expanded', 'true')
  expect(await inspector.evaluate((element) => element.getBoundingClientRect().width)).toBe(320)
})

test('desktop shell owns the viewport and workspace scrolls independently', async ({ page }) => {
  await fulfillSessions(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/settings')

  const metrics = await page.evaluate(() => {
    const shell = document.querySelector<HTMLElement>('[data-testid="app-shell"]')!
    const workspace = document.querySelector<HTMLElement>('#main-workspace')!
    const navigation = document.querySelector<HTMLElement>('[data-testid="app-navigation"]')!
    const inspector = document.querySelector<HTMLElement>('[data-testid="session-inspector"]')!
    const fixture = document.createElement('div')
    fixture.style.height = '2000px'
    fixture.dataset.testid = 'long-route-fixture'
    workspace.append(fixture)
    workspace.scrollTop = 320
    return {
      shellHeight: shell.getBoundingClientRect().height,
      workspaceClientHeight: workspace.clientHeight,
      workspaceScrollHeight: workspace.scrollHeight,
      workspaceScrollTop: workspace.scrollTop,
      navigationScrollTop: navigation.scrollTop,
      inspectorScrollTop: inspector.scrollTop,
    }
  })

  expect(metrics.shellHeight).toBe(900)
  expect(metrics.workspaceScrollHeight).toBeGreaterThan(metrics.workspaceClientHeight)
  expect(metrics.workspaceScrollTop).toBeGreaterThan(0)
  expect(metrics.navigationScrollTop).toBe(0)
  expect(metrics.inspectorScrollTop).toBe(0)
})

for (const viewport of [
  { name: 'minimum desktop', width: 1024, height: 768 },
  { name: 'compact desktop boundary', width: 1280, height: 800 },
]) {
  test(`${viewport.name} skips hidden inspector controls during keyboard navigation`, async ({
    page,
  }) => {
    await page.route('**/api/sessions', (route) =>
      route.fulfill({ status: 503, body: 'private service detail' }),
    )
    await page.setViewportSize(viewport)
    await page.goto('/workbench')

    const inspector = page.getByTestId('session-inspector')
    const inspectorToggle = page.getByTestId('inspector-toggle')
    await expect(inspector.getByRole('button', { name: '重新加载考试列表' })).toBeVisible()
    await inspectorToggle.click()
    await expect(inspectorToggle).toHaveAttribute('aria-expanded', 'false')
    await expect(inspector).toBeHidden()

    await inspectorToggle.focus()
    for (let step = 0; step < 12; step += 1) {
      await page.keyboard.press('Tab')
      expect(await inspector.evaluate((element) => !element.contains(document.activeElement))).toBe(
        true,
      )
    }
  })
}
