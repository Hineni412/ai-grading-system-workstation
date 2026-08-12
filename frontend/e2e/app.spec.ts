import { expect, test } from '@playwright/test'

const emptySessions = { items: [], total: 0 }

test.beforeEach(async ({ page }) => {
  await page.route('**/api/sessions', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(emptySessions) }),
  )
})

test('design-system route preserves the P2-02 showcase and focus treatment', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto('/design-system')

  await expect(page.getByTestId('design-system-showcase')).toBeVisible()
  await expect(page.getByRole('heading', { name: '基础 Token' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '操作反馈' })).toBeVisible()
  await expect(page.locator('#teacher-score')).toHaveAttribute('aria-invalid', 'true')

  const longTeacherBadge = page.getByText('教师决定：二次函数图像与性质', { exact: false })
  await expect(longTeacherBadge).toBeVisible()
  expect(await longTeacherBadge.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(
    true,
  )

  const examInput = page.locator('#exam-name')
  const invalidInput = page.locator('#teacher-score')

  // 无效字段（未聚焦）保持危险色边框，无聚焦环
  await expect
    .poll(() => invalidInput.evaluate((element) => getComputedStyle(element).borderColor))
    .toBe('rgb(176, 68, 68)')
  expect(await invalidInput.evaluate((element) => getComputedStyle(element).boxShadow)).not.toContain(
    '0px 0px 0px 3px',
  )

  // 有效字段聚焦后显示品牌青边框与 50% 品牌青焦点环
  await examInput.focus()
  await expect(examInput).toBeFocused()
  await expect
    .poll(() => examInput.evaluate((element) => getComputedStyle(element).borderColor))
    .toBe('rgb(19, 94, 107)')
  await expect
    .poll(() => examInput.evaluate((element) => getComputedStyle(element).boxShadow))
    .toContain('/ 0.5) 0px 0px 0px 3px')

  // Tab 顺序从有效字段进入无效字段，无效字段聚焦后转为 20% 危险色焦点环
  await page.keyboard.press('Tab')
  await expect(invalidInput).toBeFocused()
  await expect
    .poll(() => invalidInput.evaluate((element) => getComputedStyle(element).borderColor))
    .toBe('rgb(176, 68, 68)')
  await expect
    .poll(() => invalidInput.evaluate((element) => getComputedStyle(element).boxShadow))
    .toContain('/ 0.2) 0px 0px 0px 3px')
})
