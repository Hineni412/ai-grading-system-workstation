import { resolve } from 'node:path'
import { expect, test, type Page } from '@playwright/test'

async function filterStudent(page: Page, code: string): Promise<void> {
  await page.getByLabel('搜索学生').fill(code)
  await page.getByRole('button', { name: '筛选' }).click()
  await expect(page.getByText(`共 ${code ? 1 : 125} 名学生`, { exact: true })).toBeVisible()
}

async function openStudent(page: Page, code: string, name: string): Promise<void> {
  await filterStudent(page, code)
  await page.getByRole('button', { name: `查看并编辑 ${name}` }).click()
  await expect(page.getByRole('heading', { name, exact: true })).toBeVisible()
}

test('真实 API 名单流程保持可预览、可恢复且不会横向溢出', async ({ page }) => {
  await page.goto('/students')
  await expect(page.getByRole('heading', { name: '学生名单', exact: true })).toBeVisible()
  await expect(page.getByText('共 125 名学生', { exact: true })).toBeVisible()

  const fileInput = page.locator('input[type="file"]')
  await fileInput.setInputFiles(resolve('test-results/p2-13-real/anonymous-students.csv'))
  await expect(page.getByText('新增 1', { exact: true })).toBeVisible()
  await expect(page.getByText('更新 1', { exact: true })).toBeVisible()
  await expect(page.getByText('无效 1', { exact: true })).toBeVisible()
  await expect(page.getByText('重复 1', { exact: true })).toBeVisible()
  await expect(page.getByLabel('选择源文件第 3 行')).toBeDisabled()
  await expect(page.getByLabel('选择源文件第 4 行')).toBeDisabled()
  await expect(page.getByLabel('选择源文件第 5 行')).toBeEnabled()

  await page.getByLabel('学号列').selectOption('name')
  await page.getByRole('button', { name: '应用列对应' }).click()
  await expect(page.getByText('新增 3', { exact: true })).toBeVisible()
  await page.getByLabel('学号列').selectOption('student_code')
  await page.getByRole('button', { name: '应用列对应' }).click()
  await expect(page.getByText('重复 1', { exact: true })).toBeVisible()

  await page.getByRole('button', { name: '确认写入名单' }).click()
  await expect(page.getByText('共 126 名学生', { exact: true })).toBeVisible()
  await expect(page.getByText('已导入 1 名新学生，更新 1 名学生。')).toBeVisible()

  await openStudent(page, 'A002', '匿名学生002')
  await page.getByRole('textbox', { name: '姓名', exact: true }).fill('匿名学生002已核对')
  await page.getByRole('button', { name: '保存学生信息' }).click()
  await expect(page.getByText('学生信息已保存。')).toBeVisible()
  await page.getByRole('button', { name: '关闭学生资料' }).click()

  await openStudent(page, 'A003', '匿名学生003')
  await page.getByRole('button', { name: '查看删除影响' }).click()
  await page.getByLabel('输入学号 A003 确认').fill('A003')
  await page.request.post('/test-support/fail-next-backup')
  await page.getByRole('button', { name: '创建备份并永久删除' }).click()
  await expect(
    page.getByText('备份没有成功，系统已停止删除，学生数据保持不变。'),
  ).toBeVisible()
  await expect(page.getByRole('heading', { name: '匿名学生003', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '关闭学生资料' }).click()

  await openStudent(page, 'A001', '匿名学生001更新')
  await page.getByRole('button', { name: '查看删除影响' }).click()
  await expect(page.getByText(/将删除 1 份成绩记录、\s*1 条评分明细/)).toBeVisible()
  await expect(page.getByText(/同时删除 1 条批注、\s*1 条考勤，并解除\s*1 份答卷/)).toBeVisible()
  await page.getByLabel('输入学号 A001 确认').fill('A001')
  await page.getByRole('button', { name: '创建备份并永久删除' }).click()
  await expect(page.getByText(/学生及关联记录已安全删除，备份已完成：1 名学生、1 份成绩、1 条评分明细、1 条批注、1 条考勤；已解除 1 份答卷关联。/)).toBeVisible()

  await fileInput.setInputFiles(resolve('test-results/p2-13-real/anonymous-students.xlsx'))
  await expect(page.getByText('新增 1', { exact: true })).toBeVisible()
  await expect(page.getByText('无变化 1', { exact: true })).toBeVisible()

  await page.reload()
  await expect(page.getByText('共 125 名学生', { exact: true })).toBeVisible()
  await filterStudent(page, 'A002')
  await expect(page.getByText('匿名学生002已核对', { exact: true })).toBeVisible()

  await filterStudent(page, 'A050')
  await expect(page.getByText('匿名超长姓名用于验证名单表格不会撑出窗口边界050')).toBeVisible()
  for (const viewport of [
    { width: 1920, height: 1080 },
    { width: 1440, height: 900 },
    { width: 1366, height: 768 },
    { width: 1280, height: 800 },
    { width: 1024, height: 768 },
  ]) {
    await page.setViewportSize(viewport)
    const hasHorizontalOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    )
    expect(hasHorizontalOverflow).toBe(false)
  }
})
