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

async function openBatch(page: Page) {
  await page.addInitScript(([key]) => localStorage.setItem(key, '7'), [STORAGE_KEY])
  await page.goto(`${ORIGIN}/grading?question=Q1`)
  await expect(page.getByRole('heading', { name: '评分复核', exact: true })).toBeVisible()
  await expect(page.getByTestId('review-batch-workspace')).toBeVisible()
  await expect(page.getByTestId('review-answer-sheet')).toHaveCount(2)
}

test.beforeEach(async ({ request }) => {
  const response = await request.post(`${ORIGIN}/__p2_08__/reset`, { data: {} })
  expect(response.ok()).toBe(true)
})

test('starts in the truthful question-level batch workspace and confirms two answers once', async ({ page }) => {
  const confirmBodies: unknown[] = []
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().endsWith('/confirm')) {
      confirmBodies.push(request.postDataJSON())
    }
  })
  await openBatch(page)

  await expect(page.getByLabel('批量复核快捷键')).toContainText('J / K')
  await expect(page.getByLabel('批量复核快捷键')).not.toContainText('Enter')
  await expect(page.getByTestId('teacher-score-1')).toHaveValue('3')
  await page.getByTestId('teacher-score-3').fill('3')
  await page.getByTestId('confirm-batch').click()

  await expect.poll(() => confirmBodies.length).toBe(1)
  expect(confirmBodies[0]).toEqual({ items: [
    { result_id: 103, detail_id: 3, score_awarded: 3 },
    { result_id: 101, detail_id: 1, score_awarded: 3 },
  ] })
  await expect(page.getByText('本批 2 份评分已确认。', { exact: true })).toBeVisible()
})

test('invalid and failed batches retain every draft and never replay writes automatically', async ({ page }) => {
  let confirmRequests = 0
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().endsWith('/confirm')) confirmRequests += 1
  })
  await openBatch(page)
  await page.getByTestId('teacher-score-1').fill('8')
  await expect(page.getByText('教师最终分不能超过 5 分')).toBeVisible()
  await expect(page.getByTestId('confirm-batch')).toBeDisabled()
  expect(confirmRequests).toBe(0)

  await page.getByTestId('teacher-score-1').fill('4.5')
  await page.getByTestId('teacher-score-3').fill('3')
  await setMode(page, { confirm: '500' })
  await page.getByTestId('confirm-batch').click()
  await expect(page.getByTestId('review-feedback-toast')).toContainText('整批草稿已保留')
  await expect(page.getByTestId('teacher-score-1')).toHaveValue('4.5')
  await expect(page.getByTestId('teacher-score-3')).toHaveValue('3')
  expect(confirmRequests).toBe(1)
  await page.waitForTimeout(250)
  expect(confirmRequests).toBe(1)
})

test('annotation retry is explicit and never reverts the saved scores', async ({ page }) => {
  let confirmRequests = 0
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().endsWith('/confirm')) confirmRequests += 1
  })
  await openBatch(page)
  await setMode(page, { confirm: 'retry' })
  await page.getByTestId('teacher-score-1').fill('4.5')
  await page.getByTestId('confirm-batch').click()
  await expect(page.getByText('分数已保存，2 份标注图需要重试', { exact: true })).toBeVisible()
  expect(confirmRequests).toBe(1)

  await setMode(page, { confirm: 'success' })
  await page.getByRole('button', { name: '重试标注图' }).click()
  await expect(page.getByText('标注图已重新生成。', { exact: true })).toBeVisible()
  expect(confirmRequests).toBe(2)

  const all = await page.request.get(
    `${ORIGIN}/api/sessions/7/review/questions/Q1/items?needs_review_only=false`,
  )
  const body = await all.json()
  expect(body.items.find((item: { detail_id: number }) => item.detail_id === 1).score_awarded).toBe(4.5)
})

test('deep review replaces the batch body, exposes all evidence, and restores exact batch context', async ({ page }) => {
  await openBatch(page)
  await page.getByTestId('review-search').fill('一号')
  await page.getByTestId('teacher-score-1').fill('4.25')
  await page.getByRole('button', { name: '深查此份答卷' }).click()

  await expect(page.getByTestId('review-deep-workspace')).toBeVisible()
  await expect(page.getByTestId('review-batch-workspace')).toHaveCount(0)
  await expect(page).toHaveURL(/question=Q1&detail=1$/)
  for (const source of ['裁剪证据', '原卷正面', '原卷反面', '标注正面', '标注反面']) {
    await expect(page.getByRole('button', { name: source })).toBeVisible()
  }
  await page.getByRole('button', { name: '标注反面' }).click()
  await expect(page.locator('.review-evidence-canvas img')).toHaveAttribute(
    'src',
    '/api/media/annotated/back/1',
  )

  await page.getByTestId('back-to-batch').click()
  await expect(page.getByTestId('review-batch-workspace')).toBeVisible()
  await expect(page.getByTestId('review-deep-workspace')).toHaveCount(0)
  await expect(page.getByTestId('review-search')).toHaveValue('一号')
  await expect(page.getByTestId('teacher-score-1')).toHaveValue('4.25')
  await expect(page).toHaveURL(/grading\?question=Q1$/)
})

test('keyboard focus stays safe and media failure remains locally recoverable', async ({ page }) => {
  await openBatch(page)
  await page.keyboard.press('/')
  await expect(page.getByTestId('review-search')).toBeFocused()
  await page.getByTestId('teacher-score-1').focus()
  await page.keyboard.press('j')
  await expect(page.getByTestId('teacher-score-1')).toBeFocused()

  await page.route('**/api/media/crop/1', async (route) => route.fulfill({ status: 500 }))
  await page.reload()
  await expect(page.getByText('答卷图片暂时无法显示')).toBeVisible()
  await page.unroute('**/api/media/crop/1')
  await page.getByRole('button', { name: '重新加载答卷图片' }).click()
  await expect(page.getByTestId('answer-crop-1')).toBeVisible()
})

test('loading, retained error, first error, empty, and no-pending states remain actionable', async ({ page }) => {
  await setMode(page, { items: 'slow' })
  await page.addInitScript(([key]) => localStorage.setItem(key, '7'), [STORAGE_KEY])
  await page.goto(`${ORIGIN}/grading?question=Q1`)
  await expect(page.getByText('正在读取本题答卷', { exact: true })).toBeVisible()
  await expect(page.getByTestId('review-batch-workspace')).toBeVisible()

  await setMode(page, { items: 'error' })
  await page.reload()
  await expect(page.getByText('复核内容加载失败', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '重新加载' })).toBeVisible()

  await setMode(page, { items: 'empty' })
  await page.getByRole('button', { name: '重新加载' }).click()
  await expect(page.getByText('当前范围没有答卷', { exact: true })).toBeVisible()

  await page.request.post(`${ORIGIN}/__p2_08__/reset`, { data: {} })
  await page.reload()
  await setMode(page, { items: 'error' })
  await page.getByLabel('显示范围').selectOption('all')
  await expect(page.getByText('复核内容刷新失败', { exact: true })).toBeVisible()
  await expect(page.getByTestId('review-answer-sheet')).toHaveCount(2)

  await setMode(page, { items: 'ready' })
  await page.getByLabel('显示范围').selectOption('needs_review')
  await expect(page.getByTestId('review-answer-sheet')).toHaveCount(2)
  await page.getByTestId('confirm-batch').click()
  await expect(page.getByTestId('review-feedback-toast')).toContainText('本批 2 份评分已确认')
  await page.getByRole('button', { name: /Q1/ }).click()
  await expect(page.getByText('当前范围没有答卷', { exact: true })).toBeVisible()
})

test('refresh warns about dirty drafts, while saved runtime state persists until reset', async ({ page }) => {
  await openBatch(page)
  await page.getByTestId('teacher-score-1').fill('4.75')
  let dialogType = ''
  page.once('dialog', async (dialog) => {
    dialogType = dialog.type()
    await dialog.accept()
  })
  await page.reload()
  expect(dialogType).toBe('beforeunload')
  await expect(page.getByTestId('teacher-score-1')).toHaveValue('3')

  await page.getByTestId('teacher-score-1').fill('4.5')
  await page.getByTestId('confirm-batch').click()
  await page.goto(`${ORIGIN}/grading?question=Q1`)
  await page.getByLabel('显示范围').selectOption('all')
  await expect(page.getByTestId('teacher-score-1')).toHaveValue('4.5')

  await page.request.post(`${ORIGIN}/__p2_08__/reset`, { data: {} })
  await page.reload()
  await page.getByLabel('显示范围').selectOption('all')
  await expect(page.getByTestId('teacher-score-1')).toHaveValue('3')
})

for (const viewport of viewports) {
  test(`${viewport.name} has no document overflow, clipping, or batch/deep overlap`, async ({ page }) => {
    const consoleErrors: string[] = []
    page.on('console', (message) => {
      if (message.type() === 'error') consoleErrors.push(message.text())
    })
    await page.setViewportSize(viewport)
    await openBatch(page)
    await expect(page.getByTestId('confirm-batch')).toBeVisible()
    await page.getByRole('button', { name: '深查此份答卷' }).first().click()
    await expect(page.getByTestId('review-deep-workspace')).toBeVisible()

    const geometry = await page.evaluate(() => {
      const evidence = document.querySelector<HTMLElement>('.review-deep-workspace__evidence')!
      const scoring = document.querySelector<HTMLElement>('.review-deep-workspace__scoring')!
      const evidenceRect = evidence.getBoundingClientRect()
      const scoringRect = scoring.getBoundingClientRect()
      const overlapWidth = Math.max(
        0,
        Math.min(evidenceRect.right, scoringRect.right) - Math.max(evidenceRect.left, scoringRect.left),
      )
      const overlapHeight = Math.max(
        0,
        Math.min(evidenceRect.bottom, scoringRect.bottom) - Math.max(evidenceRect.top, scoringRect.top),
      )
      return {
        overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        overlap: overlapWidth * overlapHeight,
        batchPresent: Boolean(document.querySelector('[data-testid="review-batch-workspace"]')),
      }
    })
    expect(geometry).toEqual({ overflow: 0, overlap: 0, batchPresent: false })
    expect(consoleErrors).toEqual([])
  })
}
