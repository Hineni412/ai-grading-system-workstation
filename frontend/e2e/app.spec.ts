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
  await examInput.focus()
  await expect
    .poll(() =>
      examInput.evaluate((element) => {
        const wrapper = element.closest('.el-input__wrapper')
        return wrapper ? getComputedStyle(wrapper).boxShadow : ''
      }),
    )
    .toContain('rgb(37, 99, 235)')
  const validStyles = await examInput.evaluate((element) => {
    const wrapper = element.closest('.el-input__wrapper')
    return {
      inputShadow: getComputedStyle(element).boxShadow,
      wrapperShadow: wrapper ? getComputedStyle(wrapper).boxShadow : '',
    }
  })
  expect(validStyles.inputShadow).toBe('none')
  expect(validStyles.wrapperShadow).toContain('rgb(37, 99, 235)')
  expect(validStyles.wrapperShadow).toContain('rgb(239, 246, 255)')

  const invalidInput = page.locator('#teacher-score')
  await page.keyboard.press('Tab')
  await expect(invalidInput).toBeFocused()
  await expect
    .poll(() =>
      invalidInput.evaluate((element) => {
        const wrapper = element.closest('.el-input__wrapper')
        return wrapper ? getComputedStyle(wrapper).boxShadow : ''
      }),
    )
    .toContain('rgb(239, 246, 255)')
  const invalidStyles = await invalidInput.evaluate((element) => {
    const wrapper = element.closest('.el-input__wrapper')
    return {
      inputShadow: getComputedStyle(element).boxShadow,
      wrapperShadow: wrapper ? getComputedStyle(wrapper).boxShadow : '',
    }
  })
  expect(invalidStyles.inputShadow).toBe('none')
  expect(invalidStyles.wrapperShadow).toContain('rgb(176, 68, 68)')
  expect(invalidStyles.wrapperShadow).toContain('rgb(239, 246, 255)')
})
