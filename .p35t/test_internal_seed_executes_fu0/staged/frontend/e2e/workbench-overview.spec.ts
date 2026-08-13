import { expect, test, type Page, type Request, type Route, type TestInfo } from '@playwright/test'

const STORAGE_KEY = 'ai-grading:selected-session:v1'

const sessions = [
  {
    id: 7,
    name: '七年级数学期末质量监测（匿名合成数据）',
    status: 'grading',
    is_deleted: false,
    deleted_at: null,
    created_at: '2026-07-14T08:00:00Z',
    updated_at: '2026-07-15T09:30:00Z',
  },
  {
    id: 8,
    name: '八年级物理单元检测（匿名合成数据）',
    status: 'completed',
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
  analysisFailure?: boolean
  overviewFailureAfterSuccess?: boolean
  delaySession7?: boolean
  paginated?: boolean
}

interface RequestLog {
  method: string
  pathname: string
}

interface SyntheticApi {
  requests: RequestLog[]
  delayedSession7Settled: Promise<void>
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
    progress: progress(sessionId),
    review: sessionId === 8 ? { question_count: 0, item_count: 0 } : { question_count: 2, item_count: 3 },
    anomalies: sessionId === 8
      ? { unmatched_papers: 0, scan_issue_students: 0, failed_papers: 0 }
      : { unmatched_papers: 1, scan_issue_students: 1, failed_papers: 1 },
    recent_jobs: sessionId === 8 ? [] : [{
      id: 41,
      job_type: 'grading',
      status: 'running',
      progress: 0.58,
      stage: '评分中',
      detail: '正在读取匿名合成答卷',
      created_at: '2026-07-15T09:00:00Z',
      started_at: '2026-07-15T09:01:00Z',
      updated_at: '2026-07-15T09:32:00Z',
      finished_at: null,
    }],
    recent_sessions: sessions
      .filter((item) => item.id !== sessionId)
      .map((item) => ({ session: item, progress: progress(item.id) })),
    updated_at: sessionId === 8 ? '2026-07-15T10:05:00Z' : '2026-07-15T09:35:00Z',
  }
}

function questionAnalysis(sessionId: number, className: string | null) {
  const scopeClass = className?.trim() || null
  return {
    scope: { session_id: sessionId, class_name: scopeClass, question_id: null },
    classes: ['七年级一班', '七年级二班'],
    items: [{
      class_name: scopeClass ?? '全部班级',
      question_id: 'Q1',
      max_score: 10,
      score_rate: sessionId === 8 ? 91 : 82.5,
      average_score: sessionId === 8 ? 9.1 : 8.25,
      deduction_count: 4,
      attempt_count: 12,
      metric_status: 'ready',
    }, {
      class_name: scopeClass ?? '全部班级',
      question_id: 'Q2',
      max_score: null,
      score_rate: null,
      average_score: null,
      deduction_count: 0,
      attempt_count: 1,
      metric_status: 'missing_max_score',
    }],
    total: 2,
    page: 1,
    page_size: 100,
    total_pages: 1,
  }
}

function studentAnalysis(sessionId: number, questionId: string, className: string | null) {
  const scopeClass = className?.trim() || null
  const isSecond = questionId === 'Q2'
  return {
    scope: { session_id: sessionId, class_name: scopeClass, question_id: questionId },
    items: [{
      result_id: sessionId * 10 + (isSecond ? 2 : 1),
      detail_id: sessionId * 100 + (isSecond ? 2 : 1),
      student_id: sessionId * 10 + 7,
      student_code: `S-${sessionId}-17`,
      student_name: isSecond ? '匿名学生乙' : '匿名学生甲（长名称验证布局）',
      class_name: scopeClass ?? '七年级一班',
      question_id: questionId,
      score_awarded: isSecond ? 0 : 8,
      max_score: isSecond ? null : 10,
      deduction_amount: isSecond ? null : 2,
      deduction_reason: isSecond ? null : '计算过程漏写单位',
      needs_review: true,
      evidence_url: `/api/sessions/${sessionId}/results/${sessionId * 10 + (isSecond ? 2 : 1)}/details/${sessionId * 100 + (isSecond ? 2 : 1)}/crop`,
    }],
    total: 1,
    page: 1,
    page_size: 100,
    total_pages: 1,
  }
}

function paginatedQuestionAnalysis(sessionId: number, className: string | null, page: number) {
  const base = questionAnalysis(sessionId, className)
  const start = page === 1 ? 1 : 101
  const count = page === 1 ? 100 : 2
  return {
    ...base,
    items: Array.from({ length: count }, (_, index) => ({
      ...base.items[0]!,
      question_id: `Q${start + index}`,
    })),
    total: 102,
    page,
    total_pages: 2,
  }
}

function paginatedStudentAnalysis(
  sessionId: number,
  questionId: string,
  className: string | null,
  page: number,
) {
  const base = studentAnalysis(sessionId, questionId, className)
  const start = page === 1 ? 1 : 101
  const count = page === 1 ? 100 : 1
  return {
    ...base,
    items: Array.from({ length: count }, (_, index) => {
      const id = start + index
      return {
        ...base.items[0]!,
        result_id: id,
        detail_id: id,
        student_id: id,
        student_name: id === 101 ? '后续学生 101' : `学生 ${id}`,
        evidence_url: `/api/sessions/${sessionId}/results/${id}/details/${id}/crop`,
      }
    }),
    total: 101,
    page,
    total_pages: 2,
  }
}

function graphRows(sessionId: number, className: string) {
  return {
    scope: {
      mode: 'class',
      student_ids: Array.from({ length: 101 }, (_, index) => String(index + 1)),
      class_id: className,
    },
    exam_scope: {
      mode: 'current',
      session_ids: [sessionId],
      sessions: [{ session_id: sessionId, session_name: sessions.find((item) => item.id === sessionId)!.name }],
    },
    rows: [],
    nodes: [{
      knowledge_key: 'knowledge_point:fraction',
      knowledge_label: '分数运算',
      student_count: 12,
      item_count: 18,
      deduction_count: 5,
      average_mastery: 0.78,
      tag_context: { 章节: ['数与代数'] },
      error_counts: {},
    }],
    edges: [],
    coverage: { covered_items: 18, total_items: 20, missing_items: {} },
    warnings: ['2 份作答未关联知识标签'],
    diagnosis_identity: 'question_tag',
  }
}

function graphEvidence(sessionId: number, className: string) {
  const common = graphRows(sessionId, className)
  return {
    scope: common.scope,
    exam_scope: common.exam_scope,
    knowledge_key: 'knowledge_point:fraction',
    knowledge_label: '分数运算',
    items: [{
      student_id: sessionId * 10 + 7,
      student_code: `S-${sessionId}-17`,
      student_name: '匿名学生甲',
      class_id: className,
      knowledge_key: 'knowledge_point:fraction',
      knowledge_label: '分数运算',
      session_id: sessionId,
      session_name: sessions.find((item) => item.id === sessionId)!.name,
      question_id: 'Q1',
      bank_question_id: 101,
      score_awarded: 8,
      full_score: 10,
      score_rate: 0.8,
      tag_context: { 章节: ['数与代数'] },
      actionable_reasons: ['计算过程漏写单位'],
      error_counts: {},
    }],
    total: 1,
    page: 1,
    page_size: 20,
    total_pages: 1,
    coverage: common.coverage,
    warnings: [],
    diagnosis_identity: 'question_tag',
  }
}

function paginatedGraphEvidence(sessionId: number, className: string, page: number) {
  const base = graphEvidence(sessionId, className)
  const start = page === 1 ? 1 : 21
  const count = page === 1 ? 20 : 1
  return {
    ...base,
    items: Array.from({ length: count }, (_, index) => {
      const id = start + index
      return {
        ...base.items[0]!,
        student_id: id,
        student_code: `S-${id}`,
        student_name: id === 21 ? '后续证据学生' : `证据学生 ${id}`,
        question_id: `Q${id}`,
        bank_question_id: id,
      }
    }),
    total: 21,
    page,
    total_pages: 2,
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

function sessionIdFrom(url: URL): number {
  const match = url.pathname.match(/\/sessions\/(\d+)/)
  return Number(match?.[1] ?? url.searchParams.get('session_id') ?? 7)
}

function classNameFrom(request: Request): string {
  const body = request.postDataJSON() as { scope?: { class_id?: string } } | null
  return body?.scope?.class_id?.trim() || '七年级一班'
}

async function installSyntheticApi(
  page: Page,
  options: MockOptions = {},
): Promise<SyntheticApi> {
  const requests: RequestLog[] = []
  let overviewCalls = 0
  let delayedSession7Completions = 0
  let resolveDelayedSession7!: () => void
  const delayedSession7Settled = new Promise<void>((resolve) => {
    resolveDelayedSession7 = resolve
  })
  if (!options.delaySession7) resolveDelayedSession7()

  async function delaySession7(sessionId: number): Promise<void> {
    if (!options.delaySession7 || sessionId !== 7) return
    await new Promise((resolve) => setTimeout(resolve, 250))
    delayedSession7Completions += 1
    if (delayedSession7Completions >= 2) resolveDelayedSession7()
  }

  page.on('request', (request) => {
    const url = new URL(request.url())
    if (url.pathname.startsWith('/api/')) requests.push({ method: request.method(), pathname: url.pathname })
  })

  await page.route(/^https?:\/\/[^/]+\/api\//, async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const { pathname } = url

    if (pathname === '/api/sessions') {
      await fulfillJson(route, { items: sessions, total: sessions.length })
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
    if (pathname === '/api/workbench/overview') {
      overviewCalls += 1
      if (options.overviewFailureAfterSuccess && overviewCalls > 1) {
        await route.fulfill({ status: 503, body: 'private synthetic failure' })
        return
      }
      const sessionId = Number(url.searchParams.get('session_id') ?? 7)
      await delaySession7(sessionId)
      await fulfillJson(route, overview(sessionId))
      return
    }
    if (/^\/api\/sessions\/\d+\/analysis\/questions$/.test(pathname)) {
      if (options.analysisFailure) {
        await route.fulfill({ status: 503, body: 'private synthetic analysis failure' })
        return
      }
      const sessionId = sessionIdFrom(url)
      await delaySession7(sessionId)
      const pageNumber = Number(url.searchParams.get('page') ?? 1)
      await fulfillJson(route, options.paginated
        ? paginatedQuestionAnalysis(sessionId, url.searchParams.get('class_name'), pageNumber)
        : questionAnalysis(sessionId, url.searchParams.get('class_name')))
      return
    }
    if (/^\/api\/sessions\/\d+\/analysis\/questions\/[^/]+\/students$/.test(pathname)) {
      const sessionId = sessionIdFrom(url)
      const questionId = decodeURIComponent(pathname.split('/').at(-2) ?? 'Q1')
      await delaySession7(sessionId)
      const pageNumber = Number(url.searchParams.get('page') ?? 1)
      await fulfillJson(route, options.paginated
        ? paginatedStudentAnalysis(sessionId, questionId, url.searchParams.get('class_name'), pageNumber)
        : studentAnalysis(sessionId, questionId, url.searchParams.get('class_name')))
      return
    }
    if (/^\/api\/sessions\/\d+\/anomalies$/.test(pathname)) {
      const pageNumber = Number(url.searchParams.get('page') ?? 1)
      if (options.paginated) {
        const start = pageNumber === 1 ? 1 : 101
        const count = pageNumber === 1 ? 100 : 1
        await fulfillJson(route, {
          items: Array.from({ length: count }, (_, index) => {
            const id = start + index
            return {
              anomaly_id: `unmatched:${id}`,
              anomaly_type: 'unmatched_paper',
              display_name: id === 101 ? '后续异常 101' : `异常 ${id}`,
              student_code: null,
              class_name: null,
              status: 'unmatched',
              detail: null,
              created_at: '2026-07-15T08:00:00Z',
            }
          }),
          total: 101,
          page: pageNumber,
          page_size: 100,
          total_pages: 2,
        })
        return
      }
      await fulfillJson(route, {
        items: [{
          anomaly_id: 'unmatched:10',
          anomaly_type: 'unmatched_paper',
          display_name: '匿名答卷 10',
          student_code: null,
          class_name: null,
          status: 'unmatched',
          detail: null,
          created_at: '2026-07-15T08:00:00Z',
        }],
        total: 1,
        page: 1,
        page_size: 100,
        total_pages: 1,
      })
      return
    }
    if (pathname === '/api/jobs') {
      await fulfillJson(route, { items: [], total: 0, page: 1, page_size: 20, total_pages: 0 })
      return
    }
    if (pathname === '/api/graph/rows') {
      await fulfillJson(route, graphRows(sessionIdFromGraphRequest(request), classNameFrom(request)))
      return
    }
    if (pathname === '/api/graph/evidence') {
      const body = request.postDataJSON() as { page?: number } | null
      await fulfillJson(route, options.paginated
        ? paginatedGraphEvidence(sessionIdFromGraphRequest(request), classNameFrom(request), body?.page ?? 1)
        : graphEvidence(sessionIdFromGraphRequest(request), classNameFrom(request)))
      return
    }
    await route.fulfill({ status: 418, body: `unexpected synthetic API request: ${request.method()} ${pathname}` })
  })

  return { requests, delayedSession7Settled }
}

function sessionIdFromGraphRequest(request: Request): number {
  const body = request.postDataJSON() as { exam_scope?: { session_ids?: number[] } } | null
  return body?.exam_scope?.session_ids?.[0] ?? 7
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

function expectOnlyExpected503(errors: ReturnType<typeof trackBrowserErrors>): void {
  expect(errors.pageErrors).toEqual([])
  expect(errors.consoleErrors.length).toBeGreaterThan(0)
  for (const message of errors.consoleErrors) expect(message).toContain('503')
}

async function openWorkbench(page: Page, sessionId = 7): Promise<void> {
  await page.addInitScript(([key, value]) => localStorage.setItem(key, value), [STORAGE_KEY, String(sessionId)])
  await page.goto('/workbench')
  await expect(page.getByRole('heading', { name: '工作台' })).toBeVisible()
  await expect(page.getByTestId('progress-action-rail')).toBeVisible()
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
    const allowedGraphPost = request.method === 'POST' && (
      request.pathname === '/api/graph/rows' || request.pathname === '/api/graph/evidence'
    )
    expect(request.method === 'GET' || allowedGraphPost, `${request.method} ${request.pathname}`).toBe(true)
    expect(request.pathname).not.toMatch(/(?:submit|confirm|cancel|score|grading\/jobs)/)
  }
}

async function captureVisualEvidence(page: Page, testInfo: TestInfo, width: number): Promise<void> {
  if (![1024, 1366, 1920].includes(width)) return
  await page.screenshot({
    path: testInfo.outputPath(`workbench-${width}.png`),
    fullPage: true,
    animations: 'disabled',
  })
}

for (const viewport of viewports) {
  test(`${viewport.width}x${viewport.height} keeps the workbench readable and actionable`, async ({ page }, testInfo) => {
    const errors = trackBrowserErrors(page)
    const { requests } = await installSyntheticApi(page)
    await page.setViewportSize(viewport)
    await openWorkbench(page)

    await expect(page.getByText('12 / 36 份', { exact: true })).toBeVisible()
    await expect(page.getByText('33.33% 已批改', { exact: true })).toBeVisible()
    await expect(page.getByText('2 道题需要核对', { exact: true })).toBeVisible()
    await expect(page.getByText('当前显示 2 / 2 道题目；选择题目可查看学生得分与扣分证据。')).toBeVisible()
    await expect(page.getByText('本题基于 12 份已批改作答')).toBeVisible()
    await expect(page.getByRole('button', { name: /^Q1/ })).toContainText('82.5%')
    await expect(page.getByRole('button', { name: /^Q2/ })).toContainText('无法计算')
    await expect(page.getByRole('cell', { name: /匿名学生甲/ })).toBeVisible()

    const refresh = page.getByRole('button', { name: '刷新工作台' })
    await refresh.focus()
    await page.keyboard.press('Tab')
    await expect(page.getByRole('button', { name: '查看批改' })).toBeFocused()

    const nodes = page.getByTestId('progress-action-rail').locator('li')
    await expect(nodes).toHaveCount(3)
    await expect(page.getByText('最近任务', { exact: true })).toHaveCount(0)
    const first = await nodes.nth(0).boundingBox()
    const second = await nodes.nth(1).boundingBox()
    expect(first).not.toBeNull()
    expect(second).not.toBeNull()
    expect(Math.abs(second!.y - first!.y) < 2).toBe(viewport.width !== 1024)

    await expectNoHorizontalOverflow(page)
    await captureVisualEvidence(page, testInfo, viewport.width)
    expectReadOnlyRequests(requests)
    expect(errors.pageErrors).toEqual([])
    expect(errors.consoleErrors).toEqual([])
  })
}

test('keeps overview actions available when analysis fails independently', async ({ page }) => {
  const errors = trackBrowserErrors(page)
  const options: MockOptions = { analysisFailure: true }
  const { requests } = await installSyntheticApi(page, options)
  await openWorkbench(page)

  await expect(page.getByText('12 / 36 份', { exact: true })).toBeVisible()
  await expect(page.getByRole('alert')).toContainText('分析数据暂时无法读取；工作台其他内容仍可使用')
  await expect(page.getByRole('button', { name: '查看异常' })).toBeEnabled()

  options.analysisFailure = false
  await page.getByRole('button', { name: '重新加载分析' }).click()
  await expect(page.getByText('本题基于 12 份已批改作答')).toBeVisible()
  expectReadOnlyRequests(requests)
  expectOnlyExpected503(errors)
})

test('retains the last successful overview after a refresh failure', async ({ page }) => {
  const errors = trackBrowserErrors(page)
  const { requests } = await installSyntheticApi(page, { overviewFailureAfterSuccess: true })
  await openWorkbench(page)
  await expect(page.getByText('12 / 36 份', { exact: true })).toBeVisible()

  await page.getByRole('button', { name: '刷新工作台' }).click()
  await expect(page.getByRole('alert')).toContainText('数据可能不是最新')
  await expect(page.getByText('12 / 36 份', { exact: true })).toBeVisible()
  expectReadOnlyRequests(requests)
  expectOnlyExpected503(errors)
})

test('isolates delayed session 7 responses after quickly switching to session 8', async ({ page }) => {
  const errors = trackBrowserErrors(page)
  const { requests, delayedSession7Settled } = await installSyntheticApi(page, { delaySession7: true })
  await page.addInitScript(([key]) => localStorage.setItem(key, '7'), [STORAGE_KEY])
  await page.goto('/workbench')
  const sessionSelector = page.getByRole('combobox', { name: '当前考试' })
  await expect(sessionSelector).toHaveValue('7')
  await expect.poll(() => requests.filter((request) => (
    request.pathname === '/api/workbench/overview' ||
    request.pathname === '/api/sessions/7/analysis/questions'
  )).length).toBe(2)

  await sessionSelector.selectOption('8')
  await expect(page.locator('.workbench-hero__session')).toHaveText('八年级物理单元检测（匿名合成数据）')
  await expect(page.getByText('40 / 40 份', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: /^Q1/ })).toContainText('91%')
  await delayedSession7Settled
  await expect(page.getByText('40 / 40 份', { exact: true })).toBeVisible()
  await expect(page.getByText('12 / 36 份', { exact: true })).toHaveCount(0)
  expectReadOnlyRequests(requests)
  expect(errors.pageErrors).toEqual([])
  expect(errors.consoleErrors).toEqual([])
})

test('loads Graph only after class selection and supports question, student, anomaly and evidence drill-downs', async ({ page }) => {
  const errors = trackBrowserErrors(page)
  const { requests } = await installSyntheticApi(page)
  await openWorkbench(page)

  expect(requests.filter((request) => request.pathname === '/api/graph/rows')).toEqual([])
  await page.getByRole('combobox', { name: '班级' }).selectOption('七年级一班')
  await expect(page.getByText('已覆盖 18 / 20 份')).toBeVisible()
  expect(requests.filter((request) => request.pathname === '/api/graph/rows')).toHaveLength(1)
  await expect(page.getByTestId('open-knowledge-graph')).toHaveAttribute(
    'href',
    `/knowledge-graph?session=7&class=${encodeURIComponent('七年级一班')}`,
  )

  await page.getByRole('button', { name: /^Q2/ }).click()
  await expect(page.getByRole('cell', { name: /匿名学生乙/ })).toBeVisible()
  await expect(page.getByText('0 / 暂不可用')).toBeVisible()
  const evidenceLink = page.getByRole('link', { name: '查看答卷证据' })
  await expect(evidenceLink).toHaveAttribute('href', '/api/sessions/7/results/72/details/702/crop')

  await page.getByRole('button', { name: /分数运算/ }).click()
  await expect(page.getByRole('list', { name: '标签证据' })).toContainText('匿名学生甲 · Q1')
  expect(requests.filter((request) => request.pathname === '/api/graph/evidence')).toHaveLength(1)

  await page.getByRole('button', { name: '查看异常' }).click()
  await expect(page.locator('section[aria-labelledby="anomaly-list-title"]')).toBeFocused()
  await expect(page.getByText('匿名答卷 10')).toBeVisible()

  expectReadOnlyRequests(requests)
  expect(errors.pageErrors).toEqual([])
  expect(errors.consoleErrors).toEqual([])
})

test('shows partial totals and reaches later question, student, anomaly and evidence pages', async ({ page }) => {
  const errors = trackBrowserErrors(page)
  const { requests } = await installSyntheticApi(page, { paginated: true })
  await openWorkbench(page)

  await expect(page.getByText('当前显示 100 / 102 道题目')).toBeVisible()
  await page.getByRole('button', { name: '加载更多题目' }).click()
  await expect(page.getByRole('button', { name: /^Q101/ })).toBeVisible()
  await expect(page.getByRole('button', { name: /^Q102/ })).toBeVisible()
  await expect(page.getByText('当前显示 102 / 102 道题目')).toBeVisible()
  await expect(page.getByRole('button', { name: '加载更多题目' })).toHaveCount(0)

  await expect(page.getByText('当前显示 100 / 101 名学生')).toBeVisible()
  await page.getByRole('button', { name: '加载更多学生' }).click()
  await expect(page.getByText('后续学生 101')).toBeVisible()
  await expect(page.getByText('当前显示 101 / 101 名学生')).toBeVisible()
  await expect(page.getByRole('button', { name: '加载更多学生' })).toHaveCount(0)

  await page.getByRole('button', { name: '查看异常' }).click()
  await expect(page.getByText('当前显示 100 / 101 条异常')).toBeVisible()
  await page.getByRole('button', { name: '加载更多异常' }).click()
  await expect(page.getByText('后续异常 101')).toBeVisible()
  await expect(page.getByText('当前显示 101 / 101 条异常')).toBeVisible()
  await expect(page.getByRole('button', { name: '加载更多异常' })).toHaveCount(0)

  await page.getByRole('combobox', { name: '班级' }).selectOption('七年级一班')
  await page.getByRole('button', { name: /分数运算/ }).click()
  await expect(page.getByText('当前显示 20 / 21 条证据')).toBeVisible()
  await page.getByRole('button', { name: '加载更多证据' }).click()
  await expect(page.getByText('后续证据学生')).toBeVisible()
  await expect(page.getByText('当前显示 21 / 21 条证据')).toBeVisible()
  await expect(page.getByRole('button', { name: '加载更多证据' })).toHaveCount(0)

  expect(requests.filter((request) => request.pathname.includes('/analysis/questions')).length).toBeGreaterThan(2)
  expect(errors.pageErrors).toEqual([])
  expect(errors.consoleErrors).toEqual([])
})
