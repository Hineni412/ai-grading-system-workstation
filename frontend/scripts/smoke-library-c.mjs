/* 只读冒烟：方案 C 资料库页渲染检查（不做任何写操作） */
import { chromium } from 'playwright'

const BASE = 'http://127.0.0.1:8035/teaching-prep?view=library'
const OUT = '../tmp/proto_check'
const errors = []

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`))
page.on('console', (m) => { if (m.type() === 'error') errors.push(`console: ${m.text()}`) })

await page.goto(BASE, { waitUntil: 'networkidle', timeout: 30000 })
await page.waitForTimeout(1500)
await page.screenshot({ path: `${OUT}/smoke-1-default.png`, fullPage: false })

const bodyText = await page.textContent('body')
console.log('HAS_RAIL:', bodyText.includes('资料') )
console.log('HAS_PROGRESS:', /导入|本机处理|课时树/.test(bodyText))

// 点击左栏第一个章文件夹（只读选中）
const chapterItem = page.locator('.tp-rail__item').first()
if (await chapterItem.count()) {
  await chapterItem.click()
  await page.waitForTimeout(800)
  await page.screenshot({ path: `${OUT}/smoke-2-chapter.png`, fullPage: false })
  console.log('CHAPTER_CLICKED:', await chapterItem.textContent())
} else {
  console.log('NO_RAIL_ITEMS')
}

// 点击"本学期课时树"条目
const treeItem = page.locator('.tp-rail__item', { hasText: '课时树' }).first()
if (await treeItem.count()) {
  await treeItem.click()
  await page.waitForTimeout(800)
  await page.screenshot({ path: `${OUT}/smoke-3-tree.png`, fullPage: false })
  console.log('TREE_CLICKED')
} else {
  console.log('NO_TREE_ITEM')
}

// 点击教材条目
const bookItem = page.locator('.tp-rail__item', { hasText: '教材' }).first()
if (await bookItem.count()) {
  await bookItem.click()
  await page.waitForTimeout(800)
  await page.screenshot({ path: `${OUT}/smoke-4-textbook.png`, fullPage: false })
  console.log('TEXTBOOK_CLICKED')
} else {
  console.log('NO_TEXTBOOK_ITEM')
}

console.log('CONSOLE_ERRORS:', errors.length ? errors.slice(0, 5) : 'none')
await browser.close()
