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
const media = {
  crop_url: '/api/media/crop/1',
  original_front_url: '/api/media/original/front/1',
  original_back_url: '/api/media/original/back/1',
  annotated_front_url: '/api/media/annotated/front/1',
  annotated_back_url: '/api/media/annotated/back/1',
}
const items = Array.from({ length: 1000 }, (_, offset) => {
  const detailId = offset + 1
  const padded = String(detailId).padStart(4, '0')
  return {
    session_id: 7,
    result_id: detailId,
    detail_id: detailId,
    question_id: 'Q1',
    student_code: `S${padded}`,
    student_name:
      detailId === 1
        ? `学生${padded}·用于验证超长姓名在复核队列中保持单行且不会越过记录边界`
        : `学生${padded}`,
    class_name: detailId === 1 ? '七年级第一实验班（联合命题长班级名称）' : '七年级一班',
    score_awarded: detailId % 6,
    max_score: 5,
    deduction_reason: detailId === 1 ? '步骤依据需要人工核对' : null,
    error_category: null,
    error_summary:
      detailId === 1 ? '答案过程较长，需要对照评分标准确认关键步骤是否完整' : null,
    confidence_score: 60,
    needs_review: detailId % 2 === 1,
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
  await page.route(/\/api\/sessions$/, (route) =>
    fulfillJson(route, { items: [session], total: 1 }),
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

test('restores URL context and crosses the 100/101 boundary with keyboard navigation', async ({
  page,
}) => {
  const state: MockState = { questions: 'ready', items: 'ready' }
  await installReviewApi(page, state)
  await openReviewQueue(page, '/grading?question=Q1&detail=199&discard=me')

  await expect(page).toHaveURL(/\/grading\?question=Q1&detail=199$/)
  await expect(page.getByText('当前位置 100 / 1000')).toBeVisible()
  await expect(await selectedRow(page)).toContainText('学生0199')
  await expect(page.getByText('第 1 / 10 页')).toBeVisible()

  await page.keyboard.press('j')

  await expect(page).toHaveURL(/detail=201$/)
  await expect(page.getByText('当前位置 101 / 1000')).toBeVisible()
  await expect(await selectedRow(page)).toContainText('学生0201')
  await expect(page.getByText('第 2 / 10 页')).toBeVisible()

  await page.keyboard.press('k')
  await expect(page).toHaveURL(/detail=199$/)
  await expect(page.getByText('当前位置 100 / 1000')).toBeVisible()
})

test('search, needs-review filter, and risk sort keep a valid current item', async ({ page }) => {
  const state: MockState = { questions: 'ready', items: 'ready' }
  await installReviewApi(page, state)
  await openReviewQueue(page)

  const search = page.getByRole('searchbox', { name: '搜索学生' })
  await search.fill('学生00')
  await page.getByRole('combobox', { name: '复核范围' }).selectOption('needs_review')
  await page.getByRole('combobox', { name: '排序方式' }).selectOption('student_name')
  await page.getByRole('combobox', { name: '排序方式' }).selectOption('risk')

  await expect(await selectedRow(page)).toHaveCount(1)
  await expect(await selectedRow(page)).toHaveAttribute('aria-current', 'true')
  await expect(page.locator('.review-selection-summary')).toBeVisible()
  await expect(page.locator('.review-selection-summary')).toContainText('待复核')
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
  await expect(await selectedRow(page)).toContainText('学生0001')
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
  await expect(page.locator('.review-selection-summary')).toContainText('学生0001')

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
  await expect(page.locator('.review-selection-summary')).toContainText('学生0001')
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
    await openReviewQueue(page)
    await expect(page.locator('.review-selection-summary')).toBeVisible()

    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
      ),
      viewport.name,
    ).toBe(true)
    expect(await page.locator('.review-queue-row').count(), viewport.name).toBeLessThanOrEqual(100)
    await expect(await selectedRow(page), viewport.name).toHaveCount(1)
    await expect(await selectedRow(page), viewport.name).toHaveAttribute('aria-current', 'true')

    const longNameFits = await page.locator('.review-queue-row').first().evaluate((row) => {
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
