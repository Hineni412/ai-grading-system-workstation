import { expect, test } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('ai-grading:curriculum-scope:v1', 'bnu24-math-g8-upper'))
  await page.emulateMedia({ reducedMotion: 'reduce' })
})

test('teacher tags survive saving and reopening through the real API', async ({ page }) => {
  await page.goto('/question-bank?tab=skill')
  await page.getByRole('searchbox', { name: '搜题干' }).fill('LONG_FORMULA')
  await expect(page.locator('.qb-question-card')).toHaveCount(1)
  await page.getByRole('button', { name: '第 1 题', exact: true }).click()
  await expect(page.locator('.qb-annotation')).toBeVisible()
  await page.getByText('编辑题目属性 / 选择小节', { exact: true }).click()
  const value = 'TEST-保存后的能力标签'
  await page.getByLabel('能力标签值').last().fill(value)
  await page.getByRole('button', { name: '保存标签', exact: true }).click()
  await expect(page.getByText('标签已经保存。', { exact: true })).toBeVisible()
  const stored = await page.request.get('/api/question-bank/questions/1')
  expect(stored.status()).toBe(200)
  expect((await stored.json()).tags).toContainEqual(expect.objectContaining({ tag_type: 'ability', tag_value: value }))
  await page.reload()
  await page.getByRole('searchbox', { name: '搜题干' }).fill('LONG_FORMULA')
  await expect(page.locator('.qb-question-card')).toHaveCount(1)
  await page.getByRole('button', { name: '第 1 题', exact: true }).click()
  await expect(page.locator('.qb-annotation')).toBeVisible()
  await page.getByText('编辑题目属性 / 选择小节', { exact: true }).click()
  await expect(page.getByLabel('能力标签值').last()).toHaveValue(value)
})

test('real API keeps both pages of paper questions and restores the selected question', async ({ page }) => {
  await page.goto('/question-bank?tab=paper')
  await page.getByRole('button', { name: '按试卷', exact: true }).click()
  await page.getByRole('button', { name: '匿名九年级期末试卷', exact: true }).click()
  await expect(page.locator('.qb-paper-detail .qb-question-card')).toHaveCount(105)
  await expect(page.locator('.qb-paper-detail .qb-number-nav button')).toHaveCount(105)
  await page.locator('.qb-number-nav button').first().click()
  await expect(page.locator('.qb-paper-detail .qb-annotation')).toBeVisible()
  await expect(page).toHaveURL(/question=1/)
  await page.reload()
  await expect(page.locator('.qb-paper-detail .qb-annotation')).toBeVisible({ timeout: 60000 })
  await page.keyboard.press('Escape')
})
