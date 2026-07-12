import { expect, test } from '@playwright/test'

test('loads the frontend foundation without horizontal overflow', async ({ page }) => {
  await page.goto('/')

  await expect(page.getByTestId('frontend-ready')).toHaveText('前端工程已就绪')
  const hasHorizontalOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
  )
  expect(hasHorizontalOverflow).toBe(false)
})
