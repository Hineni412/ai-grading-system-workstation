/**
 * 日常管理缺陷修复复测（临时，不进测试套件）。
 * 范围：1) 周次校正 UI 崩溃修复；2) 拖拽移动/交换提示文案。
 * 运行：cd 仓库根目录 && node scratch/daily-recheck.cjs（需 8036 实例已启动）
 */
// playwright 装在 frontend/node_modules，本脚本在仓库根 scratch/ 下需按路径引入。
const { chromium } = require('../frontend/node_modules/playwright')
const fs = require('fs')
const path = require('path')

const BASE = 'http://127.0.0.1:8036'
const SHOTS = path.resolve(__dirname, 'daily-acceptance')

const results = []
const consoleErrors = []
const screenshots = []

function ok(name, cond, extra = '') {
  results.push({ name, pass: !!cond, extra })
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${extra ? '  -- ' + extra : ''}`)
}

async function shot(page, filename) {
  const file = path.join(SHOTS, filename)
  await page.screenshot({ path: file, fullPage: true })
  screenshots.push(file)
  console.log(`SHOT  ${filename}`)
}

function cell(page, slotLabel, dayIndex) {
  return page
    .locator('table.timetable-grid tbody tr', {
      has: page.locator(`th[scope="row"] span:text-is("${slotLabel}")`),
    })
    .locator('td')
    .nth(dayIndex - 1)
}

async function waitGrid(page) {
  await page.locator('table.timetable-grid').waitFor({ timeout: 15000 })
}

async function main() {
  fs.mkdirSync(SHOTS, { recursive: true })
  const browser = await chromium.launch({ headless: true })
  const context = await browser.newContext({ baseURL: BASE, viewport: { width: 1440, height: 900 } })
  const page = await context.newPage()
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text())
  })
  page.on('pageerror', (err) => consoleErrors.push(`pageerror: ${err.message}`))

  try {
    console.log('\n===== 复测 1：真实 UI 周次校正 =====')
    await page.goto(`${BASE}/daily`, { waitUntil: 'domcontentloaded' })
    await waitGrid(page)
    const before = await page.locator('.week-navigator__title').textContent()
    console.log(`INFO  校正前表头: ${before.trim()}`)

    await page.getByRole('button', { name: '校正周次' }).click()
    await page.locator('.week-navigator__anchor input').fill('4')
    await page.locator('.week-navigator__anchor').getByRole('button', { name: '确定' }).click()
    await page.locator('text=已把当前查看的周设为第 4 周').waitFor()
    ok('UI 校正周次后页面不崩溃', !(await page.locator('.application-error').isVisible()))
    const title = await page.locator('.week-navigator__title').textContent()
    ok('表头变为「第 4 周」', title.includes('第 4 周'), title.trim())
    await shot(page, 'recheck-week-anchor.png')

    await page.reload({ waitUntil: 'domcontentloaded' })
    await waitGrid(page)
    const titleAfter = await page.locator('.week-navigator__title').textContent()
    ok('刷新后第 4 周保持', titleAfter.includes('第 4 周'), titleAfter.trim())

    console.log('\n===== 复测 2：拖拽移动/交换提示文案 =====')
    await page.locator('div[role="group"][aria-label="课表模式"]').getByRole('button', { name: '临时调整' }).click()

    // 2a: 周一第1节（数学·九(1)班）→ 周五第2节（空白格）= 移动
    await cell(page, '第1节', 1).dragTo(cell(page, '第2节', 5))
    const moveNotice = page.locator('text=仅本周生效').first()
    await moveNotice.waitFor()
    const moveText = await moveNotice.textContent()
    ok('拖到空白格提示「移动」语义', moveText.includes('已把这节课移到新位置') && !moveText.includes('已交换'), moveText.trim())
    const fri2 = await cell(page, '第2节', 5).textContent()
    ok('周五第2节出现该课并带临时标记',
      fri2.includes('数学') && fri2.includes('九(1)班') && await cell(page, '第2节', 5).locator('.timetable-grid__override-badge').isVisible(),
      fri2.trim())
    ok('周一第1节本周变空', !(await cell(page, '第1节', 1).textContent()).includes('数学'))
    await shot(page, 'recheck-drag-move.png')

    // 2b: 周三第5节（语文·九(1)班）→ 周五第2节（已有课的临时格）= 交换
    await cell(page, '第5节', 3).dragTo(cell(page, '第2节', 5))
    const swapNotice = page.locator('text=仅本周生效').first()
    await swapNotice.waitFor()
    const swapText = await swapNotice.textContent()
    ok('拖到有课格提示「已交换」', swapText.includes('已交换这两节课'), swapText.trim())
    const fri2After = await cell(page, '第2节', 5).textContent()
    const wed5After = await cell(page, '第5节', 3).textContent()
    ok('交换后周五第2节=语文·九(1)班', fri2After.includes('语文') && fri2After.includes('九(1)班'), fri2After.trim())
    ok('交换后周三第5节=数学·九(1)班', wed5After.includes('数学') && wed5After.includes('九(1)班'), wed5After.trim())
    await shot(page, 'recheck-drag-swap.png')
  } catch (e) {
    ok('复测执行', false, e.message.split('\n')[0])
    await shot(page, 'recheck-error.png').catch(() => {})
  }

  await browser.close()
  const failed = results.filter((r) => !r.pass)
  console.log('\n===== 汇总 =====')
  console.log(`断言 ${results.length} 项，通过 ${results.length - failed.length}，失败 ${failed.length}`)
  console.log(`控制台错误 ${consoleErrors.length} 条`)
  consoleErrors.slice(0, 10).forEach((e) => console.log(`  CONSOLE: ${e}`))
  fs.writeFileSync(
    path.join(SHOTS, 'recheck-results.json'),
    JSON.stringify({ results, consoleErrors, screenshots }, null, 2),
  )
  process.exit(failed.length ? 1 : 0)
}

main().catch((e) => {
  console.error('FATAL', e)
  process.exit(2)
})
