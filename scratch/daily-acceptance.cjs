/**
 * 日常管理（/daily）三条核心流程端到端验收脚本（临时，不进测试套件）。
 * 运行：cd 仓库根目录 && node scratch/daily-acceptance.cjs
 * 依赖：已启动的 8036 测试实例 + 两班各 5 名测试学生。
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
let dragWorked = false

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

/** 课表格子定位：按时段行头精确文本 + 星期几（1=周一 … 5=周五）。 */
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

async function setRegularCell(page, slotLabel, dayIndex, course, classLabel) {
  await cell(page, slotLabel, dayIndex).click()
  const panel = page.locator('section[aria-label="常规课表编辑"]')
  await panel.getByRole('heading').waitFor()
  await panel.getByLabel('课程').fill(course)
  await panel.getByLabel('班级（可留空）').fill(classLabel)
  await panel.getByRole('button', { name: '保存常规格' }).click()
  await page.locator('text=常规课表已保存').waitFor()
}

async function addCustomSlot(page, label, start, end, positionLabel) {
  const mgr = page.locator('section[aria-label="自定义时段管理"]')
  await mgr.getByLabel('名称').fill(label)
  await mgr.getByLabel('开始（可留空）').fill(start)
  await mgr.getByLabel('结束（可留空）').fill(end)
  await mgr.getByLabel('位置').selectOption({ label: positionLabel })
  await mgr.getByRole('button', { name: '添加时段' }).click()
  await page.locator(`text=已添加时段「${label}」`).waitFor()
}

async function flow1(page) {
  console.log('\n===== 流程一：课表录入与显示 =====')

  // 缺陷复现：周次校正在真实 UI 下崩溃（WeekNavigator submitAnchor 对 number 值调 .trim）。
  // 先用 API 把本周锚定为第 3 周（绕过有缺陷的 UI 路径，保证后续步骤可验）。
  const put = await page.context().request.put(`${BASE}/api/class-teacher/daily/timetable/week-anchor`, {
    headers: { 'x-class-teacher-client': 'class-teacher-browser-v1' },
    data: { date: '2026-08-31', week_no: 3 },
  })
  ok('API 锚定本周为第 3 周（绕过 UI 缺陷）', put.ok())

  await page.goto(`${BASE}/daily`, { waitUntil: 'domcontentloaded' })
  await waitGrid(page)
  ok('打开 /daily 显示标题', await page.getByRole('heading', { name: '日常管理' }).isVisible())
  const title = await page.locator('.week-navigator__title').textContent()
  ok('表头显示「第 3 周」', title.includes('第 3 周'), title.trim())

  // UI 路径缺陷取证：校正周次 → 输入数字 → 确定 → 整页掉错误边界
  await page.getByRole('button', { name: '校正周次' }).click()
  await page.locator('.week-navigator__anchor input').fill('5')
  await page.locator('.week-navigator__anchor').getByRole('button', { name: '确定' }).click()
  await page.locator('.application-error').waitFor()
  ok('缺陷复现：UI 校正周次导致整页错误边界', true)
  await shot(page, 'flow1-week-anchor-crash.png')
  await page.getByRole('button', { name: '重新加载当前页面' }).click()
  await waitGrid(page)
  ok('错误边界「重新加载当前页面」可恢复', true)

  // 编辑常规课表
  await page.locator('div[role="group"][aria-label="课表模式"]').getByRole('button', { name: '编辑常规课表' }).click()
  await setRegularCell(page, '第1节', 1, '数学', '九(1)班')
  await setRegularCell(page, '第3节', 2, '数学', '九(2)班')
  await setRegularCell(page, '第5节', 3, '语文', '九(1)班')

  // 自定义时段
  await page.getByRole('button', { name: '自定义时段' }).click()
  await addCustomSlot(page, '午练', '12:30', '12:50', '第 4 节后')
  await addCustomSlot(page, '午休', '13:00', '13:50', '第 4 节后')
  await addCustomSlot(page, '延时', '18:00', '19:00', '第 8 节后')
  const slotList = await page.locator('.custom-slots__list').textContent()
  ok('三个自定义时段均出现在管理中', ['午练', '午休', '延时'].every((s) => slotList.includes(s)))

  // 午练时段周一格（仍在常规编辑模式）
  await setRegularCell(page, '午练', 1, '午练', '九(1)班')

  // 回到查看模式断言
  await page.locator('div[role="group"][aria-label="课表模式"]').getByRole('button', { name: '查看' }).click()
  const mon1 = await cell(page, '第1节', 1).textContent()
  ok('周一第1节 = 数学·九(1)班', mon1.includes('数学') && mon1.includes('九(1)班'), mon1.trim())
  const tue3 = await cell(page, '第3节', 2).textContent()
  ok('周二第3节 = 数学·九(2)班', tue3.includes('数学') && tue3.includes('九(2)班'), tue3.trim())
  const wed5 = await cell(page, '第5节', 3).textContent()
  ok('周三第5节 = 语文·九(1)班', wed5.includes('语文') && wed5.includes('九(1)班'), wed5.trim())
  const monNoon = await cell(page, '午练', 1).textContent()
  ok('周一午练格出现 九(1)班', monNoon.includes('九(1)班'), monNoon.trim())
  const gridFile = path.join(SHOTS, 'flow1-grid-element.png')
  await page.locator('table.timetable-grid').screenshot({ path: gridFile })
  screenshots.push(gridFile)

  const todayHead = page.locator('table.timetable-grid thead th.is-today')
  ok('本周含今天，今天列高亮带徽标', (await todayHead.count()) === 1 && (await todayHead.textContent()).includes('今天'),
    (await todayHead.count()) ? (await todayHead.textContent()).trim() : '无高亮列')

  await shot(page, 'flow1-timetable.png')

  // 持久化复核
  await page.reload({ waitUntil: 'domcontentloaded' })
  await waitGrid(page)
  const mon1After = await cell(page, '第1节', 1).textContent()
  const noonAfter = await cell(page, '午练', 1).textContent()
  ok('刷新后常规课与午练仍在', mon1After.includes('数学') && noonAfter.includes('九(1)班'))
}

async function flow2(page) {
  console.log('\n===== 流程二：临时调换与进度 =====')
  await page.locator('div[role="group"][aria-label="课表模式"]').getByRole('button', { name: '临时调整' }).click()

  const src = cell(page, '第3节', 2)
  const dst = cell(page, '第5节', 4)

  // 先尝试真实 HTML5 拖拽
  await src.dragTo(dst).catch(() => {})
  await page.waitForTimeout(1800)
  dragWorked = (await dst.textContent()).includes('数学')

  if (!dragWorked) {
    console.log('INFO  拖拽未生效，改用弹层手工等价路径')
    // 目标格（空格，adjust 模式点击直接进入临时课程表单）
    await dst.click()
    const dialog = page.getByRole('dialog')
    await dialog.getByLabel('临时课程').fill('数学')
    await dialog.getByLabel('班级（可留空）').fill('九(2)班')
    await dialog.getByRole('button', { name: '保存临时调整' }).click()
    await page.locator('text=已保存本周的临时调整').waitFor()
    // 源格：本周留空
    await cell(page, '第3节', 2).click()
    await dialog.getByRole('button', { name: '临时调整此格' }).click()
    await dialog.getByRole('button', { name: '本周此格留空' }).click()
    await page.locator('text=本周这一格已留空').waitFor()
  }

  const dstText = await cell(page, '第5节', 4).textContent()
  ok('周四第5节出现该课', dstText.includes('数学') && dstText.includes('九(2)班'), dstText.trim())
  ok('周四第5节带「临时」标记',
    await cell(page, '第5节', 4).locator('.timetable-grid__override-badge').isVisible())
  const srcText = await cell(page, '第3节', 2).textContent()
  ok('周二第3节本周变空', !srcText.includes('数学'), srcText.trim() || '(空)')
  await shot(page, 'flow2-adjust.png')

  // 下周恢复常规
  await page.getByRole('button', { name: '下一周' }).click()
  await page.locator('.week-navigator__title', { hasText: '第 4 周' }).waitFor()
  const nextDst = await cell(page, '第5节', 4).textContent()
  ok('下周周四第5节恢复为空', !nextDst.includes('数学'), nextDst.trim() || '(空)')
  const nextSrc = await cell(page, '第3节', 2).textContent()
  ok('下周周二第3节恢复常规数学', nextSrc.includes('数学') && nextSrc.includes('九(2)班'), nextSrc.trim())
  await shot(page, 'flow2-nextweek.png')

  await page.getByRole('button', { name: '本周', exact: true }).click()
  await page.locator('.week-navigator__title', { hasText: '第 3 周' }).waitFor()
  ok('切回本周临时调换仍生效', (await cell(page, '第5节', 4).textContent()).includes('数学'))

  // 回到查看模式记一笔
  await page.locator('div[role="group"][aria-label="课表模式"]').getByRole('button', { name: '查看' }).click()
  await cell(page, '第1节', 1).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByRole('button', { name: '记一笔' }).click()
  const prefilledClass = await dialog.getByLabel('班级', { exact: true }).inputValue()
  ok('记一笔预填班级', prefilledClass === '九(1)班', prefilledClass)
  await dialog.getByLabel('本节课内容').fill('第三章 二次函数 3.1 概念')
  await dialog.getByLabel('作业（可留空）').fill('P45 1-5')
  await dialog.getByRole('button', { name: '保存记录' }).click()
  await page.locator('text=已记下这节课的进度').waitFor()

  // 第二笔：周二第3节本周已被调空，空格也可记一笔（班级手填九(2)班）
  await cell(page, '第3节', 2).click()
  await dialog.getByRole('button', { name: '记一笔' }).click()
  await dialog.getByLabel('班级', { exact: true }).fill('九(2)班')
  await dialog.getByLabel('本节课内容').fill('3.1 练习讲评')
  await dialog.getByRole('button', { name: '保存记录' }).click()
  await page.locator('text=已记下这节课的进度').waitFor()

  // 双班进度对照
  const compare = page.locator('section[aria-label="双班进度对照"]')
  const toggleText = await compare.locator('.progress-compare__header button').textContent()
  if (toggleText.trim() === '展开') await compare.locator('.progress-compare__header button').click()
  const compareText = await compare.textContent()
  ok('进度对照含九(1)班记录', compareText.includes('九(1)班') && compareText.includes('第三章 二次函数 3.1 概念'))
  ok('进度对照含作业', compareText.includes('P45 1-5'))
  ok('进度对照含九(2)班记录', compareText.includes('九(2)班') && compareText.includes('3.1 练习讲评'))
  const compareFile = path.join(SHOTS, 'flow2-progress-section.png')
  await compare.screenshot({ path: compareFile })
  screenshots.push(compareFile)
  await shot(page, 'flow2-progress.png')

  // 持久化复核
  await page.reload({ waitUntil: 'domcontentloaded' })
  await waitGrid(page)
  ok('刷新后临时调换仍在', (await cell(page, '第5节', 4).textContent()).includes('数学'))
  const compareAfter = await page.locator('section[aria-label="双班进度对照"]').textContent()
  ok('刷新后进度记录仍在', compareAfter.includes('第三章 二次函数 3.1 概念') && compareAfter.includes('3.1 练习讲评'))
}

async function flow3(page) {
  console.log('\n===== 流程三：表格管理 =====')
  await page.getByRole('tab', { name: '表格' }).click()
  await page.getByRole('button', { name: '新建表格' }).click()
  await page.getByPlaceholder('如：研学回执统计').fill('研学回执统计')
  await page.getByRole('checkbox', { name: /九\(1\)班/ }).check()
  await page.getByRole('checkbox', { name: /九\(2\)班/ }).check()
  const wizardText = await page.locator('.table-wizard').textContent()
  ok('勾选两班显示共 10 人', wizardText.includes('已选 2 个班，共 10 人'), wizardText.match(/已选.*人。/)?.[0] ?? '')
  await page.getByRole('button', { name: '创建表格' }).click()

  await page.locator('.table-editor h2', { hasText: '研学回执统计' }).waitFor()

  // 加列（新表无列时网格不渲染，先建列再断言行数）
  await page.getByRole('button', { name: '列管理' }).click()
  const colEd = page.locator('section[aria-label="列管理"]')
  await colEd.getByLabel('新列列名').fill('已交')
  await colEd.getByLabel('新列类型').selectOption({ label: '打勾' })
  await colEd.getByRole('button', { name: '添加列' }).click()
  await page.locator('text=已添加列「已交」').waitFor()
  await colEd.getByLabel('新列列名').fill('备注')
  await colEd.getByLabel('新列类型').selectOption({ label: '文本' })
  await colEd.getByRole('button', { name: '添加列' }).click()
  await page.locator('text=已添加列「备注」').waitFor()
  const headText = await page.locator('.table-grid thead').textContent()
  ok('网格出现已交/备注两列', headText.includes('已交') && headText.includes('备注'))
  const rowCount = await page.locator('.table-grid tbody tr').count()
  ok('网格出现 10 行学生', rowCount === 10, `实际 ${rowCount} 行`)

  // 前 3 名学生打勾
  for (const name of ['测试学生01', '测试学生02', '测试学生03']) {
    const cb = page.getByRole('checkbox', { name: `${name}·已交` })
    await Promise.all([
      page.waitForResponse((r) => r.url().includes('/cells') && r.request().method() === 'PUT' && r.ok()),
      cb.check(),
    ])
  }
  ok('前 3 名学生已交打勾', await page.getByRole('checkbox', { name: '测试学生03·已交' }).isChecked())

  // 第 1 名备注
  const remark = page.getByRole('textbox', { name: '测试学生01·备注' })
  await remark.click()
  await remark.fill('已交纸质版')
  await Promise.all([
    page.waitForResponse((r) => r.url().includes('/cells') && r.request().method() === 'PUT' && r.ok()),
    remark.press('Tab'),
  ])
  ok('备注保存成功', true)
  await shot(page, 'flow3-grid.png')

  // 导出 CSV
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('button', { name: '导出 CSV' }).click(),
  ])
  const csvPath = path.join(SHOTS, download.suggestedFilename())
  await download.saveAs(csvPath)
  const csv = fs.readFileSync(csvPath, 'utf8')
  ok('CSV 带 BOM', csv.charCodeAt(0) === 0xfeff)
  const lines = csv.slice(1).split('\r\n').filter(Boolean)
  ok('CSV 表头正确', lines[0] === '姓名,班级,已交,备注', lines[0])
  ok('CSV 共 10 行数据', lines.length - 1 === 10, `实际 ${lines.length - 1} 行`)
  const r1 = lines[1].split(',')
  const r4 = lines[4].split(',')
  ok('CSV 前 3 行已交=1', [1, 2, 3].every((i) => lines[i].split(',')[2] === '1'))
  ok('CSV 第 4 行已交为空', r4[2] === '')
  ok('CSV 第 1 行备注正确', r1[3] === '已交纸质版' && r1[0] === '测试学生01', lines[1])

  // 归档
  await page.getByRole('button', { name: '归档' }).click()
  await page.locator('text=已归档，回到列表后').waitFor()
  await page.getByRole('button', { name: '返回列表' }).click()
  const archivedSection = page.locator('section[aria-label="已归档的表格"]')
  await archivedSection.locator('text=研学回执统计').waitFor()
  ok('已归档分区出现该表', (await archivedSection.textContent()).includes('研学回执统计'))
  const activeSection = page.locator('section[aria-label="进行中的表格"]')
  ok('进行中分区为空', (await activeSection.textContent()).includes('暂无进行中的表格'))
  await shot(page, 'flow3-archived.png')

  // 持久化复核
  await page.reload({ waitUntil: 'domcontentloaded' })
  await page.locator('section[aria-label="已归档的表格"]').waitFor()
  ok('刷新后归档状态保持',
    (await page.locator('section[aria-label="已归档的表格"]').textContent()).includes('研学回执统计'))
}

async function main() {
  fs.mkdirSync(SHOTS, { recursive: true })
  const browser = await chromium.launch({ headless: true })
  const context = await browser.newContext({
    baseURL: BASE,
    viewport: { width: 1440, height: 900 },
    acceptDownloads: true,
  })
  const page = await context.newPage()
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text())
  })
  page.on('pageerror', (err) => consoleErrors.push(`pageerror: ${err.message}`))

  try {
    await flow1(page)
  } catch (e) {
    ok('流程一执行', false, e.message.split('\n')[0])
    await shot(page, 'flow1-error.png').catch(() => {})
  }
  try {
    await flow2(page)
  } catch (e) {
    ok('流程二执行', false, e.message.split('\n')[0])
    await shot(page, 'flow2-error.png').catch(() => {})
  }
  try {
    await flow3(page)
  } catch (e) {
    ok('流程三执行', false, e.message.split('\n')[0])
    await shot(page, 'flow3-error.png').catch(() => {})
  }

  await browser.close()

  const failed = results.filter((r) => !r.pass)
  console.log('\n===== 汇总 =====')
  console.log(`断言 ${results.length} 项，通过 ${results.length - failed.length}，失败 ${failed.length}`)
  console.log(`HTML5 拖拽自动化: ${dragWorked ? '真实生效' : '未生效（已走弹层等价路径）'}`)
  console.log(`控制台错误 ${consoleErrors.length} 条`)
  consoleErrors.slice(0, 10).forEach((e) => console.log(`  CONSOLE: ${e}`))
  fs.writeFileSync(
    path.join(SHOTS, 'acceptance-results.json'),
    JSON.stringify({ results, dragWorked, consoleErrors, screenshots }, null, 2),
  )
  process.exit(failed.length ? 1 : 0)
}

main().catch((e) => {
  console.error('FATAL', e)
  process.exit(2)
})
