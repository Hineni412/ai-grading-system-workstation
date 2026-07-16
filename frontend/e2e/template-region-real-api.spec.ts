import { expect, test } from '@playwright/test'
import { resolve } from 'node:path'

test('real isolated API uploads, saves, confirms and restores answer regions', async ({ page }) => {
  await page.goto('/sessions/1/regions')
  await expect(page.getByRole('heading', { name: '上传双页样卷' })).toBeVisible()

  await page.getByLabel('样卷 PDF').setInputFiles(
    resolve('test-results', 'p2-10-real', 'anonymous-two-page.pdf'),
  )
  await page.getByRole('button', { name: '上传并打开画框' }).click()
  await expect(page.getByText('草稿标定中')).toBeVisible()

  await page.getByRole('button', { name: '新增框' }).click()
  const canvas = page.locator('[data-role="canvas"]')
  const box = await canvas.boundingBox()
  expect(box).not.toBeNull()
  await page.mouse.move(box!.x + 90, box!.y + 110)
  await page.mouse.down()
  await page.mouse.move(box!.x + 300, box!.y + 280)
  await page.mouse.up()
  await page.getByRole('button', { name: '题框列表' }).click()
  await page.locator('.mapping-select').selectOption('Q1')
  await expect(page.getByText('草稿已保存', { exact: true })).toBeVisible()

  page.once('dialog', (dialog) => dialog.accept())
  await page.getByRole('button', { name: '完成标定' }).click()
  await expect(page.getByText('正式版本 · 只读')).toBeVisible()
  await expect(page.getByText(/P2-11/)).toBeVisible()

  await page.reload()
  await expect(page.getByText('正式版本 · 只读')).toBeVisible()
  await expect(page.locator('[data-region-uuid]')).toHaveCount(1)
})
