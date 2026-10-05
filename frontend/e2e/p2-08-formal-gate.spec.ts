import { expect, test, type Page } from '@playwright/test';

const STORAGE_KEY = 'ai-grading:selected-session:v1'

async function setMode(page: Page, mode: Record<string, string>) {
  const response = await page.request.post('/__p2_08__/mode', { data: mode })
  expect(response.ok()).toBe(true)
}

async function openBatch(page: Page) {
  await page.addInitScript(([key]) => localStorage.setItem(key, '7'), [STORAGE_KEY])
  await page.goto('/grading?question=Q1')
  await expect(page.getByRole('heading', { name: '人工干预工作台', exact: true })).toBeVisible()
  await expect(page.getByTestId('review-batch-workspace')).toBeVisible()
  await expect(page.getByTestId('review-answer-sheet')).toHaveCount(4)
  await page.getByLabel('显示范围').selectOption('ai_review')
  await expect(page.getByTestId('review-answer-sheet')).toHaveCount(2)
}

test.beforeEach(async ({ request }) => {
  const response = await request.post('/__p2_08__/reset', { data: {} })
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

  await expect(page.locator('.review-batch-shortcuts')).toContainText(/J\s*\/\s*K/)
  await expect(page.locator('.review-batch-shortcuts')).toContainText('Enter')
  await expect(page.locator('[data-detail-id="1"] [data-score-position]')).toHaveValue('3')
  await page.locator('[data-detail-id="3"] [data-score-position]').fill('3')
  await page.getByTestId('confirm-batch').click()

  await expect.poll(() => confirmBodies.length).toBe(1)
  expect(confirmBodies[0]).toMatchObject({ annotation_mode: 'on_demand', items: [
    { result_id: 103, detail_id: 3, score_awarded: 3 },
    { result_id: 101, detail_id: 1, score_awarded: 3 },
  ] })
  await expect(page.getByTestId('review-feedback-toast')).toContainText('2')
})

test('invalid and failed batches retain every draft and never replay writes automatically', async ({ page }) => {
  let confirmRequests = 0
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().endsWith('/confirm')) confirmRequests += 1
  })
  await openBatch(page)
  await page.locator('[data-detail-id="1"] [data-score-position]').fill('8')
  await expect(page.getByText('教师最终分不能超过 5 分')).toBeVisible()
  await expect(page.getByTestId('confirm-batch')).toBeDisabled()
  expect(confirmRequests).toBe(0)

  await page.locator('[data-detail-id="1"] [data-score-position]').fill('4.5')
  await expect(page.locator('[data-detail-id="1"]').getByText('教师最终分必须是整数')).toBeVisible()
  await expect(page.getByTestId('confirm-batch')).toBeDisabled()
  expect(confirmRequests).toBe(0)
  await page.locator('[data-detail-id="1"] [data-score-position]').fill('4')
  await page.locator('[data-detail-id="1"] [data-score-position]').press('Tab')
  await page.locator('[data-detail-id="3"] [data-score-position]').fill('3')
  await page.locator('[data-detail-id="3"] [data-score-position]').press('Tab')
  await expect(page.getByTestId('confirm-batch')).toBeEnabled()
  await setMode(page, { confirm: '500' })
  await page.getByTestId('confirm-batch').click()
  await expect(page.getByTestId('review-feedback-toast')).toContainText('整批草稿已保留')
  await expect(page.locator('[data-detail-id="1"] [data-score-position]')).toHaveValue('4')
  await expect(page.locator('[data-detail-id="3"] [data-score-position]')).toHaveValue('3')
  expect(confirmRequests).toBe(1)
})
