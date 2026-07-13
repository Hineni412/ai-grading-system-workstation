import { expect, test, type Page } from '@playwright/test'

const ORIGIN = process.env.P2_08_ORIGIN ?? 'http://127.0.0.1:4188'
const STORAGE_KEY = 'ai-grading:selected-session:v1'
const viewports = [
  { name: '1920×1080', width: 1920, height: 1080 },
  { name: '1440×900', width: 1440, height: 900 },
  { name: '1366×768', width: 1366, height: 768 },
  { name: '1280×800', width: 1280, height: 800 },
  { name: '1024×768', width: 1024, height: 768 },
]

async function setMode(page: Page, mode: Record<string, string>) {
  const response = await page.request.post(`${ORIGIN}/__p2_08__/mode`, { data: mode })
  expect(response.ok()).toBe(true)
}

async function openReview(page: Page, detailId = 1, expectImage = true) {
  await page.addInitScript(([key]) => localStorage.setItem(key, '7'), [STORAGE_KEY])
  await page.goto(`${ORIGIN}/grading?question=Q1&detail=${detailId}`)
  await expect(page.getByRole('heading', { name: '复核队列', exact: true })).toBeVisible()
  await expect(page.getByTestId('review-scoring-inspector')).toBeVisible()
  if (expectImage) {
    await expect(page.locator('.review-evidence-canvas img')).toHaveJSProperty('complete', true)
  }
}

test.beforeEach(async ({ request }) => {
  const response = await request.post(`${ORIGIN}/__p2_08__/reset`, { data: {} })
  expect(response.ok()).toBe(true)
})

test('fixed anonymous page exposes only real shortcuts and restores confirmed state after refresh', async ({ page }) => {
  const consoleErrors: string[] = []
  const pageErrors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  page.on('pageerror', (error) => pageErrors.push(error.message))

  await openReview(page)
  await expect(page.getByRole('heading', { name: '匿名学生一号（超长姓名布局核对）' })).toBeVisible()
  await expect(page.getByText('一次函数综合应用', { exact: true })).toBeVisible()

  const guide = page.getByLabel('单题复核快捷键')
  await expect(guide).toContainText('J / K')
  await expect(guide).toContainText('Enter')
  await expect(guide).toContainText('Shift + Enter')
  await expect(guide).toContainText('适应宽度')
  await expect(guide).toContainText('搜索学生')
  await expect(guide).not.toContainText(/(^|\s)R($|\s)/)

  const scoreHierarchy = await page.evaluate(() => ({
    teacher: Number.parseFloat(getComputedStyle(
      document.querySelector<HTMLElement>('[data-testid="teacher-score"]')!,
    ).fontSize),
    ai: Number.parseFloat(getComputedStyle(
      document.querySelector<HTMLElement>('.review-ai-score strong')!,
    ).fontSize),
  }))
  expect(scoreHierarchy.teacher).toBeGreaterThan(scoreHierarchy.ai)

  const canvas = page.getByLabel('答卷图片画布')
  const scale = page.getByLabel('当前缩放比例')
  await canvas.focus()
  await canvas.press('+')
  await expect(scale).not.toHaveText('100%')
  await canvas.press('z')

  await page.keyboard.press('/')
  await expect(page.getByLabel('搜索学生')).toBeFocused()
  await page.keyboard.press('Escape')
  await canvas.focus()

  const score = page.getByTestId('teacher-score')
  await score.fill('4.5')
  await canvas.focus()
  await canvas.press('Enter')
  await expect(page).toHaveURL(/question=Q1&detail=1$/)
  await expect(page.getByText('教师最终分已确认。', { exact: true })).toBeVisible()
  await expect(page).toHaveURL(/question=Q1&detail=1$/)

  await page.reload()
  await expect(page).toHaveURL(/question=Q1&detail=1$/)
  await expect(page.getByRole('heading', { name: '匿名学生一号（超长姓名布局核对）' })).toBeVisible()
  await expect(score).toHaveValue('4.5')
  await expect(page.getByText('教师已确认', { exact: true })).toBeVisible()
  const pixels = await page.locator('.review-evidence-canvas img').evaluate(
    (image: HTMLImageElement) => image.naturalWidth * image.naturalHeight,
  )
  expect(pixels).toBeGreaterThan(0)
  expect(consoleErrors).toEqual([])
  expect(pageErrors).toEqual([])
})

test('navigation, protected focus and confirm-next shortcuts perform exactly one action', async ({ page }) => {
  let confirmRequests = 0
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().endsWith('/confirm')) confirmRequests += 1
  })
  await openReview(page)
  const canvas = page.getByLabel('答卷图片画布')
  const protectedScore = page.getByTestId('teacher-score')
  const scale = page.getByLabel('当前缩放比例')

  await canvas.focus()
  await canvas.press('j')
  await expect(page).toHaveURL(/detail=2$/)
  await canvas.press('k')
  await expect(page).toHaveURL(/detail=1$/)

  await protectedScore.focus()
  await protectedScore.press('j')
  await expect(page).toHaveURL(/detail=1$/)
  await expect(protectedScore).toBeFocused()

  await canvas.focus()
  const beforeZoom = await scale.textContent()
  await canvas.press('-')
  await expect(scale).not.toHaveText(beforeZoom ?? '')
  await canvas.press('r')
  await expect(page).toHaveURL(/detail=1$/)
  expect(confirmRequests).toBe(0)

  await protectedScore.fill('4.25')
  await canvas.focus()
  await canvas.press('Shift+Enter')
  await expect(page).toHaveURL(/detail=2$/)
  expect(confirmRequests).toBe(1)
})

test('unconfirmed drafts survive record changes, warn before refresh and are not presented as saved', async ({ page }) => {
  await openReview(page)
  const score = page.getByTestId('teacher-score')
  await score.fill('4.75')
  await page.getByRole('button', { name: /匿名学生二号/ }).click()
  await page.getByRole('button', { name: /匿名学生一号/ }).click()
  await expect(score).toHaveValue('4.75')

  let dialogType = ''
  page.once('dialog', async (dialog) => {
    dialogType = dialog.type()
    await dialog.accept()
  })
  await page.reload()
  expect(dialogType).toBe('beforeunload')
  await expect(score).toHaveValue('3')
  await expect(page.getByText('教师草稿未确认', { exact: true })).toBeHidden()
})

test('failure retains the draft and annotation retry remains non-blocking', async ({ page }) => {
  await openReview(page, 3)
  const score = page.getByTestId('teacher-score')
  const canvas = page.getByLabel('答卷图片画布')

  await setMode(page, { confirm: '422' })
  await score.fill('3.5')
  await canvas.focus()
  await canvas.press('Enter')
  await expect(page.getByRole('alert')).toContainText('确认失败')
  await expect(score).toHaveValue('3.5')
  await expect(page).toHaveURL(/detail=3$/)

  await setMode(page, { confirm: '500' })
  await canvas.press('Enter')
  await expect(page.getByRole('alert')).toContainText('确认失败')
  await expect(score).toHaveValue('3.5')

  await setMode(page, { confirm: 'retry' })
  await canvas.press('Enter')
  await expect(page.getByText('分数已确认，标注图需要稍后刷新。', { exact: true })).toBeVisible()
  await expect(page).toHaveURL(/detail=3$/)
})

test('loading, disabled, retained-content, empty and first-load error states remain actionable', async ({ page }) => {
  await setMode(page, { items: 'slow' })
  await page.addInitScript(([key]) => localStorage.setItem(key, '7'), [STORAGE_KEY])
  await page.goto(`${ORIGIN}/grading?question=Q1&detail=1`)
  await expect(page.getByText('正在读取复核记录', { exact: true })).toBeVisible()
  await expect(page.getByTestId('review-scoring-inspector')).toBeVisible()
  await expect(page.getByTestId('confirm-next')).toBeDisabled()
  await expect(page.getByText('根据题意建立变量关系', { exact: false })).toBeVisible()

  await setMode(page, { confirm: 'success', items: 'error' })
  await page.getByTestId('teacher-score').fill('4.5')
  await page.getByLabel('答卷图片画布').focus()
  await page.getByLabel('答卷图片画布').press('Enter')
  await expect(page.getByText(/分数已确认，队列刷新失败/)).toBeVisible()
  await expect(page.getByText('复核内容刷新失败', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '重新加载' })).toBeVisible()

  await page.request.post(`${ORIGIN}/__p2_08__/reset`, { data: {} })
  await setMode(page, { items: 'empty' })
  await page.reload()
  await expect(page.locator('.review-queue-list .review-queue-row')).toHaveCount(0)
  await expect(page.getByText('共 0 条', { exact: true })).toBeVisible()

  await setMode(page, { items: 'error' })
  await page.reload()
  await expect(page.getByText('复核队列加载失败', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '重新加载' })).toBeVisible()
})

for (const viewport of viewports) {
  test(`${viewport.name} keeps the workspace reachable without horizontal overflow`, async ({ page }) => {
    await page.setViewportSize(viewport)
    await openReview(page)
    const layout = await page.evaluate(() => {
      const documentElement = document.documentElement
      const footer = document.querySelector<HTMLElement>('[data-testid="scoring-footer"]')
      const workspace = document.querySelector<HTMLElement>('.review-workspace')
      const queue = document.querySelector<HTMLElement>('.review-queue-panel')
      const evidence = document.querySelector<HTMLElement>('.review-detail')
      const inspector = document.querySelector<HTMLElement>('[data-testid="review-scoring-inspector"]')
      const image = document.querySelector<HTMLImageElement>('.review-evidence-canvas img')
      const rect = (element: HTMLElement | null) => element?.getBoundingClientRect() ?? null
      const overlap = (left: DOMRect | null, right: DOMRect | null) => {
        if (!left || !right) return Number.POSITIVE_INFINITY
        return Math.max(0, Math.min(left.right, right.right) - Math.max(left.left, right.left)) *
          Math.max(0, Math.min(left.bottom, right.bottom) - Math.max(left.top, right.top))
      }
      const queueRect = rect(queue)
      const evidenceRect = rect(evidence)
      const inspectorRect = rect(inspector)
      const footerRect = rect(footer)
      return {
        horizontalOverflow: documentElement.scrollWidth - documentElement.clientWidth,
        footerWidth: footerRect?.width ?? 0,
        footerReachable: Boolean(
          footerRect && footerRect.left >= 0 && footerRect.right <= window.innerWidth + 1 &&
          footerRect.top >= 0 && footerRect.bottom <= window.innerHeight + 1,
        ),
        workspaceWidth: workspace?.getBoundingClientRect().width ?? 0,
        imagePixels: (image?.naturalWidth ?? 0) * (image?.naturalHeight ?? 0),
        queueEvidenceOverlap: overlap(queueRect, evidenceRect),
        workspaceInspectorOverlap: overlap(rect(workspace), inspectorRect),
      }
    })
    expect(layout.horizontalOverflow).toBeLessThanOrEqual(1)
    expect(layout.footerWidth).toBeGreaterThan(0)
    expect(layout.workspaceWidth).toBeGreaterThan(0)
    expect(layout.imagePixels).toBeGreaterThan(0)
    expect(layout.footerReachable).toBe(true)
    expect(layout.queueEvidenceOverlap).toBe(0)
    expect(layout.workspaceInspectorOverlap).toBe(0)
  })
}
