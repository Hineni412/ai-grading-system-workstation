import { expect, test } from '@playwright/test'

const viewports = [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'compact desktop', width: 1280, height: 800 },
  { name: 'tablet landscape', width: 1024, height: 768 },
  { name: 'tablet portrait', width: 768, height: 1024 },
  { name: 'mobile', width: 390, height: 844 },
]

for (const viewport of viewports) {
  test(`${viewport.name} keeps the design system readable and keyboard accessible`, async ({ page }) => {
    const pageErrors: Error[] = []
    const consoleErrors: string[] = []
    page.on('pageerror', (error) => pageErrors.push(error))
    page.on('console', (message) => {
      if (message.type() === 'error') consoleErrors.push(message.text())
    })
    await page.setViewportSize(viewport)
    await page.goto('/')

    await expect(page.getByTestId('design-system-showcase')).toBeVisible()
    await expect(page.getByRole('heading', { name: '基础 Token' })).toBeVisible()
    await expect(page.getByRole('heading', { name: '操作反馈' })).toBeVisible()

    const hasHorizontalOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    )
    expect(hasHorizontalOverflow).toBe(false)

    const longTeacherBadge = page.getByText('教师决定：二次函数图像与性质', { exact: false })
    await expect(longTeacherBadge).toBeVisible()
    expect(
      await longTeacherBadge.evaluate((element) => element.scrollWidth <= element.clientWidth),
    ).toBe(true)

    await page.keyboard.press('Tab')
    const focusedStyle = await page.evaluate(() => {
      const active = document.activeElement
      if (!(active instanceof HTMLElement)) return null
      const style = getComputedStyle(active)
      return { tagName: active.tagName, outlineStyle: style.outlineStyle }
    })
    expect(focusedStyle?.tagName).toBe('BUTTON')
    expect(focusedStyle?.outlineStyle).not.toBe('none')

    await expect(page.locator('#teacher-score')).toHaveAttribute('aria-invalid', 'true')
    expect(pageErrors).toEqual([])
    expect(consoleErrors).toEqual([])
  })
}

test('focused invalid input preserves the complete danger frame', async ({ page }) => {
  await page.goto('/')
  const input = page.locator('#teacher-score')

  await input.click()
  const styles = await input.evaluate((element) => {
    const inputStyle = getComputedStyle(element)
    const wrapper = element.closest('.el-input__wrapper')
    return {
      inputShadow: inputStyle.boxShadow,
      wrapperShadow: wrapper ? getComputedStyle(wrapper).boxShadow : '',
    }
  })

  expect(styles.inputShadow).toBe('none')
  expect(styles.wrapperShadow).toContain('rgb(176, 68, 68)')
})
