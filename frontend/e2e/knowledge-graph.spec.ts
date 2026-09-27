import { expect, test, type Page, type Request, type Route, type TestInfo } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { arch, cpus, platform, release, totalmem } from 'node:os';

const STORAGE_KEY = 'ai-grading:selected-session:v1'
const sessions = [
  { id: 7, name: '匿名七年级数学考试', status: 'completed', is_deleted: false, deleted_at: null, created_at: null, updated_at: null },
  { id: 8, name: '匿名八年级数学考试', status: 'completed', is_deleted: false, deleted_at: null, created_at: null, updated_at: null },
]
const students = [
  { id: 12, student_code: 'S012', name: '匿名学生甲', class_name: '七年级一班', created_at: null },
  { id: 15, student_code: 'S015', name: '匿名学生乙（长名称用于布局验证）', class_name: '七年级一班', created_at: null },
  { id: 21, student_code: 'S021', name: '匿名学生丙', class_name: '七年级二班', created_at: null },
]

interface RequestLog { method: string; pathname: string }
interface GraphApiState { query: 'ready' | 'empty' | 'error' }

const PERFORMANCE_LIMITS_MS = {
  firstRender: 15_000,
  modeSwitch: 5_000,
  zoom: 5_000,
  drag: 5_000,
  selection: 5_000,
} as const

function trackBrowserErrors(page: Page) {
  const pageErrors: Error[] = []
  const consoleErrors: string[] = []
  page.on('pageerror', (error) => pageErrors.push(error))
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  return { pageErrors, consoleErrors }
}

function graphContext(body: {
  scope: { mode: 'class' | 'student' | 'selected'; class_id?: string; student_ids?: string[] }
  exam_scope: { mode: 'current' | 'manual' | 'cross_exam'; session_ids?: number[] }
}) {
  const sessionIds = body.exam_scope.mode === 'cross_exam' ? [7, 8] : body.exam_scope.session_ids ?? [7]
  const studentIds = body.scope.mode === 'class' ? ['12', '15'] : body.scope.student_ids ?? []
  return {
    scope: {
      mode: body.scope.mode,
      student_ids: studentIds,
      class_id: body.scope.mode === 'class' ? body.scope.class_id ?? '七年级一班' : null,
    },
    exam_scope: {
      mode: body.exam_scope.mode,
      session_ids: sessionIds,
      sessions: sessionIds.map((id) => ({
        session_id: id,
        session_name: sessions.find((session) => session.id === id)?.name ?? `匿名考试${id}`,
      })),
    },
  }
}

function graphResponse(request: Request, nodeCount: number) {
  const body = request.postDataJSON() as Parameters<typeof graphContext>[0]
  const context = graphContext(body)
  const nodes = Array.from({ length: nodeCount }, (_, index) => {
    const mastery = [0.48, 0.66, 0.81, 0.94][index % 4]!
    return {
      stable_key: `kp_anonymous_${index}`,
      display_name: `匿名知识点 ${index}${index % 17 === 0 ? '（较长名称用于验证标签布局）' : ''}`,
      definition: `匿名知识点 ${index} 的定义`,
      include_scope: '当前课程范围',
      exclude_scope: '相邻课程范围',
      curriculum_anchors: ['匿名课程标准'],
      observable_evidence: '能够在作答中展示对应步骤',
      rationale: '匿名课程依据',
      evidence_source_ids: ['anonymous-standard'],
      mastery: { status: 'available', value: mastery, evidence_count: index + 1, parameter_version: 'd'.repeat(64), reason: null },
      evidence: {
        student_count: 2,
        item_count: index + 1,
        deduction_count: index % 9,
        tag_context: index === 0 ? { prerequisite: ['题目支持标签甲'] } : {},
        error_counts: { primary: index === 0 ? { 步骤不完整: 2 } : {}, secondary: {} },
      },
      missing_reasons: [],
    }
  })
  return {
    response_schema_version: 'knowledge-graph-current',
    response_version: 'a'.repeat(64),
    ...context,
    current_standard: { release_id: 'current', content_hash: 'c'.repeat(64), taxonomy_revision: 1 },
    nodes,
    edges: [],
    coverage: { covered_items: nodeCount, total_items: nodeCount + 2, missing_items: { QX: '未标注' } },
    warnings: ['匿名数据中有 2 份作答未关联知识标签'],
    missing: [],
    counts: { node_count: nodeCount, edge_count: 0, evidence_row_count: nodeCount, missing_count: 0 },
  }
}

function graphEvidence(request: Request) {
  const body = request.postDataJSON() as Parameters<typeof graphContext>[0] & {
    stable_key: string
    page: number
  }
  const context = graphContext(body)
  const start = body.page === 1 ? 1 : 21
  const count = body.page === 1 ? 20 : 1
  const label = body.stable_key.replace('kp_anonymous_', '匿名知识点 ')
  return {
    response_schema_version: 'knowledge-graph-evidence-current',
    response_version: 'b'.repeat(64),
    ...context,
    current_standard: { release_id: 'current', content_hash: 'c'.repeat(64), taxonomy_revision: 1 },
    stable_key: body.stable_key,
    display_name: label,
    items: Array.from({ length: count }, (_, index) => {
      const itemOrdinal = start + index
      const rawStudentId = context.scope.student_ids[index % context.scope.student_ids.length]!
      const studentId = Number(rawStudentId)
      const student = students.find((candidate) => candidate.id === studentId)
      return {
        student_id: studentId,
        student_code: student?.student_code ?? `S${String(studentId).padStart(3, '0')}`,
        student_name: itemOrdinal === 21 ? '后续页匿名学生' : student?.name ?? `证据学生 ${studentId}`,
        class_id: context.scope.class_id ?? '七年级一班',
        knowledge_key: body.stable_key,
        stable_key: body.stable_key,
        knowledge_label: label,
        session_id: context.exam_scope.session_ids[0]!,
        session_name: context.exam_scope.sessions[0]!.session_name,
        question_id: `Q${itemOrdinal}`,
        bank_question_id: 1000 + itemOrdinal,
        score_awarded: 3,
        full_score: 5,
        score_rate: 0.6,
        tag_context: {},
        actionable_reasons: itemOrdinal === 1 ? ['步骤不完整'] : [],
        error_counts: { primary: {}, secondary: {} },
      }
    }),
    total: 21,
    page: body.page,
    page_size: 20,
    total_pages: 2,
    coverage: { covered_items: 20, total_items: 22, missing_items: { QX: '未标注' } },
  }
}

async function fulfillJson(route: Route, body: unknown): Promise<void> {
  await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
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

async function installGraphApi(
  page: Page,
  nodeCount: number,
  state: GraphApiState = { query: 'ready' },
): Promise<RequestLog[]> {
  const requests: RequestLog[] = []
  page.on('request', (request) => {
    const url = new URL(request.url())
    if (url.pathname.startsWith('/api/')) requests.push({ method: request.method(), pathname: url.pathname })
  })
  await page.route(/^https?:\/\/[^/]+\/api\//, async (route) => {
    const request = route.request()
    const pathname = new URL(request.url()).pathname
    if (pathname === '/api/sessions') return fulfillJson(route, { items: sessions, total: sessions.length })
    if (pathname === '/api/students') return fulfillJson(route, { items: students, total: students.length })
    const activeSourceMatch = pathname.match(/^\/api\/sessions\/(\d+)\/config\/sources\/active$/)
    if (request.method() === 'GET' && activeSourceMatch) {
      return fulfillJson(route, configSource(Number(activeSourceMatch[1])))
    }
    const editorMatch = pathname.match(/^\/api\/sessions\/(\d+)\/config\/editor$/)
    if (request.method() === 'GET' && editorMatch) {
      return fulfillJson(route, configEditor(Number(editorMatch[1])))
    }
    if (pathname === '/api/graph/query') {
      if (state.query === 'error') {
        return fulfillJson(route, { invalid: true })
      }
      return fulfillJson(route, graphResponse(request, state.query === 'empty' ? 0 : nodeCount))
    }
    if (pathname === '/api/graph/evidence') return fulfillJson(route, graphEvidence(request))
    await route.fulfill({ status: 418, body: `unexpected anonymous request: ${request.method()} ${pathname}` })
  })
  return requests
}

async function openGraph(page: Page, nodeCount = 120): Promise<RequestLog[]> {
  const requests = await installGraphApi(page, nodeCount)
  await page.addInitScript(([key]) => localStorage.setItem(key, '7'), [STORAGE_KEY])
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto('/knowledge-graph?session=7&class=七年级一班')
  await expect(page.getByRole('heading', { name: '知识图谱', exact: true })).toBeVisible()
  await expect(page.getByText(`已覆盖 ${nodeCount} / ${nodeCount + 2} 份作答`)).toBeVisible()
  await expect(page.locator('.knowledge-graph-canvas canvas').first()).toBeVisible()
  return requests
}

async function nonBackgroundPixelCount(page: Page): Promise<number> {
  return page.locator('.knowledge-graph-canvas canvas').first().evaluate((canvas: HTMLCanvasElement) => {
    const context = canvas.getContext('2d')
    if (!context) return 0
    const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data
    let count = 0
    for (let index = 0; index < pixels.length; index += 16) {
      if (pixels[index + 3]! > 0 && (pixels[index]! < 248 || pixels[index + 1]! < 248 || pixels[index + 2]! < 248)) count += 1
    }
    return count
  })
}

async function dragCanvas(page: Page): Promise<void> {
  const canvas = page.locator('.knowledge-graph-canvas canvas').first()
  const box = await canvas.boundingBox()
  if (!box) throw new Error('Knowledge graph canvas is not visible')
  const startX = box.x + box.width * 0.55
  const startY = box.y + box.height * 0.55
  await page.mouse.move(startX, startY)
  await page.mouse.down()
  await page.mouse.move(startX + Math.min(120, box.width * 0.18), startY + 40, { steps: 6 })
  await page.mouse.up()
}

async function clickCanvasNode(page: Page, requests: RequestLog[]): Promise<void> {
  const canvas = page.locator('.knowledge-graph-canvas canvas').first()
  const box = await canvas.boundingBox()
  if (!box) throw new Error('Knowledge graph canvas is not visible')
  const before = requests.filter((request) => request.pathname === '/api/graph/evidence').length
  for (let y = 8; y < box.height - 8; y += 16) {
    for (let x = 8; x < box.width - 8; x += 16) {
      await page.mouse.click(box.x + x, box.y + y)
      const after = requests.filter((request) => request.pathname === '/api/graph/evidence').length
      if (after > before) return
    }
  }
  throw new Error('No selectable knowledge node was found on the canvas')
}

function expectReadOnly(requests: RequestLog[]): void {
  expect(requests.length).toBeGreaterThan(0)
  for (const request of requests) {
    const allowedPost = request.method === 'POST' && ['/api/graph/query', '/api/graph/evidence'].includes(request.pathname)
    expect(request.method === 'GET' || allowedPost).toBe(true)
  }
}

test('supports zoom, grouping explanation, keyboard selection and paged evidence', async ({ page }) => {
  const errors = trackBrowserErrors(page)
  const requests = await openGraph(page)
  const canvas = page.locator('.knowledge-graph-canvas canvas').first()
  await canvas.scrollIntoViewIfNeeded()
  const beforeZoom = await canvas.screenshot()
  await canvas.hover()
  await page.mouse.wheel(0, -500)
  await expect.poll(async () => (await canvas.screenshot()).equals(beforeZoom)).toBe(false)
  await page.getByRole('button', { name: '恢复视图' }).click()
  const beforeDrag = await canvas.screenshot()
  await dragCanvas(page)
  await expect.poll(async () => (await canvas.screenshot()).equals(beforeDrag)).toBe(false)
  await page.getByRole('button', { name: '恢复视图' }).click()

  await clickCanvasNode(page, requests)
  await expect(page.locator('.knowledge-graph-inspector__facts')).toBeVisible()

  await page.getByRole('button', { name: '学生分组树' }).click()
  await expect(page.getByText(/虚线仅表示筛选范围、学生与知识标签的分组归属/)).toBeVisible()
  await expect(page.getByText(/不是知识点父子、先修或相关关系/)).toBeVisible()
  await page.getByRole('button', { name: '掌握度分区' }).click()

  const search = page.getByRole('searchbox', { name: '搜索知识标签' })
  await search.fill('匿名知识点')
  const directoryItems = page.getByTestId('graph-directory-item')
  await directoryItems.first().focus()
  await page.keyboard.press('ArrowDown')
  await page.keyboard.press('Enter')
  await expect(directoryItems.nth(1)).toHaveAttribute('aria-current', 'true')
  await page.getByRole('button', { name: '加载更多证据' }).click()
  await expect(page.getByText('后续页匿名学生')).toBeVisible()
  expect(requests.filter((request) => request.pathname === '/api/graph/evidence').length)
    .toBeGreaterThanOrEqual(3)
  expectReadOnly(requests)
  expect(errors.pageErrors).toEqual([])
  expect(errors.consoleErrors).toEqual([])
})

test('keeps no-tag and partial-failure states distinct and recoverable', async ({ page }) => {
  const errors = trackBrowserErrors(page)
  const state: GraphApiState = { query: 'empty' }
  const requests = await installGraphApi(page, 120, state)
  await page.addInitScript(([key]) => localStorage.setItem(key, '7'), [STORAGE_KEY])
  await page.goto('/knowledge-graph?session=7&class=七年级一班')

  await expect(page.getByText('当前范围没有可显示的知识标签')).toBeVisible()
  state.query = 'ready'
  await page.getByRole('button', { name: '应用范围' }).click()
  await expect(page.locator('.knowledge-graph-canvas canvas').first()).toBeVisible()

  state.query = 'error'
  await page.getByRole('button', { name: '应用范围' }).click()
  await expect(page.getByText(/当前显示上次成功读取的知识图谱/)).toBeVisible()
  await expect(page.locator('.knowledge-graph-canvas canvas').first()).toBeVisible()
  expectReadOnly(requests)
  expect(errors.pageErrors).toEqual([])
  expect(errors.consoleErrors).toEqual([])
})

test('renders and operates on a reproducible 1000-node anonymous graph', async ({ page }, testInfo: TestInfo) => {
  const errors = trackBrowserErrors(page)
  const startedAt = performance.now()
  const requests = await openGraph(page, 1000)
  const firstRenderMs = performance.now() - startedAt
  expect(firstRenderMs).toBeLessThan(PERFORMANCE_LIMITS_MS.firstRender)
  expect(await nonBackgroundPixelCount(page)).toBeGreaterThan(100)

  const modeStartedAt = performance.now()
  await page.getByRole('button', { name: '学生分组树' }).click()
  await expect(page.getByText(/不是知识点父子、先修或相关关系/)).toBeVisible()
  const modeSwitchMs = performance.now() - modeStartedAt
  expect(modeSwitchMs).toBeLessThan(PERFORMANCE_LIMITS_MS.modeSwitch)
  await page.getByRole('button', { name: '掌握度分区' }).click()

  const canvas = page.locator('.knowledge-graph-canvas canvas').first()
  const beforeZoom = await canvas.screenshot()
  const zoomStartedAt = performance.now()
  await canvas.hover()
  await page.mouse.wheel(0, -500)
  await expect.poll(async () => (await canvas.screenshot()).equals(beforeZoom)).toBe(false)
  const zoomMs = performance.now() - zoomStartedAt
  expect(zoomMs).toBeLessThan(PERFORMANCE_LIMITS_MS.zoom)

  const beforeDrag = await canvas.screenshot()
  const dragStartedAt = performance.now()
  await dragCanvas(page)
  await expect.poll(async () => (await canvas.screenshot()).equals(beforeDrag)).toBe(false)
  const dragMs = performance.now() - dragStartedAt
  expect(dragMs).toBeLessThan(PERFORMANCE_LIMITS_MS.drag)
  await page.getByRole('button', { name: '恢复视图' }).click()

  const selectionStartedAt = performance.now()
  await page.getByRole('searchbox', { name: '搜索知识标签' }).fill('匿名知识点 999')
  await expect(page.getByTestId('graph-directory-item')).toHaveCount(1)
  await page.getByTestId('graph-directory-item').click()
  await expect(page.getByRole('heading', { name: /匿名知识点 999/ })).toBeVisible()
  const selectionMs = performance.now() - selectionStartedAt
  expect(selectionMs).toBeLessThan(PERFORMANCE_LIMITS_MS.selection)

  const actualCandidateSha = execFileSync(
    'git', ['rev-parse', 'HEAD'], { encoding: 'utf8' },
  ).trim()
  expect(process.env.P2_15_CANDIDATE_SHA ?? actualCandidateSha).toBe(actualCandidateSha)
  await testInfo.attach('knowledge-graph-1000-node-baseline.json', {
    body: Buffer.from(JSON.stringify({
      candidateSha: actualCandidateSha,
      nodeCount: 1000,
      firstRenderMs,
      modeSwitchMs,
      zoomMs,
      dragMs,
      selectionMs,
      thresholdsMs: PERFORMANCE_LIMITS_MS,
      browser: testInfo.project.name,
      viewport: page.viewportSize(),
      testMachine: {
        platform: platform(),
        release: release(),
        architecture: arch(),
        cpuModel: cpus()[0]?.model ?? 'unknown',
        cpuCount: cpus().length,
        totalMemoryMb: Math.round(totalmem() / 1024 / 1024),
      },
    }, null, 2)),
    contentType: 'application/json',
  })
  expectReadOnly(requests)
  expect(errors.pageErrors).toEqual([])
  expect(errors.consoleErrors).toEqual([])
})
