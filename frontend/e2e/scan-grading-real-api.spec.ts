import { expect, test } from '@playwright/test'
import { resolve } from 'node:path'

test('real isolated API preserves grading controls, later-match supplement and batch isolation', async ({ page }) => {
  await page.goto('/sessions/1/grading-run')
  await expect(page.getByRole('heading', { name: '考试批改', exact: true })).toBeVisible()

  await page.locator('input[type="file"]').setInputFiles(
    resolve('test-results', 'p2-11-real', 'anonymous-class-scan.jpg'),
  )
  await expect(page.getByText('anonymous-class-scan.jpg')).toBeVisible()
  await page.getByRole('button', { name: '开始预检' }).click()
  await expect.poll(async () => {
    const response = await page.request.get('/api/sessions/1/grading-workspace')
    const payload = await response.json() as { upload_batch: { state: string } }
    return payload.upload_batch.state
  }).toBe('frozen')
  await expect.poll(async () => {
    const response = await page.request.get('/api/jobs?session_id=1&job_type=scan_analysis&page_size=1')
    const payload = await response.json() as { items: Array<{ status: string }> }
    return payload.items[0]?.status
  }).toBe('succeeded')
  await expect(page.getByText('仍有 1 份异常答卷待处理')).toBeVisible()
  await expect(page.getByRole('img', { name: 'anonymous-002正面', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '反面', exact: true }).click()
  await expect(page.getByRole('img', { name: 'anonymous-002反面', exact: true })).toBeVisible()

  await page.getByRole('button', { name: '下一步：批改', exact: true }).first().click()
  await page.locator('[data-confirm-pending]').check()
  await page.locator('[data-grading-mode="ai"]').click()
  await expect(page.locator('.grading-plan')).toHaveAttribute('data-status', 'ready')
  await page.locator('[data-confirm-grading-plan]').click()
  await expect.poll(async () => {
    const response = await page.request.get('/api/sessions/1/grading-workspace')
    const payload = await response.json() as { grading_run: unknown }
    return payload.grading_run !== null
  }).toBe(true)
  await page.reload()
  await page.getByRole('button', { name: '批改', exact: true }).click()
  await expect(page.getByText('已完成 1')).toBeVisible()
  await expect(page.locator('[data-action="pause"]')).toBeVisible()
  await expect(page.locator('[data-action="cancel"]')).toBeVisible()

  await page.locator('[data-action="pause"]').click()
  await expect(page.getByText(/正在安全暂停|已安全暂停/).first()).toBeVisible()
  await page.reload()
  await page.getByRole('button', { name: '批改', exact: true }).click()
  await expect(page.locator('[data-action="resume"]')).toBeVisible()
  await page.locator('[data-action="resume"]').click()
  await expect.poll(async () => {
    const response = await page.request.get('/api/jobs?session_id=1&job_type=grading_run&page_size=1')
    const payload = await response.json() as { items: Array<{ status: string }> }
    return payload.items[0]?.status
  }).toBe('running')
  await expect.poll(async () => {
    const response = await page.request.get('/api/sessions/1/grading-workspace')
    const payload = await response.json() as { grading_run: { state: string, counts: { graded: number } } }
    return `${payload.grading_run.state}:${payload.grading_run.counts.graded}`
  }, { timeout: 10_000 }).toBe('completed:1')
  await expect(page.getByRole('region', { name: '复核', exact: true })).toBeVisible()

  await page.getByRole('button', { name: /答卷与预检/ }).click()
  await page.getByRole('combobox', { name: '选择学生' }).fill('A004')
  await page.getByRole('option', { name: /匿名学生四.*A004/ }).click()
  const savedDecision = page.waitForResponse(response =>
    new URL(response.url()).pathname === '/api/sessions/1/scan/preflight/decisions'
    && response.request().method() === 'PUT', { timeout: 12_000 })
  await page.getByRole('button', { name: '匹配', exact: true }).click()
  const decisionResponse = await savedDecision
  expect(decisionResponse.status()).toBe(200)
  await decisionResponse.finished()
  await expect(page.getByText('已保存 1 项；仍有 0 份待处理。', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '已处理 1', exact: true }).click()
  await expect(page.locator('[data-saved-decision="issue:anonymous-issue-1"]')).toContainText('已保存：匹配至 匿名学生四')
  await expect(page.locator('[data-scan-reconciliation]')).toContainText(/有效可批改\s*2\s*份/)
  await expect(page.locator('[data-scan-reconciliation]')).toContainText(/未决\s*0\s*份/)
  await page.getByRole('button', { name: '下一步：批改', exact: true }).first().click()
  await expect(page.locator('[data-action="retry-failed"]')).toBeVisible()
  await page.locator('[data-action="supplement"]').click()
  await expect.poll(async () => {
    const response = await page.request.get('/api/sessions/1/grading-workspace')
    const payload = await response.json() as {
      grading_run: { state: string, counts: { graded: number } }
    }
    return `${payload.grading_run.state}:${payload.grading_run.counts.graded}`
  }, { timeout: 10_000 }).toBe('completed:2')
  await page.getByRole('button', { name: '批改', exact: true }).click()
  await expect(page.getByText('已完成 2')).toBeVisible()

  await page.getByRole('button', { name: /答卷与预检/ }).click()
  await page.getByRole('button', { name: '替换全部答卷' }).click()
  await expect(page.getByRole('button', { name: '确认替换并开始预检' })).toBeVisible()
  await expect(page.locator('input[type="file"]')).toBeEnabled()
  await page.locator('input[type="file"]').setInputFiles(
    resolve('test-results', 'p2-11-real', 'anonymous-class-scan.jpg'),
  )
  await expect(page.getByText('旧答卷和已有成果目前仍然保留', { exact: false })).toBeVisible()
  await expect(page.getByRole('button', { name: '确认替换并开始预检' })).toBeEnabled()
  page.once('dialog', (dialog) => dialog.accept())
  await page.getByRole('button', { name: '确认替换并开始预检' }).click()
  await expect.poll(async () => {
    const response = await page.request.get('/api/jobs?session_id=1&job_type=scan_analysis&page_size=1')
    const payload = await response.json() as { items: Array<{ status: string }> }
    return payload.items[0]?.status
  }).toBe('succeeded')
  await page.getByRole('button', { name: '下一步：批改', exact: true }).first().click()
  await page.locator('[data-confirm-pending]').check()
  await page.locator('[data-grading-mode="ai"]').click()
  await expect(page.locator('.grading-plan')).toHaveAttribute('data-status', 'ready')
  await page.locator('[data-confirm-grading-plan]').click()
  await expect(page.locator('[data-action="cancel"]')).toBeVisible()
  page.once('dialog', (dialog) => dialog.accept())
  await page.locator('[data-action="cancel"]').click()
  await expect.poll(async () => {
    const response = await page.request.get('/api/sessions/1/grading-workspace')
    const payload = await response.json() as { grading_run: { state: string } }
    return payload.grading_run.state
  }).toBe('cancelled')
  await expect(page.getByRole('region', { name: '复核', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '批改', exact: true }).click()
  await expect(page.getByText('本次运行已取消').first()).toBeVisible()
  await page.reload()
  await page.getByRole('button', { name: '批改', exact: true }).click()
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
