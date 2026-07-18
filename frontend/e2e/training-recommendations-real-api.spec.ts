import { expect, test } from '@playwright/test'

test('real training API preserves evidence, idempotency, conflicts and export recovery', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('ai-grading:selected-session:v1', '14')
  })
  await page.goto('/training')
  await expect(page.getByRole('heading', { name: '训练推荐', exact: true })).toBeVisible()
  await expect(page.getByText('精确标签口径', { exact: true })).toBeVisible()

  await page.getByTestId('training-student').selectOption('15')
  await page.getByTestId('analyze-training').click()
  await expect(page.getByText(/没有可生成推荐的精确标签证据/)).toBeVisible()

  await page.getByTestId('training-student').selectOption('12')
  await page.getByTestId('analyze-training').click()
  await expect(page.getByText('已覆盖 2 / 3 个评分题')).toBeVisible()
  await expect(page.getByText('证明步骤缺少依据')).toBeVisible()
  await expect(page.getByText(/匿名阶段测验 · Q1 · 6 \/ 10/)).toBeVisible()

  await page.getByTestId('preview-training').click()
  await expect(page.getByText('与薄弱知识点标签完全相同').first()).toBeVisible()
  await expect(page.getByText(/基础巩固阶段缺少 1 道知识点完全相同的候选题/).first()).toBeVisible()
  await expect(page.getByText(/提升应用阶段缺少 1 道知识点完全相同的候选题/).first()).toBeVisible()
  await page.request.post('/__p2_18__/change-candidate')
  await page.getByTestId('confirm-training').click()
  await expect(page.getByText(/训练候选题已经变化/).first()).toBeVisible()

  await page.getByTestId('preview-training').click()
  await page.getByTestId('confirm-training').click()
  await expect(page.getByRole('status').filter({ hasText: '训练任务已保存' })).toBeVisible()
  const taskCode = page.getByRole('button', { name: /TRN-CFM/ }).first()
  await expect(taskCode).toBeVisible()

  await page.reload()
  await expect(page.getByRole('button', { name: /TRN-CFM/ }).first()).toBeVisible()
  await page.getByRole('button', { name: /TRN-CFM/ }).first().click()
  await page.getByTestId('export-training-task').click()
  await expect(page.getByText(/训练材料已加入生成队列/)).toBeVisible()
  await expect(page.getByText('可下载')).toBeVisible()
  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('button', { name: '下载', exact: true }).click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toMatch(/\.zip$/i)

  for (const viewport of [
    { width: 1920, height: 1080 },
    { width: 1440, height: 900 },
    { width: 1366, height: 768 },
    { width: 1280, height: 800 },
    { width: 1024, height: 768 },
  ]) {
    await page.setViewportSize(viewport)
    expect(await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    )).toBe(false)
  }
})
