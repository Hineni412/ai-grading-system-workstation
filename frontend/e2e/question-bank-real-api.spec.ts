import { resolve } from 'node:path'
import { expect, test } from '@playwright/test'

test('real API question bank handles scale, evidence, recovery and guarded writes', async ({ page }) => {
  await page.goto('/question-bank')
  await expect(page.getByRole('heading', { name: '题库管理', exact: true })).toBeVisible()
  await expect(page.getByText('共 2005 题', { exact: true })).toBeVisible()
  const fileInput = page.locator('input[type="file"]')
  await fileInput.focus()
  expect(await fileInput.evaluate((input) => {
    const picker = input.closest('.qb-file-picker')
    return picker ? getComputedStyle(picker).outlineStyle : 'none'
  })).not.toBe('none')

  const keyword = page.getByRole('searchbox', { name: '关键词' })
  await keyword.fill('LONG_FORMULA')
  await expect(page.getByText('共 2005 题', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '应用筛选' }).click()
  await expect(page.getByText('共 1 题', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '打开第 1 题详情' }).click()
  await expect(page.getByRole('heading', { name: '第 1 题' })).toBeVisible()
  await expect(page.getByLabel('第 1 题', { exact: true }).getByText(/∑_\{k=1\}\^\{120\}/)).toBeVisible()
  await expect(page.getByAltText('题目图片素材')).toBeVisible()
  await expect(page.getByRole('link', { name: /打开题目预览/ })).toBeVisible()

  await page.getByRole('button', { name: '添加标签' }).click()
  const values = page.getByLabel(/标签值/)
  await values.last().fill('函数建模')
  await page.getByRole('button', { name: '保存标签' }).click()
  await expect(page.getByText('标签已经保存。')).toBeVisible()

  page.once('dialog', (dialog) => dialog.accept())
  await page.getByRole('button', { name: '删除这道题' }).click()
  await expect(page.getByRole('button', { name: '立即恢复' })).toBeVisible()
  await page.getByRole('button', { name: '立即恢复' }).click()
  await expect(page.getByText('题目已经恢复。')).toBeVisible()

  await page.getByRole('button', { name: '清除' }).click()
  await expect(page.getByText('共 2005 题', { exact: true })).toBeVisible()
  await page.locator('.qb-ledger tbody input[type="checkbox"]').first().check()
  await page.getByRole('button', { name: '下一页' }).click()
  await expect(page.getByText('第 2 / 101 页', { exact: true })).toBeVisible()
  await page.locator('.qb-ledger tbody input[type="checkbox"]').first().check()
  await expect(page.locator('.qb-selection-bar')).toContainText(/已选\s*2\s*题\s*，其中 1 题在其他页/)

  page.once('dialog', (dialog) => dialog.accept())
  await page.getByRole('button', { name: 'AI 标注 2 题' }).click()
  await expect(page.getByText(/部分完成：成功 1/)).toBeVisible()
  page.once('dialog', async (dialog) => {
    expect(dialog.message()).toContain('只重试 1 道失败题目')
    await dialog.dismiss()
  })
  await page.getByRole('button', { name: '重试允许的失败项' }).click()
  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('button', { name: '下载失败清单' }).click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toContain('失败清单.csv')

  await page.reload()
  await expect(page.getByText(/AI 标注 #/)).toBeVisible()
  await expect(page.getByText(/部分完成：成功 1/)).toBeVisible()

  await fileInput.setInputFiles(
    resolve('test-results/p2-16-real/anonymous-import.docx'),
  )
  await expect(page.getByText(/已提交任务/)).toBeVisible()
  await expect(page.getByText(/试卷导入 #/)).toBeVisible()

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
