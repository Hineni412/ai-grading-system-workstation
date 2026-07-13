import { expect, test, type Page, type Route } from '@playwright/test'

const STORAGE_KEY = 'ai-grading:selected-session:v1'
const mediaPng = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFElEQVR42mP8z8Dwn4GBgYGJAQoAHgQCAfJQZ8sAAAAASUVORK5CYII=',
  'base64',
)
const session = {
  id: 7,
  name: '匿名评分验收',
  status: 'grading',
  is_deleted: false,
  deleted_at: null,
  created_at: '2026-07-13T00:00:00Z',
  updated_at: '2026-07-13T00:00:00Z',
}
const questions = [
  { question_id: 'Q1', total_count: 3, needs_review_count: 3, max_score: 5 },
]
const viewports = [
  { name: 'large desktop', width: 1920, height: 1080 },
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'standard workstation', width: 1366, height: 768 },
  { name: 'compact desktop', width: 1280, height: 800 },
  { name: 'minimum desktop', width: 1024, height: 768 },
]

const originalItems = Array.from({ length: 3 }, (_, offset) => {
  const detailId = offset + 1
  return {
    session_id: 7,
    result_id: detailId + 10,
    detail_id: detailId,
    question_id: 'Q1',
    student_code: `ANON-${detailId}`,
    student_name: `匿名学生${detailId}`,
    class_name: '匿名班级',
    score_awarded: 3,
    max_score: 5,
    deduction_reason: '步骤依据需要人工核对',
    error_category: '需复核',
    error_summary: '关键关系式需要教师确认',
    confidence_score: detailId * 10,
    needs_review: true,
    candidate_scores: [{ score: 2.5, confidence: 0.6, reason: '另一种判定' }],
    metadata: {
      evidence_steps: ['已写出变量'],
      missing_steps: ['尚未完整列出关系式'],
    },
    media: {
      crop_url: `/api/media/crop/${detailId}`,
      original_front_url: `/api/media/original/front/${detailId}`,
      original_back_url: `/api/media/original/back/${detailId}`,
      annotated_front_url: `/api/media/annotated/front/${detailId}`,
      annotated_back_url: `/api/media/annotated/back/${detailId}`,
    },
  }
})

type ConfirmMode = 'success' | 'retry' | 422 | 500

interface MockState {
  items: typeof originalItems
  confirmMode: ConfirmMode
  posts: unknown[]
}

function fulfillJson(route: Route, body: unknown, status = 200): Promise<void> {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

function freshState(confirmMode: ConfirmMode = 'success'): MockState {
  return {
    items: structuredClone(originalItems),
    confirmMode,
    posts: [],
  }
}

async function installApi(page: Page, state: MockState): Promise<void> {
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
        questions: [{
          question_id: 'Q1',
          max_score: 5,
          question_type: 'comprehensive',
          knowledge_points: [{ knowledge_name: '一次函数' }],
          core_goal: '根据题意列出正确关系式并说明每一步依据。'.repeat(12),
          required_elements: Array.from(
            { length: 8 },
            (_, index) => `评分要点 ${index + 1}：核对关系式、计算过程和结论。`,
          ),
        }],
      },
    }),
  )
  await page.route(/\/api\/sessions\/7\/review\/questions$/, (route) =>
    fulfillJson(route, { items: questions, total: questions.length }),
  )
  await page.route(
    /\/api\/sessions\/7\/review\/questions\/Q1\/items\?needs_review_only=false$/,
    (route) => fulfillJson(route, { items: state.items, total: state.items.length }),
  )
  await page.route(/\/api\/sessions\/7\/review\/questions\/Q1\/confirm$/, async (route) => {
    const body = route.request().postDataJSON() as {
      items?: Array<{ detail_id: number; score_awarded: number; deduction_reason?: string }>
    }
    state.posts.push(body)
    if (typeof state.confirmMode === 'number') {
      return fulfillJson(route, { detail: 'private validation or server detail' }, state.confirmMode)
    }
    const submitted = body.items?.[0]
    if (submitted) {
      state.items = state.items.map((entry) => entry.detail_id === submitted.detail_id
        ? {
            ...entry,
            score_awarded: submitted.score_awarded,
            deduction_reason: submitted.deduction_reason ?? '人工复核已确认',
            error_category: '已复核',
            error_summary: 'manual_review_confirmed',
            needs_review: false,
          }
        : entry)
    }
    return fulfillJson(route, {
      updated_details: 1,
      updated_results: 1,
      annotation_outcomes: state.confirmMode === 'retry'
        ? [{ result_id: submitted?.detail_id ?? 1, status: 'retry_required' }]
        : [],
    })
  })
}

async function openScoring(page: Page, detailId = 1): Promise<void> {
  await page.addInitScript(([key]) => localStorage.setItem(key, '7'), [STORAGE_KEY])
  await page.goto(`/grading?question=Q1&detail=${detailId}`)
  await expect(page.getByRole('heading', { name: '复核队列', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: '评分与复核', exact: true })).toBeVisible()
  await expect(page.getByText('一次函数', { exact: true })).toBeVisible()
}

test('preserves drafts across records, blocks invalid scores and confirms one item safely', async ({ page }) => {
  const state = freshState('retry')
  await installApi(page, state)
  await openScoring(page)

  await expect(page.getByRole('heading', { name: '评分标准' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'AI 初评' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '风险与错因' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '教师最终分' })).toBeVisible()

  const score = page.getByLabel('最终得分')
  const note = page.getByLabel('教师备注')
  await score.fill('4')
  await note.fill('教师核对步骤后调整')
  await page.locator('.review-queue-row').filter({ hasText: '匿名学生2' }).click()
  await page.locator('.review-queue-row').filter({ hasText: '匿名学生1' }).click()
  await expect(score).toHaveValue('4')
  await expect(note).toHaveValue('教师核对步骤后调整')

  await score.fill('6')
  await expect(page.getByText('教师最终分不能超过 5 分')).toBeVisible()
  await expect(page.getByRole('button', { name: '确认并下一份' })).toBeDisabled()
  expect(state.posts).toHaveLength(0)

  await score.fill('4')
  await score.press('j')
  await expect(page).toHaveURL(/detail=1$/)
  await page.getByRole('button', { name: '确认并下一份' }).click()

  await expect(page).toHaveURL(/detail=2$/)
  expect(state.posts).toHaveLength(1)
  expect(state.posts[0]).toEqual({
    items: [{
      result_id: 11,
      detail_id: 1,
      score_awarded: 4,
      deduction_reason: '教师核对步骤后调整',
    }],
  })
  await expect(page.getByText('分数已确认，标注图需要稍后刷新。')).toBeVisible()
})

test('click replaces the old score, Enter confirms next, and Shift+Enter stays inactive', async ({ page }) => {
  const state = freshState()
  await installApi(page, state)
  await openScoring(page)

  const score = page.getByLabel('最终得分')
  await score.click()
  await page.keyboard.type('4')
  await expect(score).toHaveValue('4')
  await score.press('Enter')
  await expect.poll(() => state.posts.length).toBe(1)
  await expect(page).toHaveURL(/detail=2$/)

  await score.fill('4.5')
  const canvas = page.locator('.review-evidence-canvas')
  await canvas.focus()
  await canvas.press('Shift+Enter')
  await expect(page).toHaveURL(/detail=2$/)
  expect(state.posts).toHaveLength(1)

  await canvas.press('Enter')
  await expect.poll(() => state.posts.length).toBe(2)
  await expect(page).toHaveURL(/detail=3$/)

  await page.keyboard.press('r')
  await expect.poll(() => state.posts.length).toBe(2)
  await expect(page).toHaveURL(/detail=3$/)
})

for (const status of [422, 500] as const) {
  test(`HTTP ${status} keeps the teacher draft and does not expose server details`, async ({ page }) => {
    const state = freshState(status)
    await installApi(page, state)
    await openScoring(page)
    await page.getByLabel('最终得分').fill('4.5')
    await page.getByLabel('教师备注').fill(`失败保留 ${status}`)
    await page.getByTestId('scoring-scroll-region').evaluate((element) => {
      element.scrollTop = element.scrollHeight
    })
    await page.getByRole('button', { name: '确认并下一份' }).click()

    const toast = page.getByTestId('review-feedback-toast')
    await expect(toast).toBeVisible()
    await expect(toast).toContainText('确认失败，教师草稿已保留')
    await expect(toast).not.toContainText('private')
    expect(await toast.evaluate((element) => element.parentElement === document.body)).toBe(true)
    const stickySince = Date.now()
    await expect.poll(
      () => Date.now() - stickySince,
      { intervals: [4100], timeout: 5000 },
    ).toBeGreaterThanOrEqual(4000)
    await expect(toast).toBeVisible()
    await expect(page.getByLabel('最终得分')).toHaveValue('4.5')
    await expect(page.getByLabel('教师备注')).toHaveValue(`失败保留 ${status}`)
    await expect(page).toHaveURL(/detail=1$/)
    expect(state.posts).toHaveLength(1)
  })
}

test('keeps scoring reachable with a stable footer and no layout errors at five desktop sizes', async ({ page }) => {
  const state = freshState()
  const pageErrors: Error[] = []
  const consoleErrors: string[] = []
  page.on('pageerror', (error) => pageErrors.push(error))
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  await installApi(page, state)

  for (const viewport of viewports) {
    await page.setViewportSize(viewport)
    await openScoring(page)
    const scroll = page.getByTestId('scoring-scroll-region')
    const footer = page.getByTestId('scoring-footer')
    const button = page.getByRole('button', { name: '确认并下一份' })
    await expect(scroll, viewport.name).toBeInViewport()
    await expect(footer, viewport.name).toBeInViewport()
    await expect(button, viewport.name).toBeInViewport()
    expect(await page.evaluate(() =>
      document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    ), viewport.name).toBe(true)

    const before = await footer.boundingBox()
    const hasScrollableRubric = await scroll.evaluate((element) =>
      element.scrollHeight > element.clientHeight,
    )
    expect(hasScrollableRubric, viewport.name).toBe(true)
    await scroll.evaluate((element) => { element.scrollTop = element.scrollHeight })
    const after = await footer.boundingBox()
    expect(after, viewport.name).toEqual(before)
    await expect(button, viewport.name).toBeInViewport()

    const noColumnOverlap = await page.evaluate(() => {
      const queue = document.querySelector<HTMLElement>('.review-queue-panel')
      const evidence = document.querySelector<HTMLElement>('.review-evidence-viewer')
      const scoring = document.querySelector<HTMLElement>('.review-scoring-inspector')
      if (!queue || !evidence || !scoring) return false
      const queueBox = queue.getBoundingClientRect()
      const evidenceBox = evidence.getBoundingClientRect()
      const scoringBox = scoring.getBoundingClientRect()
      return queueBox.right <= evidenceBox.left + 1 && evidenceBox.right <= scoringBox.left + 1
    })
    expect(noColumnOverlap, viewport.name).toBe(true)
  }

  expect(pageErrors).toEqual([])
  expect(consoleErrors).toEqual([])
})
