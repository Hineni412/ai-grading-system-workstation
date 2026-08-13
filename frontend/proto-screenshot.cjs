/* 原型截图脚本：7 页 full-page + results 明细 tab */
const { chromium } = require('playwright');
const path = require('path');

const BASE = 'file:///D:/AI阅卷系统_工作机版_v1.5.0/frontend/prototypes/layout-refresh-20260811';
const OUT = path.join(__dirname, 'prototypes', 'layout-refresh-20260811');
const PAGES = ['workbench', 'session-config', 'grading-run', 'results', 'question-bank', 'assembly', 'knowledge'];

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  for (const p of PAGES) {
    await page.goto(`${BASE}/${p}.html`);
    await page.waitForTimeout(120);
    await page.screenshot({ path: path.join(OUT, `_shot-${p}.png`), fullPage: true });
    console.log('captured', p);
  }
  await page.goto(`${BASE}/results.html`);
  await page.click('.tabs a[data-view="detail"]');
  await page.waitForTimeout(200);
  await page.screenshot({ path: path.join(OUT, '_shot-results-detail.png'), fullPage: true });
  console.log('captured results-detail');
  await browser.close();
})();
