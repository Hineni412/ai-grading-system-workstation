import { expect, test, type Page, type Route, type TestInfo } from '@playwright/test';

const STORAGE_KEY = 'ai-grading:selected-session:v1'

const sessions = [
  {
    id: 7,
    name: '七年级数学期末质量监测（匿名合成数据）',
    status: 'grading',
    curriculum_volume_id: 'volume-1',
    is_deleted: false,
    deleted_at: null,
    created_at: '2026-07-14T08:00:00Z',
    updated_at: '2026-07-15T09:30:00Z',
  },
  {
    id: 8,
    name: '八年级物理单元检测（匿名合成数据）',
    status: 'completed',
    curriculum_volume_id: 'volume-1',
    is_deleted: false,
    deleted_at: null,
    created_at: '2026-07-12T08:00:00Z',
    updated_at: '2026-07-15T10:00:00Z',
  },
]

const viewports = [
  { width: 1024, height: 768 },
  { width: 1280, height: 720 },
  { width: 1366, height: 768 },
  { width: 1440, height: 900 },
  { width: 1920, height: 1080 },
]

interface MockOptions {
  overviewFailure?: boolean
  bankFailure?: boolean
  empty?: boolean
  phaseTwo?: boolean
  trainingFailure?: boolean
}

interface RequestLog {
  method: string
  pathname: string
}

function progress(sessionId: number) {
  const completed = sessionId === 8
  return {
    total_papers: completed ? 40 : 36,
    matched_papers: completed ? 40 : 35,
    unmatched_papers: completed ? 0 : 1,
    graded_papers: completed ? 40 : 12,
    failed_papers: completed ? 0 : 1,
    grading_papers: completed ? 0 : 2,
    needs_human_review: completed ? 0 : 3,
    absent_students: 0,
    scan_issue_students: completed ? 0 : 1,
    progress_percent: completed ? 100 : 33.33,
  }
}

function overview(sessionId: number) {
  const session = sessions.find((item) => item.id === sessionId) ?? sessions[0]!
  return {
    current_session: session,
    personal_reports: { current: 8, stale: 0, missing: 4 },
    progress: progress(sessionId),
    review: sessionId === 8 ? { question_count: 0, item_count: 0 } : { question_count: 2, item_count: 3 },
    anomalies: sessionId === 8
      ? { unmatched_papers: 0, scan_issue_students: 0, failed_papers: 0 }
      : { unmatched_papers: 1, scan_issue_students: 1, failed_papers: 1 },
    recent_jobs: [],
    recent_sessions: sessions
      .slice()
      .map((item) => ({ session: item, progress: progress(item.id) })),
    updated_at: sessionId === 8 ? '2026-07-15T10:05:00Z' : '2026-07-15T09:35:00Z',
  }
}

function fulfillJson(route: Route, body: unknown): Promise<void> {
  return route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

function configSource(sessionId: number) {
  return {
    session_id: sessionId,
    source_id: 'a'.repeat(32),
    source_revision: 'b'.repeat(64),
    safe_filename: 'synthetic-exam.pdf',
    suffix: '.pdf',
    size_bytes: 1,
    sha256_prefix: 'c'.repeat(12),
    parse_state: 'ready',
    questions: [],
  }
}

function configEditor(sessionId: number) {
  return {
    session_id: sessionId,
    configured: false,
    revision: 'd'.repeat(64),
    rows: [],
    total_score: 0,
    issues: [],
    source: null,
  }
}

function curriculumCatalog() {
  const volumes = [
    ['七年级', '上学期'],
    ['七年级', '下学期'],
    ['八年级', '上学期'],
    ['八年级', '下学期'],
    ['九年级', '上学期'],
  ] as const
  return {
    schema_version: 2,
    catalog_id: 'bnu-math-2024',
    knowledge_standard_id: 'bnu-math-2024-curriculum-knowledge-v2',
    publisher: '北京师范大学出版社',
    subject: '初中数学',
    edition: '2024',
    statistics: {
      raw_nodes: 5,
      excluded_nodes: 0,
      retained_nodes: 5,
      chapters: 5,
      sections: 0,
      knowledge_points: 0,
    },
    volumes: volumes.map(([grade, semester], index) => ({
      id: `volume-${index + 1}`,
      order: index + 1,
      label: `${grade}${semester === '上学期' ? '上册' : '下册'}`,
      grade,
      semester,
      textbook_version: '北师大版2024',
      source: { provider: '组卷网' },
      statistics: { raw_nodes: 1, excluded_nodes: 0, retained_nodes: 1 },
      chapters: [{
        id: `chapter-${index + 1}`,
        knowledge_id: `chapter-${index + 1}`,
        order: 1,
        number: '第一章',
        title: `测试章节${index + 1}`,
        label: `第一章 测试章节${index + 1}`,
        kind: 'chapter',
        display_name: `${grade}${semester === '上学期' ? '上册' : '下册'}｜第一章 测试章节${index + 1}`,
        source_ref: {
          node_id: `node-${index + 1}`,
          relative_url: `/czsx/zj${index + 1}`,
        },
        exam_scope_values: [
          `${grade}${semester === '上学期' ? '上册' : '下册'} 测试范围${index + 1}`,
        ],
        sections: [],
      }],
    })),
  }
}

async function installSyntheticApi(
  page: Page,
  options: MockOptions = {},
): Promise<RequestLog[]> {
  const requests: RequestLog[] = []

  page.on('request', (request) => {
    const url = new URL(request.url())
    if (url.pathname.startsWith('/api/')) requests.push({ method: request.method(), pathname: url.pathname })
  })

  await page.route(/^https?:\/\/[^/]+\/api\//, async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const { pathname } = url

    if (pathname === '/api/sessions') {
      await fulfillJson(route, { items: options.empty ? [] : sessions, total: options.empty ? 0 : sessions.length })
      return
    }
    const activeSourceMatch = pathname.match(/^\/api\/sessions\/(\d+)\/config\/sources\/active$/)
    if (request.method() === 'GET' && activeSourceMatch) {
      await fulfillJson(route, configSource(Number(activeSourceMatch[1])))
      return
    }
    const editorMatch = pathname.match(/^\/api\/sessions\/(\d+)\/config\/editor$/)
    if (request.method() === 'GET' && editorMatch) {
      await fulfillJson(route, configEditor(Number(editorMatch[1])))
      return
    }
    if (pathname === '/api/question-bank/curriculum') {
      await fulfillJson(route, curriculumCatalog())
      return
    }
    if (pathname === '/api/workbench/overview') {
      if (options.overviewFailure) {
        await route.fulfill({ status: 503, body: 'private synthetic failure' })
        return
      }
      const sessionId = Number(url.searchParams.get('session_id') ?? 7)
      const data = overview(sessionId)
      if (options.phaseTwo) data.personal_reports.stale = 3
      await fulfillJson(route, options.empty ? { ...data, current_session: null, progress: null, review: null, anomalies: null, personal_reports: null, recent_sessions: [] } : data)
      return
    }
    const readiness = pathname.match(/^\/api\/sessions\/(\d+)\/regions\/readiness$/)
    if (readiness) {
      await fulfillJson(route, { session_id: Number(readiness[1]), scoring_configured: true, template_present: true, template_ready: true })
      return
    }
    if (/^\/api\/sessions\/\d+\/question-bank-status$/.test(pathname)) {
      if (options.bankFailure) { await route.fulfill({ status: 503, body: 'TEST-bank-failure' }); return }
      await fulfillJson(route, { question_count: 1, tagged_count: 0, evidence_count: 0, criteria_count: 0, complete_count: 0,
        pending_taxonomy_count: 0, unlinked_skill_count: options.phaseTwo ? 1 : 0,
        incomplete_question_ids: [1], incomplete_source_refs: ['Q1'] })
      return
    }
    if (pathname === '/api/training/pending-summary') {
      if (options.trainingFailure) { await route.fulfill({ status: 503, body: 'TEST-training-failure' }); return }
      await fulfillJson(route, { items: options.phaseTwo ? [{ draft_id: 'd'.repeat(64), draft_name: '2026-10-03 22:30:00 训练卷（测试）',
        scan_page_count: 2, review_submission_count: 3, publish_submission_count: 1 }] : [] })
      return
    }
    if (pathname === '/api/training/overview') {
      await fulfillJson(route, { scope: { mode: 'all', student_ids: [] },
        exam_scope: { mode: 'semester', curriculum_volume_id: 'volume-1', session_ids: [7], sessions: [{ session_id: 7, session_name: 'TEST-home' }] },
        warnings: [], nodes: [{ knowledge_key: 'skill-1', display_name: '册｜第一章｜技能·列等式', kind: 'skill',
          chapter_key: '', section_key: '', group_mastery: .4, evidence_student_count: 10,
          distribution: { weak: 4, unsteady: 2, stable: 4, insufficient: 0 }, students: [] }], students: [],
        summary: { student_count: 10, evidence_student_count: 10, exam_student_count: 10, exam_score_rate: .5,
          topic_count: 0, skill_count: 1, weak_topic_count: 0, weak_skill_count: 1 } })
      return
    }
    if (pathname === '/api/jobs') {
      await fulfillJson(route, { items: [], total: 0, page: 1, page_size: 20, total_pages: 0 })
      return
    }
    await route.fulfill({ status: 418, body: `unexpected synthetic API request: ${request.method()} ${pathname}` })
  })

  return requests
}

function trackBrowserErrors(page: Page) {
  const pageErrors: Error[] = []
  const consoleErrors: string[] = []
  page.on('pageerror', (error) => pageErrors.push(error))
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  return { pageErrors, consoleErrors }
}

async function openWorkbench(page: Page, sessionId = 7): Promise<void> {
  await page.addInitScript(([key, value]) => localStorage.setItem(key, value), [STORAGE_KEY, String(sessionId)])
  await page.addInitScript(() => localStorage.setItem('ai-grading:curriculum-scope:v1', 'volume-1'))
  await page.goto('/workbench')
  await expect(page.getByRole('heading', { name: '工作台', exact: true })).toBeVisible()
}

async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  expect(await page.evaluate(() => ({
    document: document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    body: document.body.scrollWidth <= document.body.clientWidth,
  }))).toEqual({ document: true, body: true })
}

function expectReadOnlyRequests(requests: RequestLog[]): void {
  expect(requests.length).toBeGreaterThan(0)
  for (const request of requests) {
    expect(request.method, `${request.method} ${request.pathname}`).toBe(request.pathname === '/api/training/overview' ? 'POST' : 'GET')
    expect(request.pathname).not.toMatch(/(?:submit|confirm|cancel|score|grading\/jobs)/)
  }
}

async function captureVisualEvidence(page: Page, testInfo: TestInfo, width: number): Promise<void> {
  if (![1024, 1280, 1440, 1920].includes(width)) return
  await page.screenshot({
    path: testInfo.outputPath(`workbench-${width}.png`),
    fullPage: false,
    animations: 'disabled',
  })
}


for (const viewport of viewports) {
  test(`${viewport.width}x${viewport.height} matches the home panels without overlap`, async ({ page }, testInfo) => {
    const errors = trackBrowserErrors(page)
    const requests = await installSyntheticApi(page)
    await page.setViewportSize(viewport)
    await openWorkbench(page)
    await expect(page.locator('[data-todo="bank"]')).toBeVisible()
    await expect(page.locator('.workbench-todo [data-variant="primary"]')).toHaveCount(1)
    await expect(page.locator('.workbench-current')).toContainText('36 份答卷')
    await expect(page.locator('.workbench-insight')).toContainText('4/10 人明显薄弱')
    await expect(page.locator('.workbench-recent tbody tr')).toHaveCount(2)
    await expect(page.locator('.workbench-recent tr.is-current')).toContainText('当前')
    await expect(page.locator('.step-progress--vertical')).toContainText('3 项待复核')
    await expect(page.locator('.workbench-pulse')).toHaveCount(0)
    const boxes = await page.locator('.workbench-panel').evaluateAll(elements => elements.map(el => {
      const r = el.getBoundingClientRect(); return { x: r.x, y: r.y, right: r.right, bottom: r.bottom }
    }))
    if (viewport.width >= 1200) {
      expect(boxes[1]!.x).toBeGreaterThanOrEqual(boxes[0]!.right)
      expect(boxes[3]!.y - boxes[0]!.bottom).toBeLessThanOrEqual(17)
      await page.locator('.workbench-grid').evaluate(el => (el as HTMLElement).style.minHeight = '1800px')
      await page.locator('.main-workspace').evaluate(el => el.scrollTop = 180)
      await expect(page.locator('.workbench-sidebar')).toHaveCSS('position', 'sticky')
      const sticky = await page.locator('.workbench-sidebar').boundingBox()
      expect(sticky!.y).toBeGreaterThanOrEqual(15)
      expect(sticky!.y).toBeLessThanOrEqual(18)
      await page.locator('.main-workspace').evaluate(el => el.scrollTop = 0)
      await page.locator('.workbench-grid').evaluate(el => (el as HTMLElement).style.minHeight = '')
    } else {
      expect(boxes[1]!.y).toBeGreaterThanOrEqual(boxes[0]!.bottom)
      expect(boxes[2]!.y).toBeGreaterThanOrEqual(boxes[1]!.bottom)
      expect(boxes[3]!.y).toBeGreaterThanOrEqual(boxes[2]!.bottom)
    }
    await expectNoHorizontalOverflow(page)
    await captureVisualEvidence(page, testInfo, viewport.width)
    expectReadOnlyRequests(requests)
    expect(errors.pageErrors).toEqual([])
    expect(errors.consoleErrors).toEqual([])
  })
}

test('second phase keeps units, report status and training draft entry visible', async ({ page }, testInfo) => {
  const requests = await installSyntheticApi(page, { phaseTwo: true })
  await page.setViewportSize({ width: 1024, height: 768 })
  await openWorkbench(page)
  await expect(page.locator('.workbench-todo')).toContainText('核对 2 页训练卷扫描')
  await expect(page.locator('.workbench-todo')).toContainText('判定 3 份')
  await expect(page.locator('.workbench-todo')).toContainText('证据 1 份')
  await expect(page.locator('[data-todo="reports"]')).toContainText('3 人需重新生成')
  await expect(page.locator('[data-todo="reports"]')).toContainText('8 人已是最新')
  await expect(page.locator('[data-todo="unlinked"]')).toContainText('本场 1 道题未挂技能')
  await expect(page.locator('.workbench-todo [data-variant="primary"]')).toHaveCount(1)
  await expectNoHorizontalOverflow(page)
  await captureVisualEvidence(page, testInfo, 1024)
  await page.locator('[data-todo="reports"]').scrollIntoViewIfNeeded()
  await page.screenshot({ path: testInfo.outputPath('workbench-1024-reports.png'), animations: 'disabled' })
  await page.locator('.workbench-sidebar').scrollIntoViewIfNeeded()
  await page.screenshot({ path: testInfo.outputPath('workbench-1024-sidebar.png'), animations: 'disabled' })
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.locator('.main-workspace').evaluate(el => el.scrollTop = 0)
  await expectNoHorizontalOverflow(page)
  await captureVisualEvidence(page, testInfo, 1440)
  expectReadOnlyRequests(requests)
  await page.locator(`[data-todo="scan-${'d'.repeat(64)}"]`).getByRole('button').click()
  await expect(page).toHaveURL(new RegExp(`/training\\?mode=paper&draft=${'d'.repeat(64)}$`))
})

test('training source failure preserves exam panels', async ({ page }) => {
  await installSyntheticApi(page, { trainingFailure: true })
  await openWorkbench(page)
  await expect(page.locator('.workbench-todo')).toContainText('训练回收待办暂时无法读取')
  await expect(page.locator('[data-todo="review"]')).toBeVisible()
  await expect(page.locator('.workbench-current')).toContainText('3 项待复核')
})

test('recent exam name switches the current exam and preserves the home route', async ({ page }) => {
  const requests = await installSyntheticApi(page)
  await openWorkbench(page)
  await page.locator('.workbench-exams-name button').filter({ hasText: '八年级物理' }).click()
  await expect(page.locator('.workbench-current')).toContainText('八年级物理')
  await expect(page.locator('[data-todo="assembly"]')).toBeVisible()
  await expect(page).toHaveURL(/\/workbench$/)
  expectReadOnlyRequests(requests)
})

test('source errors show retries and preserve unrelated panels', async ({ page }) => {
  await installSyntheticApi(page, { bankFailure: true })
  await openWorkbench(page)
  await expect(page.locator('.workbench-todo')).toContainText('题库资料状态暂时无法读取')
  await expect(page.locator('[data-todo="bank"]')).toHaveCount(0)
  await expect(page.locator('[data-todo="review"]')).toBeVisible()
  await expect(page.locator('.workbench-insight')).toContainText('4/10 人明显薄弱')
})

test('failed overview and no exam give accurate empty and failure states', async ({ page }) => {
  await installSyntheticApi(page, { overviewFailure: true })
  await openWorkbench(page)
  await expect(page.locator('.workbench-content > .feedback-banner')).toContainText('考试概况暂时无法读取')
  await expect(page.locator('.workbench-insight')).toContainText('4/10 人明显薄弱')
  await page.unroute(/^https?:\/\/[^/]+\/api\//)
  await installSyntheticApi(page, { empty: true })
  await page.evaluate(() => localStorage.removeItem('ai-grading:selected-session:v1'))
  await page.reload()
  await expect(page.locator('.workbench-current')).toContainText('尚未选择考试')
  await expect(page.locator('.workbench-todo')).toContainText('今天没有待处理的事项')
})
