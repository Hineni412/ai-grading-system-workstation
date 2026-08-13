import { expect, test, type Page, type Route } from '@playwright/test'

const STORAGE_KEY = 'ai-grading:selected-session:v1'
const session = {
  id: 7,
  name: '七年级数学期末质量监测',
  status: 'grading',
  is_deleted: false,
  deleted_at: null,
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
}
const questions = [
  { question_id: 'Q1', total_count: 1000, needs_review_count: 500, max_score: 5 },
]
const continuousReviewText = 'A'.repeat(512)
const media = {
  crop_url: '/api/media/crop/1',
  original_front_url: '/api/media/original/front/1',
  original_back_url: '/api/media/original/back/1',
  annotated_front_url: '/api/media/annotated/front/1',
  annotated_back_url: '/api/media/annotated/back/1',
}
const mediaPng = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFElEQVR42mP8z8Dwn4GBgYGJAQoAHgQCAfJQZ8sAAAAASUVORK5CYII=',
  'base64',
)
const focusCases: Record<
  number,
  {
    studentCode: string
    studentName: string
    confidence: number
    needsReview: boolean
  }
> = {
  1: {
    studentCode: 'FOCUS-900',
    studentName: 'Alpha Focus',
    confidence: 90,
    needsReview: true,
  },
  2: {
    studentCode: 'FOCUS-100',
    studentName: 'Beta Normal',
    confidence: 10,
    needsReview: false,
  },
  3: {
    studentCode: 'FOCUS-500',
    studentName: 'Zulu Risk',
    confidence: 5,
    needsReview: true,
  },
  4: {
    studentCode: 'FOCUS-200',
    studentName: 'Gamma Normal',
    confidence: 1,
    needsReview: false,
  },
}
const items = Array.from({ length: 1000 }, (_, offset) => {
  const detailId = offset + 1
  const padded = String(detailId).padStart(4, '0')
  const focusCase = focusCases[detailId]
  return {
    session_id: 7,
    result_id: detailId,
    detail_id: detailId,
    question_id: 'Q1',
    student_code: focusCase?.studentCode ?? `S${padded}`,
    student_name:
      focusCase?.studentName ??
      (detailId === 5
          ? `学生${padded}·用于验证超长姓名在复核队列中保持单行且不会越过记录边界`
          : `学生${padded}`),
    class_name: focusCase ? '重点组' : '七年级一班',
    score_awarded: detailId % 6,
    max_score: 5,
    deduction_reason: detailId === 5 ? '步骤依据需要人工核对' : null,
    error_category: null,
    error_summary:
      detailId === 5 ? continuousReviewText : null,
    confidence_score: focusCase?.confidence ?? 60,
    needs_review: focusCase?.needsReview ?? detailId % 2 === 1,
    candidate_scores: [],
    metadata: {},
    media,
  }
})

const viewports = [
  { name: 'large desktop', width: 1920, height: 1080 },
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'standard workstation', width: 1366, height: 768 },
  { name: 'compact desktop', width: 1280, height: 800 },
  { name: 'minimum desktop', width: 1024, height: 768 },
]

type ResponseMode = 'ready' | 'empty' | 'error'

interface MockState {
  questions: ResponseMode
  items: Exclude<ResponseMode, 'empty'>
}

function fulfillJson(route: Route, body: unknown): Promise<void> {
  return route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

async function installReviewApi(page: Page, state: MockState): Promise<void> {
  await page.route(/\/api\/media\//, (route) => route.fulfill({
    status: 200,
    contentType: 'image/png',
    headers: { 'Cache-Control': 'no-store' },
    body: mediaPng,
  }))
  await page.route(/\/api\/sessions$/, (route) =>
    fulfillJson(route, { items: [session], total: 1 }),
  )
  await page.route(/\/api\/sessions\/7\/config$/, (route) =>
    fulfillJson(route, {
      rubric: {
        questions: [{ question_id: 'Q1', max_score: 5, core_goal: '核对解题步骤' }],
      },
    }),
  )
  await page.route(/\/api\/sessions\/7\/review\/questions$/, (route) => {
    if (state.questions === 'error') {
      return route.fulfill({ status: 503, body: 'private question failure' })
    }
    const responseItems = state.questions === 'empty' ? [] : questions
    return fulfillJson(route, { items: responseItems, total: responseItems.length })
  })
  await page.route(
    /\/api\/sessions\/7\/review\/questions\/Q1\/items\?needs_review_only=false$/,
    (route) => {
      if (state.items === 'error') {
        return route.fulfill({ status: 503, body: 'private item failure' })
      }
      return fulfillJson(route, { items, total: items.length })
    },
  )
}

async function openReviewQueue(page: Page, path = '/grading'): Promise<void> {
  await page.addInitScript(([key]) => localStorage.setItem(key, '7'), [STORAGE_KEY])
  await page.goto(path)
  await expect(page.getByRole('heading', { name: '复核队列', exact: true })).toBeVisible()
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

async function selectedRow(page: Page) {
  return page.locator('.review-queue-row[aria-current="true"]')
}

async function scrollPositions(page: Page) {
  return page.evaluate(() => ({
    page: document.scrollingElement?.scrollTop ?? 0,
    workspace: document.querySelector<HTMLElement>('#main-workspace')?.scrollTop ?? 0,
    queue: document.querySelector<HTMLElement>('.review-queue-list')?.scrollTop ?? 0,
  }))
}

test('restores URL context and crosses the 100/101 boundary with keyboard navigation', async ({
  page,
}) => {
  const state: MockState = { questions: 'ready', items: 'ready' }
  await page.setViewportSize({ width: 1024, height: 768 })
  await installReviewApi(page, state)
  await openReviewQueue(page, '/grading?question=Q1&detail=201&discard=me')
  const evidenceViewer = page.locator('.review-evidence-viewer')

  await expect(page).toHaveURL(/\/grading\?question=Q1&detail=201$/)
  await expect(page.getByText('当前位置 100 / 1000')).toBeVisible()
  await expect(await selectedRow(page)).toContainText('学生0201')
  await expect(page.getByText('第 1 / 10 页')).toBeVisible()
  const initialScroll = await scrollPositions(page)

  await page.keyboard.press('j')

  await expect(page).toHaveURL(/detail=203$/)
  await expect(page.getByText('当前位置 101 / 1000')).toBeVisible()
  await expect(await selectedRow(page)).toContainText('学生0203')
  await expect(await selectedRow(page)).toBeInViewport()
  await expect(evidenceViewer).toBeInViewport()
  await expect(page.getByText('第 2 / 10 页')).toBeVisible()
  const pageTwoScroll = await scrollPositions(page)

  await page.keyboard.press('k')
  await expect(page).toHaveURL(/detail=201$/)
  await expect(page.getByText('当前位置 100 / 1000')).toBeVisible()
  await expect(await selectedRow(page)).toBeInViewport()
  await expect(evidenceViewer).toBeInViewport()
  const returnedPageOneScroll = await scrollPositions(page)

  expect(initialScroll.page).toBe(0)
  expect(pageTwoScroll.page).toBe(0)
  expect(returnedPageOneScroll.page).toBe(0)
  expect(initialScroll.workspace).toBe(0)
  expect(pageTwoScroll.workspace).toBe(0)
  expect(returnedPageOneScroll.workspace).toBe(0)
  expect(initialScroll.queue).toBeGreaterThan(pageTwoScroll.queue)
  expect(returnedPageOneScroll.queue).toBeGreaterThan(pageTwoScroll.queue)
})

test('search, needs-review filter, and risk sort keep a valid current item', async ({ page }) => {
  const state: MockState = { questions: 'ready', items: 'ready' }
  await installReviewApi(page, state)
  await openReviewQueue(page)

  const search = page.getByRole('searchbox', { name: '搜索学生' })
  const names = page.locator('.review-queue-row__name')
  await search.fill('重点组')
  await expect(page.getByText('共 4 条', { exact: true })).toBeVisible()
  await expect(names).toHaveText(['Zulu Risk', 'Alpha Focus', 'Gamma Normal', 'Beta Normal'])

  await page.locator('.review-queue-row').filter({ hasText: 'Beta Normal' }).click()
  await expect(page).toHaveURL(/detail=2$/)
  await expect(page.getByRole('heading', { name: 'Beta Normal' })).toBeVisible()

  await page.getByRole('combobox', { name: '复核范围' }).selectOption('needs_review')
  await expect(page.getByText('共 2 条', { exact: true })).toBeVisible()
  await expect(names).toHaveText(['Zulu Risk', 'Alpha Focus'])
  await expect(page.getByText('Beta Normal', { exact: true })).toHaveCount(0)
  await expect(page).toHaveURL(/detail=3$/)
  await expect(page.getByRole('heading', { name: 'Zulu Risk' })).toBeVisible()

  await page.getByRole('combobox', { name: '排序方式' }).selectOption('student_name')
  await expect(names).toHaveText(['Alpha Focus', 'Zulu Risk'])
  await expect(page).toHaveURL(/detail=3$/)

  await page.getByRole('combobox', { name: '排序方式' }).selectOption('risk')
  await expect(names).toHaveText(['Zulu Risk', 'Alpha Focus'])

  await expect(await selectedRow(page)).toHaveCount(1)
  await expect(await selectedRow(page)).toHaveAttribute('aria-current', 'true')
  await expect(await selectedRow(page)).toContainText('Zulu Risk')
  await expect(page.getByRole('heading', { name: 'Zulu Risk' })).toBeVisible()
  await expect(page).toHaveURL(/detail=3$/)
  await expect(page.getByText(/当前位置 1 \/ \d+/)).toBeVisible()
})

test('input focus suppresses J/K navigation', async ({ page }) => {
  const state: MockState = { questions: 'ready', items: 'ready' }
  await installReviewApi(page, state)
  await openReviewQueue(page)

  const search = page.getByRole('searchbox', { name: '搜索学生' })
  await search.focus()
  const initialUrl = page.url()

  await search.dispatchEvent('keydown', { key: 'j' })
  await search.dispatchEvent('keydown', { key: 'k' })

  await expect(search).toBeFocused()
  await expect(search).toHaveValue('')
  expect(page.url()).toBe(initialUrl)
  await expect(await selectedRow(page)).toHaveAttribute('aria-current', 'true')
  await expect(await selectedRow(page)).toContainText('Zulu Risk')
})

test('first-load, retained-content error, no-questions, and filtered-empty states are actionable', async ({
  page,
}) => {
  const state: MockState = { questions: 'error', items: 'ready' }
  await installReviewApi(page, state)
  await openReviewQueue(page)

  await expect(page.getByRole('heading', { name: '复核队列加载失败' })).toBeVisible()
  await expect(page.getByRole('button', { name: '重新加载' })).toBeVisible()

  state.questions = 'empty'
  await page.getByRole('button', { name: '重新加载' }).click()
  await expect(page.getByRole('heading', { name: '当前考试没有复核题目' })).toBeVisible()

  state.questions = 'ready'
  await page.reload()
  await expect(page.locator('.review-selection-summary')).toContainText('Zulu Risk')

  state.items = 'error'
  await page.evaluate(async () => {
    type ReviewStore = { loadItems: (sessionId: number, questionId: string) => Promise<void> }
    type PiniaLike = { _s?: Map<string, ReviewStore> }
    type VueAppHost = HTMLElement & {
      __vue_app__?: { _context: { provides: Record<PropertyKey, unknown> } }
    }
    const host = document.querySelector('#app') as VueAppHost | null
    const providers = host?.__vue_app__?._context.provides
    const pinia = providers
      ? Reflect.ownKeys(providers)
          .map((key) => providers[key])
          .find((value): value is PiniaLike => {
            return typeof value === 'object' && value !== null && '_s' in value
          })
      : undefined
    const store = pinia?._s?.get('review-queue')
    if (!store) throw new Error('review queue store is unavailable')
    await store.loadItems(7, 'Q1')
  })
  await expect(page.getByText('复核内容刷新失败')).toBeVisible()
  await expect(page.locator('.review-selection-summary')).toContainText('Zulu Risk')
  await expect(page.getByRole('button', { name: '重新加载' })).toBeVisible()

  state.items = 'ready'
  await page.getByRole('button', { name: '重新加载' }).click()
  await expect(page.getByText('复核内容刷新失败')).toHaveCount(0)

  await page.getByRole('searchbox', { name: '搜索学生' }).fill('不存在的学生')
  await expect(page.getByRole('heading', { name: '当前筛选没有记录' })).toBeVisible()
  await expect(page.getByText('可以调整搜索词或复核范围。')).toBeVisible()
  await expect(page.getByRole('searchbox', { name: '搜索学生' })).toBeEnabled()
})

test('1000-row queue has no horizontal overflow or console errors at all five desktop viewports', async ({
  page,
}) => {
  const state: MockState = { questions: 'ready', items: 'ready' }
  const errors = trackBrowserErrors(page)
  await installReviewApi(page, state)

  for (const viewport of viewports) {
    await page.setViewportSize(viewport)
    await openReviewQueue(page, '/grading?question=Q1&detail=5')
    await expect(page.locator('.review-selection-summary')).toBeVisible()

    const longDetail = page.locator('.review-selection-summary dd', {
      hasText: continuousReviewText,
    })
    await expect(longDetail).toBeVisible()
    const continuousTextMetrics = await longDetail.evaluate((element) => {
      const summary = element.closest<HTMLElement>('.review-selection-summary')
      if (!summary) return { documentFits: false, textFits: false }
      const detailBounds = element.getBoundingClientRect()
      const summaryBounds = summary.getBoundingClientRect()
      return {
        documentFits:
          document.documentElement.scrollWidth <= document.documentElement.clientWidth,
        textFits:
          element.scrollWidth <= element.clientWidth &&
          detailBounds.left >= summaryBounds.left &&
          detailBounds.right <= summaryBounds.right,
      }
    })
    expect(continuousTextMetrics, viewport.name).toEqual({
      documentFits: true,
      textFits: true,
    })
    expect(await page.locator('.review-queue-row').count(), viewport.name).toBeLessThanOrEqual(100)
    await expect(await selectedRow(page), viewport.name).toHaveCount(1)
    await expect(await selectedRow(page), viewport.name).toHaveAttribute('aria-current', 'true')

    const longNameFits = await page
      .locator('.review-queue-row')
      .filter({ hasText: '学生0005' })
      .evaluate((row) => {
        const name = row.querySelector<HTMLElement>('.review-queue-row__name')
        if (!name) return false
        const rowBounds = row.getBoundingClientRect()
        const nameBounds = name.getBoundingClientRect()
        return (
          nameBounds.left >= rowBounds.left &&
          nameBounds.right <= rowBounds.right &&
          getComputedStyle(name).textOverflow === 'ellipsis'
        )
      })
    expect(longNameFits, viewport.name).toBe(true)
  }

  expect(errors.pageErrors).toEqual([])
  expect(errors.consoleErrors).toEqual([])
})
