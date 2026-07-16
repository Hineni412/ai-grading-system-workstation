import { expect, test } from '@playwright/test'
import { resolve } from 'node:path'

test('real isolated API preserves upload, preflight, grading controls and refresh recovery', async ({ page }) => {
  await page.goto('/sessions/1/grading-run')
  await expect(page.getByRole('heading', { name: '整班答卷批改' })).toBeVisible()

  await page.locator('input[type="file"]').setInputFiles(
    resolve('test-results', 'p2-11-real', 'anonymous-class-scan.jpg'),
  )
  await expect(page.getByText('anonymous-class-scan.jpg')).toBeVisible()
  await page.getByRole('button', { name: '冻结并开始预检' }).click()
  await expect(page.getByText('上传批次已冻结')).toBeVisible()
  await expect.poll(async () => {
    const response = await page.request.get('/api/jobs?session_id=1&job_type=scan_analysis&page_size=1')
    const payload = await response.json() as { items: Array<{ status: string }> }
    return payload.items[0]?.status
  }).toBe('succeeded')
  await page.getByRole('button', { name: '刷新预检结果' }).click()
  await expect(page.getByText('仍有 1 份异常答卷待处理')).toBeVisible()

  await page.locator('[data-confirm-pending]').check()
  await page.locator('[data-grading-mode="hybrid_batch"]').click()
  await expect.poll(async () => {
    const response = await page.request.get('/api/sessions/1/grading-workspace')
    const payload = await response.json() as { grading_run: unknown }
    return payload.grading_run !== null
  }).toBe(true)
  await page.reload()
  await expect(page.getByText('已完成 1')).toBeVisible()
  await expect(page.locator('[data-action="pause"]')).toBeVisible()
  await expect(page.locator('[data-action="cancel"]')).toBeVisible()

  await page.locator('[data-action="pause"]').click()
  await expect(page.getByText(/pause_requested|paused/)).toBeVisible()
  await page.reload()
  await expect(page.locator('[data-action="resume"]')).toBeVisible()
  await page.locator('[data-action="resume"]').click()
  await expect.poll(async () => {
    const response = await page.request.get('/api/jobs?session_id=1&job_type=grading_run&page_size=1')
    const payload = await response.json() as { items: Array<{ status: string }> }
    return payload.items[0]?.status
  }).toBe('running')
  await page.reload()
  await expect(page.locator('[data-action="cancel"]')).toBeVisible()
  page.once('dialog', (dialog) => dialog.accept())
  await page.locator('[data-action="cancel"]').click()
  await expect(page.getByText(/cancel_requested|cancelled/)).toBeVisible()
  await expect.poll(async () => {
    const response = await page.request.get('/api/sessions/1/grading-workspace')
    const payload = await response.json() as { grading_run: { state: string } }
    return payload.grading_run.state
  }).toBe('cancelled')
  await page.reload()
  await expect(page.locator('[data-action="resume"]')).toHaveCount(0)

  for (const viewport of [
    { width: 1920, height: 1080 }, { width: 1440, height: 900 }, { width: 1366, height: 768 },
    { width: 1280, height: 800 }, { width: 1024, height: 768 },
  ]) {
    await page.setViewportSize(viewport)
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)
    expect(overflow, `${viewport.width}x${viewport.height} should not overflow horizontally`).toBe(false)
  }
})
