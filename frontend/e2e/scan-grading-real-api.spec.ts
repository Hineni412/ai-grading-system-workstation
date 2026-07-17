import { expect, test } from '@playwright/test'
import { resolve } from 'node:path'

test('real isolated API preserves grading controls, later-match supplement and batch isolation', async ({ page }) => {
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
  await expect(page.getByText(/正在安全暂停|已安全暂停/)).toBeVisible()
  await page.reload()
  await expect(page.locator('[data-action="resume"]')).toBeVisible()
  await page.locator('[data-action="resume"]').click()
  await expect.poll(async () => {
    const response = await page.request.get('/api/jobs?session_id=1&job_type=grading_run&page_size=1')
    const payload = await response.json() as { items: Array<{ status: string }> }
    return payload.items[0]?.status
  }).toBe('running')
  await expect.poll(async () => {
    const response = await page.request.get('/api/sessions/1/grading-workspace')
    const payload = await response.json() as { grading_run: { state: string } }
    return payload.grading_run.state
  }, { timeout: 10_000 }).toBe('completed')
  await expect(page.locator('[data-action="supplement"]')).toBeVisible()

  await page.getByLabel('选择学生').selectOption({ label: '匿名学生四 · A004' })
  await page.getByRole('button', { name: '匹配', exact: true }).click()
  await expect(page.getByText('待处理异常 0 份')).toBeVisible()
  await expect(page.locator('[data-action="retry-failed"]')).toBeVisible()
  await page.locator('[data-action="supplement"]').click()
  await expect.poll(async () => {
    const response = await page.request.get('/api/sessions/1/grading-workspace')
    const payload = await response.json() as {
      grading_run: { state: string, counts: { graded: number } }
    }
    return `${payload.grading_run.state}:${payload.grading_run.counts.graded}`
  }, { timeout: 10_000 }).toBe('completed:2')
  await expect(page.getByText('已完成 2')).toBeVisible()

  page.once('dialog', (dialog) => dialog.accept())
  await page.locator('[data-action="new-batch"]').click()
  await expect(page.getByText('0 个文件 · 0 KB')).toBeVisible()
  await expect(page.locator('input[type="file"]')).toBeEnabled()
  await page.locator('input[type="file"]').setInputFiles(
    resolve('test-results', 'p2-11-real', 'anonymous-class-scan.jpg'),
  )
  await page.getByRole('button', { name: '冻结并开始预检' }).click()
  await expect.poll(async () => {
    const response = await page.request.get('/api/jobs?session_id=1&job_type=scan_analysis&page_size=1')
    const payload = await response.json() as { items: Array<{ status: string }> }
    return payload.items[0]?.status
  }).toBe('succeeded')
  await page.locator('[data-confirm-pending]').check()
  await page.locator('[data-grading-mode="hybrid_batch"]').click()
  await expect(page.locator('[data-action="cancel"]')).toBeVisible()
  page.once('dialog', (dialog) => dialog.accept())
  await page.locator('[data-action="cancel"]').click()
  await expect(page.getByText(/正在安全取消|本次运行已取消/)).toBeVisible()
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
