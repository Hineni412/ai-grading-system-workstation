import { execFileSync } from 'node:child_process'
import { expect, test, type Page } from '@playwright/test'

import type { ResultsCenterStudent } from '../src/api/results-center'
import { SESSION_STORAGE_KEY } from '../src/stores/session'

// This flow never intercepts API responses. The suite supplies a synthetic backend.
if (!process.env.AI_GRADING_REVIEW_ARTIFACTS) {
  throw new Error('Run tools/run_test_suite.py review with its isolated backend')
}

async function openAnswer(page: Page) {
  await page.goto('/results?tab=details')
  await page.getByRole('button', { name: /Synthetic Student A，Q1，/ }).click()
  await expect(page.getByTestId('teacher-score')).toHaveValue('12')
  const crop = page.getByRole('region', { name: '答卷证据查看器' }).getByRole('img')
  await expect.poll(() => crop.evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0)
}

async function resultScores(page: Page) {
  const response = await page.request.get('/api/sessions/1/results-center')
  expect(response.ok()).toBe(true)
  const payload = await response.json()
  return Object.fromEntries(payload.students.map((student: ResultsCenterStudent) => [
    student.student_code, student.current_score,
  ]))
}

test('teacher score survives refresh, a stale window, reopening and Excel export', async ({ page, browser, baseURL }, testInfo) => {
  const browserErrors: string[] = []
  page.on('pageerror', (error) => browserErrors.push(error.message))
  await page.addInitScript((key) => localStorage.setItem(key, '1'), SESSION_STORAGE_KEY)
  await openAnswer(page)
  await expect(resultScores(page)).resolves.toEqual({ 'SYN-001': 85, 'SYN-002': 70 })

  // A second independent window reads the same revision before the first saves.
  const staleContext = await browser.newContext({ baseURL })
  await staleContext.addInitScript((key) => localStorage.setItem(key, '1'), SESSION_STORAGE_KEY)
  const stalePage = await staleContext.newPage()
  await openAnswer(stalePage)
  try {
    const score = page.getByTestId('teacher-score')
    const confirm = page.getByRole('button', { name: '确认此份并返回', exact: true })

    await test.step('invalid values and keyboard input do not silently save', async () => {
      for (const [value, message] of [
        ['18', '教师最终分不能超过 17 分'],
        ['15.5', '教师最终分必须是整数'],
      ] as const) {
        await score.fill(value)
        await expect(page.getByText(message, { exact: true })).toBeVisible()
        await expect(confirm).toBeDisabled()
      }
      await score.fill('16')
      await score.press('Enter')
      await expect(score).toHaveValue('16')
      await expect(resultScores(page)).resolves.toEqual({ 'SYN-001': 85, 'SYN-002': 70 })
    })

    await test.step('the current confirmation control is visible and saves through the real API', async () => {
      for (const size of [{ width: 1280, height: 720 }, { width: 1920, height: 1080 }]) {
        await page.setViewportSize(size)
        await expect(confirm).toBeInViewport()
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true)
      }
      await page.screenshot({ path: testInfo.outputPath('review-before-save.png') })
      const saved = page.waitForResponse((response) =>
        response.url().endsWith('/review/questions/Q1/confirm') && response.request().method() === 'POST')
      await confirm.click()
      expect((await saved).status()).toBe(200)
      await expect(page).toHaveURL(/\/results\?tab=details/)
      const row = page.getByRole('row').filter({ hasText: 'SYN-001' })
      await expect(row.locator('.results-matrix__total strong')).toHaveText('89 / 100')
      await expect(page.getByRole('button', { name: /Synthetic Student A，Q1，教师已确认，得分 16/ })).toBeVisible()
      await expect(resultScores(page)).resolves.toEqual({ 'SYN-001': 89, 'SYN-002': 70 })
    })

    await test.step('a stale window keeps its draft and cannot overwrite the saved score', async () => {
      await stalePage.getByTestId('teacher-score').fill('15')
      const rejected = stalePage.waitForResponse((response) =>
        response.url().endsWith('/review/questions/Q1/confirm') && response.request().method() === 'POST')
      await stalePage.getByRole('button', { name: '确认此份并返回', exact: true }).click()
      expect((await rejected).status()).toBe(409)
      await expect(stalePage.getByTestId('review-feedback-toast')).toContainText('教师草稿仍保留')
      await expect(stalePage.getByTestId('teacher-score')).toHaveValue('15')
      await expect(resultScores(stalePage)).resolves.toEqual({ 'SYN-001': 89, 'SYN-002': 70 })
      await stalePage.screenshot({ path: testInfo.outputPath('stale-window-draft-retained.png') })
    })

    await test.step('refresh and a fresh browser context read the persisted teacher score', async () => {
      await page.reload()
      await expect(page.getByRole('button', { name: /Synthetic Student A，Q1，教师已确认，得分 16/ })).toBeVisible()
      const freshContext = await browser.newContext({ baseURL })
      try {
        await freshContext.addInitScript((key) => localStorage.setItem(key, '1'), SESSION_STORAGE_KEY)
        const freshPage = await freshContext.newPage()
        await freshPage.goto('/results?tab=details')
        await freshPage.getByRole('button', { name: /Synthetic Student A，Q1，教师已确认，得分 16/ }).click()
        await expect(freshPage.getByTestId('teacher-score')).toHaveValue('16')
        await freshPage.screenshot({ path: testInfo.outputPath('review-reopened.png') })
      } finally {
        await freshContext.close()
      }
      await page.screenshot({ path: testInfo.outputPath('results-after-refresh.png') })
    })

    await test.step('the downloaded score sheet agrees with the displayed total', async () => {
      await page.getByRole('button', { name: '导出文件', exact: true }).click()
      await page.getByTestId('generate-score_excel').click()
      await page.getByTestId('submit-score-excel').click()
      const downloadButton = page.locator('.file-product')
        .filter({ has: page.getByRole('heading', { name: '成绩表', exact: true }) })
        .getByRole('button', { name: '下载', exact: true })
      await expect(downloadButton).toBeVisible()
      const downloading = page.waitForEvent('download')
      await downloadButton.click()
      const download = await downloading
      const reportPath = testInfo.outputPath('reviewed-scores.xlsx')
      await download.saveAs(reportPath)
      const scoreText = execFileSync(process.env.AI_GRADING_REVIEW_PYTHON!, [
        '-B', '-c',
        'import sys; from pathlib import Path; from tests.api_e2e.harness import ApiE2EHarness; print(ApiE2EHarness.xlsx_score(Path(sys.argv[1]).read_bytes(), "SYN-001"))',
        reportPath,
      ], { cwd: '..', encoding: 'utf-8', windowsHide: true })
      expect(Number(scoreText.trim())).toBe(89)
    })

    expect(browserErrors).toEqual([])
  } finally {
    await staleContext.close()
  }
})
