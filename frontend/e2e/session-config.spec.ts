import { expect, type Page, type Route } from '@playwright/test';
import { test } from './mock-fixtures'
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const sourceId = 'a'.repeat(32)
const sourceRevision = 'b'.repeat(64)
const initialRevision = 'c'.repeat(64)
const savedRevision = 'd'.repeat(64)

const session = {
  id: 7, name: '七年级数学期末', status: 'created', curriculum_volume_id: null, is_deleted: false,
  deleted_at: null, created_at: '2026-07-15T08:00:00Z', updated_at: '2026-07-15T08:00:00Z',
}

const questions = [
  {
    question_id: 'Q1', question_type: 'calculation',
    question_preview: '计算 2x + 3 = 11，并写出完整推导过程。'.repeat(12),
    answer_preview: 'x = 4', answer_present: true, needs_review: false,
    local_answer_trusted: true, has_question_asset: false, has_answer_asset: false,
  },
  {
    question_id: 'Q2', question_type: 'comprehensive',
    question_preview: '在平行四边形中证明两组对边分别相等。',
    answer_preview: '连接对角线后证明全等', answer_present: true, needs_review: true,
    question_type_review_required: true,
    local_answer_trusted: false, has_question_asset: false, has_answer_asset: false,
  },
  {
    question_id: 'Q3', question_type: 'choice', question_preview: '选择正确结论。',
    answer_preview: 'B', answer_present: true, needs_review: false,
    local_answer_trusted: true, has_question_asset: false, has_answer_asset: false,
  },
]

const source = {
  session_id: 7, source_id: sourceId, source_revision: sourceRevision,
  safe_filename: 'synthetic-math.docx', suffix: '.docx', size_bytes: 8192,
  sha256_prefix: 'e'.repeat(12), parse_state: 'ready', questions,
}

function editor(configured = true, revision = initialRevision, answer = 'x = 4') {
  const rows = configured ? [
    {
      row_id: 'row-q1-p1-s1', question_id: 'Q1', part_id: 'P1', step_id: 'S1',
      part_label: '第 1 问', question_type: 'calculation', core_goal: '列式并求解', score: 10,
      standard_answer: answer, accepted_answers: ['4'], match_rule: '按步骤',
      answer_only_max_score: 4, require_final_answer: true, required_elements: ['列式', '结果'],
      deduction_rules: ['漏写过程扣 2 分'], part_deduction_rules: ['计算错误时按步骤扣分'],
      final_answer_rule: '结果正确',
    },
    {
      row_id: 'row-q2-p1-s1', question_id: 'Q2', part_id: 'P1', step_id: 'S1',
      part_label: '证明准备', question_type: 'proof', core_goal: '构造辅助线', score: 15,
      standard_answer: '连接 AC', accepted_answers: [], match_rule: '按要素',
      answer_only_max_score: null, require_final_answer: true, required_elements: ['辅助线'],
      deduction_rules: [], part_deduction_rules: [], final_answer_rule: '',
    },
    {
      row_id: 'row-q2-p1-s2', question_id: 'Q2', part_id: 'P1', step_id: 'S2',
      part_label: '证明准备', question_type: 'proof', core_goal: '证明全等并得出结论', score: 25,
      standard_answer: '由 ASA 证明全等', accepted_answers: [], match_rule: '按要素',
      answer_only_max_score: null, require_final_answer: true, required_elements: ['全等', '结论'],
      deduction_rules: [], part_deduction_rules: [], final_answer_rule: '',
    },
    {
      row_id: 'row-q3-p1-s1', question_id: 'Q3', part_id: 'P1', step_id: 'S1',
      part_label: '选择答案', question_type: 'choice', core_goal: '判断结论', score: 40,
      standard_answer: 'B', accepted_answers: [], match_rule: '精确匹配',
      answer_only_max_score: null, require_final_answer: true, required_elements: [],
      deduction_rules: [], part_deduction_rules: [], final_answer_rule: '',
    },
  ] : []
  return {
    session_id: 7, configured, revision, rows,
    total_score: rows.reduce((total, row) => total + row.score, 0), issues: [],
    source: configured ? { safe_filename: source.safe_filename, suffix: source.suffix, sha256_prefix: source.sha256_prefix } : null,
  }
}

type Mode = 'partial' | 'complete' | 'whole-fail' | 'running'
interface MockOptions {
  initialSessions?: boolean
  firstGeneration?: Mode
  uploadFailures?: number
  saveFailure?: '422' | '409' | null
  mappingStatus?: 'not_present' | 'refreshed' | 'reconfirm_required'
  cancelRace?: boolean
  holdConflictReload?: boolean
}

interface MockState {
  hasSession: boolean
  hasSource: boolean
  editorReady: boolean
  jobs: Map<number, ReturnType<typeof job>>
  generationRequests: number
  uploadFailures: number
  editorGets: number
  saveFailure: MockOptions['saveFailure']
  mappingStatus: NonNullable<MockOptions['mappingStatus']>
  authoritativeAnswer: string
  pendingEditorRoute: Route | null
  lastSave: { edits?: Array<Record<string, unknown>>; commands?: unknown[] } | null
}

function job(id: number, mode: Mode) {
  const complete = mode === 'complete'
  const partial = mode === 'partial'
  const failed = mode === 'whole-fail'
  return {
    id, job_type: 'config_generation',
    payload: { session_id: 7, source_id: sourceId, source_revision: sourceRevision,
      generation_mode: 'batched' },
    result: partial
      ? { outcome: 'partial', total_questions: 3, generated_questions: 2,
          failed_count: 1, failed_question_ids: ['Q3'],
          failed_batches: [{ batch_id: 'B001', question_ids: ['Q3'] }], retryable: true }
      : complete
        ? { outcome: 'complete', total_questions: 3, generated_questions: 3,
            failed_count: 0, failed_question_ids: [], retryable: false }
        : {},
    status: partial || complete ? 'succeeded' : failed ? 'failed' : 'running',
    progress: partial || complete || failed ? 1 : 0.35,
    stage: failed ? 'failed' : complete ? 'complete' : partial ? 'partial' : 'generating',
    detail: failed ? '批次生成未完成' : '',
    error: failed
      ? '模型因输出长度上限停止，返回结果不完整（响应摘要: synthetic-private-hash）。'
      : null,
    cancel_requested: false, created_at: '2026-07-15T08:01:00Z',
    started_at: '2026-07-15T08:01:01Z', updated_at: `2026-07-15T08:01:${String(id).padStart(2, '0')}Z`,
    finished_at: partial || complete || failed ? '2026-07-15T08:02:00Z' : null,
  }
}

function errorBody(route: Route, code: string, message: string, details: Record<string, unknown> = {}) {
  const requestId = route.request().headers()['x-request-id'] ?? 'synthetic-request'
  return { headers: { 'x-request-id': requestId }, json: { error: { code, message, details, request_id: requestId } } }
}

async function installConfigWorkspaceMockApi(page: Page, options: MockOptions = {}): Promise<MockState> {
  const state: MockState = {
    hasSession: options.initialSessions ?? false, hasSource: false, editorReady: false,
    jobs: new Map(), generationRequests: 0, uploadFailures: options.uploadFailures ?? 0,
    editorGets: 0, saveFailure: options.saveFailure ?? null,
    mappingStatus: options.mappingStatus ?? 'refreshed', authoritativeAnswer: 'x = 4',
    pendingEditorRoute: null,
    lastSave: null,
  }

  await page.route(/^https?:\/\/[^/]+\/api\//, async (route) => {
    const request = route.request()
    const method = request.method()
    const path = new URL(request.url()).pathname

    if (path === '/api/sessions' && method === 'GET') {
      const payload = { items: state.hasSession ? [session] : [], total: state.hasSession ? 1 : 0 }
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(payload) })
    } else if (path === '/api/sessions/drafts' && method === 'POST') {
      state.hasSession = true
      await route.fulfill({ json: session })
    } else if (path === '/api/sessions/7' && method === 'PATCH') {
      await route.fulfill({ json: session })
    } else if (path === '/api/sessions/7/regions/readiness' && method === 'GET') {
      await route.fulfill({ json: { session_id: 7, scoring_configured: state.editorReady,
        template_present: false, template_ready: false } })
    } else if (path === '/api/sessions/7/config/sources' && method === 'POST') {
      if (state.uploadFailures > 0) {
        state.uploadFailures -= 1
        await route.fulfill({ status: 422, ...errorBody(route, 'invalid_config_source', '合成解析失败') })
      } else {
        state.hasSource = true
        await route.fulfill({ json: source })
      }
    } else if (path === `/api/sessions/7/config/sources/${sourceId}` && method === 'GET') {
      await route.fulfill({ json: source })
    } else if (path === '/api/sessions/7/config/sources/active' && method === 'GET') {
      if (state.hasSource) await route.fulfill({ json: source })
      else await route.fulfill({ status: 404, ...errorBody(route, 'config_source_not_found', 'TEST-no-source') })
    } else if (path === '/api/sessions/7/config/sources/active/duplicates' && method === 'GET') {
      await route.fulfill({ json: { source_id: sourceId, source_revision: sourceRevision, items: [] } })
    } else if (path === '/api/sessions/7/config/generation-jobs/latest' && method === 'GET') {
      const latest = [...state.jobs.values()].at(-1)
      if (latest) await route.fulfill({ json: latest })
      else await route.fulfill({ status: 404, ...errorBody(route, 'job_not_found', 'TEST-no-job') })
    } else if (/^\/api\/sessions\/7\/config\/generation-jobs\/\d+\/question-states$/.test(path) && method === 'GET') {
      const current = state.jobs.get(Number(path.split('/').at(-2)))
      const complete = current?.result.outcome === 'complete'
      await route.fulfill({ json: {
        job_id: current?.id,
        questions: ['Q1', 'Q2', 'Q3'].map((question_id) => ({
          question_id, state: question_id === 'Q3' && !complete ? 'failed' : 'passed',
          reason: '', retryable: question_id === 'Q3' && !complete,
        })),
      } })
    } else if (path === '/api/sessions/7/config/generation-preview' && method === 'POST') {
      await route.fulfill({ json: {
        base_release_id: 'kgr_TEST-preview', input_fingerprint: 'f'.repeat(64), volume_id: 'volume-1',
        chapters: [], planned_requests: 0, model_calls: 0, analysis_request_estimate: 1,
        source_id: sourceId, source_revision: sourceRevision,
      } })
    } else if (path === '/api/sessions/7/config/generate-from-source' && method === 'POST') {
      state.generationRequests += 1
      const mode = state.generationRequests === 1
        ? (options.firstGeneration ?? 'partial')
        : 'complete'
      const next = job(30 + state.generationRequests, mode)
      state.jobs.set(next.id, next)
      if (mode === 'complete') state.editorReady = true
      await route.fulfill({ json: next })
    } else if (path === '/api/sessions/7/config/generate/retry' && method === 'POST') {
      state.generationRequests += 1
      const next = job(30 + state.generationRequests, 'complete')
      state.jobs.set(next.id, next)
      state.editorReady = true
      await route.fulfill({ json: next })
    } else if (/^\/api\/jobs\/\d+$/.test(path) && method === 'GET') {
      const id = Number(path.split('/').at(-1))
      await route.fulfill({ json: state.jobs.get(id) ?? job(id, 'partial') })
    } else if (/^\/api\/jobs\/\d+\/cancel$/.test(path) && method === 'POST') {
      const id = Number(path.split('/').at(-2))
      const current = state.jobs.get(id) ?? job(id, 'running')
      current.cancel_requested = true
      state.jobs.set(id, current)
      await route.fulfill({ json: current })
      if (options.cancelRace) {
        const completed = job(id, 'complete')
        state.jobs.set(id, completed)
        state.editorReady = true
      }
    } else if (path === '/api/sessions/7/config/editor' && method === 'GET') {
      state.editorGets += 1
      if (options.holdConflictReload && state.editorGets > 1) state.pendingEditorRoute = route
      else await route.fulfill({ json: editor(state.editorReady, initialRevision, state.authoritativeAnswer) })
    } else if (path === '/api/sessions/7/config/editor' && method === 'PUT') {
      state.lastSave = request.postDataJSON() as MockState['lastSave']
      if (state.saveFailure === '422') {
        state.saveFailure = null
        await route.fulfill({ status: 422, ...errorBody(route, 'invalid_config_editor', '合成校验失败', {
          issues: [{ code: 'invalid_score', severity: 'error', row_id: 'row-q1-p1-s1',
            field: 'score', message: 'Q1 分值需要重新核对' }],
        }) })
      } else if (state.saveFailure === '409') {
        state.saveFailure = null
        state.authoritativeAnswer = '服务器较新答案'
        await route.fulfill({ status: 409, ...errorBody(route, 'config_revision_conflict', '服务器已有较新版本') })
      } else {
        const body = request.postDataJSON() as { edits?: Array<Record<string, unknown> & { row_id: string }> }
        const saved = editor(true, savedRevision, state.authoritativeAnswer)
        for (const edit of body.edits ?? []) {
          const row = saved.rows.find((candidate) => candidate.row_id === edit.row_id)
          if (row) Object.assign(row, Object.fromEntries(
            Object.entries(edit).filter(([key]) => key !== 'row_id'),
          ))
        }
        saved.total_score = saved.rows.reduce((total, row) => total + row.score, 0)
        await route.fulfill({ json: { ...saved, save_result: {
          config_saved: true, mapping_status: state.mappingStatus, mapping_message: 'synthetic mapping result',
        } } })
      }
    } else {
      await route.fallback()
    }
  })
  await page.route('**/api/sessions', async (route) => {
    const payload = { items: state.hasSession ? [session] : [], total: state.hasSession ? 1 : 0 }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(payload) })
  })
  return state
}

async function uploadSyntheticDocx(page: Page): Promise<void> {
  await page.getByLabel('选择 DOCX 或 PDF').setInputFiles({
    name: 'synthetic-math.docx', mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    buffer: Buffer.from('synthetic docx fixture'),
  })
  await page.getByRole('button', { name: '上传并拆题' }).click()
}

async function openSeededWorkspace(page: Page, state: MockState, jobId: number | null = null): Promise<void> {
  state.hasSession = true
  state.hasSource = true
  await page.addInitScript(({ sourceIdValue, revisionValue, seededJobId }) => {
    if (localStorage.getItem('ai-grading:selected-session:v1') === null) {
      localStorage.setItem('ai-grading:selected-session:v1', '7')
    }
    if (localStorage.getItem('ai-grading:config-workspace:v1') === null) {
      localStorage.setItem('ai-grading:config-workspace:v1', JSON.stringify({
        sessionId: 7, phase: seededJobId === null ? 'source' : 'generation',
        sourceId: sourceIdValue, sourceRevision: revisionValue, jobId: seededJobId, decisions: [],
      }))
    }
  }, { sourceIdValue: sourceId, revisionValue: sourceRevision, seededJobId: jobId })
  await page.goto('/sessions')
  await expect(page.getByRole('heading', { name: state.editorReady ? '本场赋分' : '拆题结果', exact: true })).toBeVisible()
}

async function makeEditorDirtyAndSavable(page: Page): Promise<void> {
  await page.getByRole('button', { name: /^Q1 P1 S1，.*点击编辑$/ }).click()
  await page.getByLabel('Q1 P1 S1 分值').fill('20')
  await page.getByLabel('Q1 P1 S1 分值').press('Tab')
  await expect(page.getByRole('button', { name: '保存评分依据' })).toBeEnabled()
  const part = page.locator('[data-part-key="Q1:P1"]')
  await part.locator('.rubric-part__reference > summary').click()
  await part.locator('.rubric-part__common-editor > summary').click()
}

test('draft to saved rubric survives partial generation and refresh', async ({ page }) => {
  const state = await installConfigWorkspaceMockApi(page, { firstGeneration: 'partial' })
  const sessionResponsePromise = page.waitForResponse((response) =>
    new URL(response.url()).pathname === '/api/sessions')
  await page.goto('/sessions')
  const sessionResponse = await sessionResponsePromise
  expect(sessionResponse.status()).toBe(200)
  expect(await sessionResponse.json()).toEqual({ items: [], total: 0 })
  // The same verified fixture can be held open for the versioned manual quick check.
  // eslint-disable-next-line playwright/no-conditional-in-test
  if (process.env.P2_09_QUICK === '1') {
    test.setTimeout(0)
    const quickFixtureDir = resolve('test-results', 'p2-09-quick')
    await mkdir(quickFixtureDir, { recursive: true })
    await writeFile(resolve(quickFixtureDir, 'synthetic-math.docx'), 'synthetic mock-only DOCX fixture')
    await page.waitForEvent('close')
    return
  }
  await page.getByLabel('考试名称').fill('七年级数学期末')
  await page.getByRole('button', { name: '创建草稿', exact: true }).click()
  await uploadSyntheticDocx(page)
  await expect(page.getByRole('heading', { name: '拆题结果', exact: true })).toBeVisible()
  await page.getByRole('combobox', { name: /^题型建议（可选修改）/ }).selectOption('proof')
  await page.getByRole('button', { name: '分析并入库', exact: true }).click()
  await page.getByRole('combobox', { name: '选择教材册别', exact: true }).selectOption('volume-1')
  await page.getByRole('button', { name: '开始分析并入库' }).click()
  await expect(page.getByRole('alertdialog').getByText('确认分析并入库？')).toBeVisible()
  expect(state.generationRequests).toBe(0)
  await page.getByRole('alertdialog').getByRole('button', { name: '开始分析', exact: true }).click()
  await expect(page.getByText('已成功 2 道题', { exact: false })).toBeVisible()

  await page.reload()
  await expect(page.getByLabel('选择失败批次 B001')).toBeVisible()
  await page.getByLabel('选择失败批次 B001').check()
  await page.getByRole('button', { name: '重试所选失败题' }).click()
  await expect(page.getByRole('heading', { name: '本场赋分', exact: true })).toBeVisible()
  const point = page.locator('[data-row-id="row-q1-p1-s1"]')
  await expect(point.getByLabel('Q1 P1 S1 证据要求/关键步骤')).toBeHidden()
  await expect(page.getByLabel('Q1 P1 S1 标准答案')).toBeHidden()
  expect(state.lastSave).toBeNull()
  await point.scrollIntoViewIfNeeded()
  await page.screenshot({ path: resolve('../output/test_open_answers_rubric_20261008/rubric-editor.png'), fullPage: true, animations: 'disabled' })
  await page.getByRole('button', { name: /^Q1 P1 S1，.*点击编辑$/ }).click()
  await expect(point.getByLabel('Q1 P1 S1 证据要求/关键步骤')).toHaveValue('列式\n结果')
  await expect(point.getByLabel('Q1 P1 S1 扣分规则')).toBeHidden()
  await point.locator('.rubric-unit-card__exceptions > summary').click()
  await expect(point.getByLabel('Q1 P1 S1 扣分规则')).toHaveValue('漏写过程扣 2 分')
  expect(state.lastSave).toBeNull()
  await page.screenshot({ path: resolve('../output/test_open_answers_rubric_20261008/rubric-editor-expanded.png'), fullPage: true, animations: 'disabled' })
  await page.getByLabel('Q1 P1 S1 分值').fill('20')
  await page.getByLabel('Q1 P1 S1 分值').press('Tab')
  await expect(page.getByRole('button', { name: '保存评分依据' })).toBeEnabled()
  await page.getByRole('button', { name: '保存评分依据' }).click()
  await expect(page.getByText('评分依据已保存，样卷映射已刷新')).toBeVisible()
  expect(state.generationRequests).toBe(2)
})

test('409 retains local work and reloads only after explicit confirmation', async ({ page }) => {
  const state = await installConfigWorkspaceMockApi(page, { initialSessions: true, saveFailure: '409' })
  state.editorReady = true
  await openSeededWorkspace(page, state)
  await expect(page.getByRole('heading', { name: '本场赋分', exact: true })).toBeVisible()
  await makeEditorDirtyAndSavable(page)
  await page.getByLabel('Q1 P1 S1 标准答案').fill('本地未保存答案')
  await page.getByLabel('Q1 P1 S1 标准答案').press('Tab')
  await page.getByRole('button', { name: '保存评分依据' }).click()
  await expect(page.getByText('服务器已有较新版本')).toBeVisible()
  await expect(page.getByLabel('Q1 P1 S1 标准答案')).toHaveValue('本地未保存答案')
  const before = state.editorGets
  await page.getByRole('button', { name: '重新加载最新版本' }).click()
  expect(state.editorGets).toBe(before)
  await page.getByRole('button', { name: '确认丢弃并重新加载' }).click()
  await expect(page.getByLabel('Q1 P1 S1 标准答案')).toHaveValue('服务器较新答案')
  expect(state.editorGets).toBe(before + 1)
})

test('stale conflict reload cannot overwrite a newer local edit', async ({ page }) => {
  const state = await installConfigWorkspaceMockApi(page, {
    initialSessions: true, saveFailure: '409', holdConflictReload: true,
  })
  state.editorReady = true
  await openSeededWorkspace(page, state)
  await expect(page.getByRole('heading', { name: '本场赋分', exact: true })).toBeVisible()
  await makeEditorDirtyAndSavable(page)
  await page.getByRole('button', { name: '保存评分依据' }).click()
  await page.getByRole('button', { name: '重新加载最新版本' }).click()
  await page.getByRole('button', { name: '确认丢弃并重新加载' }).click()
  await expect.poll(() => state.pendingEditorRoute).not.toBeNull()
  await page.getByLabel('Q1 P1 S1 标准答案').fill('请求之后的新草稿')
  await page.getByLabel('Q1 P1 S1 标准答案').press('Tab')
  await state.pendingEditorRoute!.fulfill({ json: editor(true, savedRevision, '旧响应答案') })
  await expect(page.getByLabel('Q1 P1 S1 标准答案')).toHaveValue('请求之后的新草稿')
  await expect(page.getByText('有未保存修改')).toBeVisible()
})
