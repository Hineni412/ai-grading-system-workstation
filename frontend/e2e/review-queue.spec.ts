import { expect, type Page, type Route } from '@playwright/test';
import { test } from './mock-fixtures'

const STORAGE_KEY = 'ai-grading:selected-session:v1'
const session = {
  id: 7,
  name: '七年级数学期末质量监测',
  status: 'grading',
  curriculum_volume_id: null,
  is_deleted: false,
  deleted_at: null,
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
}
const questions = [
  { question_id: 'Q1', question_type: null, total_count: 1000, needs_review_count: 500, max_score: 5 },
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
  await page.route(/\/api\/sessions\/7\/review\/questions\/Q1\/rubric$/, route => route.fulfill({ json: null }))
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
  await page.route(/\/api\/sessions\/7\/review\/questions(?:\?.*)?$/, (route) => {
    if (state.questions === 'error') {
      return route.fulfill({ status: 503, body: 'private question failure' })
    }
    const responseItems = state.questions === 'empty' ? [] : questions
    return fulfillJson(route, { items: responseItems, total: responseItems.length })
  })
  await page.route(
    /\/api\/sessions\/7\/review\/questions\/Q1\/items\?(?:needs_review_only=false|scope=all)$/,
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
  await expect(page.getByRole('heading', { name: '人工干预工作台', exact: true })).toBeVisible()
}

test('restores URL context and crosses the 100/101 boundary with keyboard navigation', async ({
  page,
}) => {
  const state: MockState = { questions: 'ready', items: 'ready' }
  await page.setViewportSize({ width: 1024, height: 768 })
  await installReviewApi(page, state)
  await openReviewQueue(page, '/grading?question=Q1&detail=201&discard=me')
  const evidenceViewer = page.locator('.review-evidence-viewer')

  await expect(page).toHaveURL(/\/grading\?scope=all&session=7&question=Q1&item=7:Q1:201$/)
  await expect(page.getByText('第 100 / 1000 人')).toBeVisible()
  await expect(page.getByRole('heading', { name: '学生0201', exact: true })).toBeVisible()

  await page.keyboard.press('j')

  await expect(page).toHaveURL(/item=7:Q1:203$/)
  await expect(page.getByText('第 101 / 1000 人')).toBeVisible()
  await expect(page.getByRole('heading', { name: '学生0203', exact: true })).toBeVisible()
  await expect(evidenceViewer).toBeInViewport()

  await page.keyboard.press('k')
  await expect(page).toHaveURL(/item=7:Q1:201$/)
  await expect(page.getByText('第 100 / 1000 人')).toBeVisible()
  await expect(evidenceViewer).toBeInViewport()
  await page.getByRole('button', { name: '返回Q1 批量复核' }).click()
  await expect(page.getByText('第 5 / 42 批')).toBeVisible()
  await expect(page.getByTestId('review-answer-sheet').first()).toContainText('学生0195')
  await page.getByRole('button', { name: '下一批 ›' }).click()
  await expect(page.getByText('第 6 / 42 批')).toBeVisible()
  await expect(page.getByTestId('review-answer-sheet').first()).toContainText('学生0243')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
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
  await expect(page.getByTestId('review-answer-sheet').first()).toContainText('Zulu Risk')
  await expect(page.getByTestId('review-batch-workspace')).toBeVisible()
})
