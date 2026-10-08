import { expect, type Page } from '@playwright/test'
import { test } from './mock-fixtures'
import type { PersonalizedPaperInstance, PersonalizedRecommendationDraft, TrainingAssessmentOutcome, TrainingAssessmentQuestion, TrainingScanBatch } from '../src/api/training'
const volumeId = 'bnu24-math-g8-upper'
const chapter = 'kp_test_c1', section = 'kp_test_c1_s1', skill = 'sk_test_skill'
const catalog = { schema_version: 2, catalog_id: 'TEST-catalog', knowledge_standard_id: 'TEST-standard', publisher: 'TEST', subject: '数学', edition: '2024',
  statistics: { raw_nodes: 3, excluded_nodes: 0, retained_nodes: 3, chapters: 1, sections: 1, knowledge_points: 0 },
  volumes: [{ id: volumeId, order: 3, label: '合成八上', grade: '八年级', semester: '上学期', textbook_version: '北师大版2024', source: {},
    statistics: { raw_nodes: 3, excluded_nodes: 0, retained_nodes: 3 }, chapters: [{ id: 'TEST-c1', knowledge_id: chapter, order: 1, number: '1', title: '合成章', label: '第一章 合成章', kind: 'chapter', display_name: '合成册｜合成章',
      source_ref: { node_id: 'TEST', relative_url: '/TEST' }, exam_scope_values: ['TEST'], sections: [{ id: 'TEST-s1', knowledge_id: section, order: 1, number: '1', title: '合成节', label: '合成节', kind: 'lesson', display_name: '合成册｜合成章｜合成节', source_ref: { node_id: 'TEST', relative_url: '/TEST' }, knowledge_points: [] }] }] }] }
catalog.volumes = Array.from({ length: 5 }, (_, index) => ({ ...catalog.volumes[0]!, id: index === 2 ? volumeId : `TEST-volume-${index}`, order: index + 1 }))
const students = [1, 2, 3].map(id => ({ id, student_code: `TEST-${id}`, name: `合成学生${id}`, class_name: id === 3 ? '二班' : '一班', created_at: null }))
const weak = { knowledge_key: skill, knowledge_point: '技能·合成技能', parent_knowledge_key: section, tier: 'weak', mastery: .4, score_sum: 4, full_score_sum: 10, deduction_count: 1, evidence_count: 1, observation_count: 1, exam_count: 1, source_question_refs: [], actionable_reasons: [], tag_context: {}, error_counts: {} }
function diagnosis(body: { scope: { mode: string; class_ids?: string[]; student_ids?: string[] }; response_mode?: 'full' | 'display' }) {
  const selected = students.filter(student => body.scope.mode === 'class' ? body.scope.class_ids?.includes(student.class_name) : body.scope.mode === 'selected' ? body.scope.student_ids?.includes(String(student.id)) : true)
  const full = { scope: body.scope, exam_scope: { mode: 'semester', curriculum_volume_id: volumeId, session_ids: [7, 8], sessions: [{ session_id: 7, session_name: 'TEST考试7' }, { session_id: 8, session_name: 'TEST考试8' }] },
    students: selected.map(student => ({ student_id: String(student.id), student_name: student.name, student_code: student.student_code, class_id: student.class_name, score_rate: .6, score_rate_source: 'current_exam', weak_points: [weak] })),
    knowledge_catalog: [{ knowledge_key: chapter, knowledge_point: '合成章', node_kind: 'chapter' }, { knowledge_key: section, knowledge_point: '合成节', node_kind: 'section', parent_knowledge_key: chapter }, { knowledge_key: skill, knowledge_point: '合成技能', node_kind: 'skill', parent_knowledge_key: section }],
    group_weak_points: [], knowledge_associations: [], coverage: { covered_items: 3, total_items: 3, missing_items: {} }, confirmed_concept_ids: [], suggested_terms: [], unmapped_terms: [], warnings: [], diagnosis_identity: 'question_tag' }
  if (body.response_mode !== 'display') return full
  return { ...full, response_mode: 'display', students: full.students.map(student => ({ ...student,
    weak_points: student.weak_points.map(point => ({ knowledge_key: point.knowledge_key,
      knowledge_point: point.knowledge_point, mastery: point.mastery, tier: point.tier,
      observation_count: point.observation_count, evidence_count: point.evidence_count,
      parent_knowledge_key: point.parent_knowledge_key, source_reference_count: point.source_question_refs.length })),
  })) }
}
const job = { id: 71, job_type: 'wrong_question_export', payload: {}, result: { question_count: 3, filename: 'TEST错题本.zip', download_url: '/api/jobs/71/download', generated_students: [], empty_students: [], missing_items: [], failed_students: [] },
  status: 'succeeded', progress: 1, stage: 'wrong_question_export', detail: '已完成', error: null, cancel_requested: false,
  created_at: '2026-10-03T00:00:00Z', started_at: null, updated_at: '2026-10-03T00:00:00Z', finished_at: '2026-10-03T00:00:00Z' }
async function install(page: Page) {
  const calls: Array<{ path: string; body: Record<string, unknown> }> = []
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.addInitScript(id => { localStorage.clear(); localStorage.setItem('ai-grading:curriculum-scope:v1', id) }, volumeId)
  await page.route(/^https?:\/\/[^/]+\/api\//, async route => {
    const request = route.request(), path = new URL(request.url()).pathname
    const body = request.method() === 'POST' ? request.postDataJSON() : {}
    calls.push({ path, body })
    if (path === '/api/jobs/71/download') { await route.fulfill({ status: 200, contentType: 'application/zip', headers: { 'Content-Disposition': 'attachment; filename="TEST-books.zip"' }, body: 'TEST ZIP' }); return }
    let value: unknown
    if (path === '/api/question-bank/curriculum') value = catalog
    else if (path === '/api/students') value = { items: students, total: students.length }
    else if (path === '/api/sessions') value = { items: [], total: 0 }
    else if (path === '/api/training/diagnosis') value = diagnosis(body)
    else if (path === '/api/students/wrong-question-books/preview') value = {
      students: students.filter(student => body.student_ids.includes(student.id)), sessions: [7, 8].map(id => ({ session_id: id, session_name: `TEST考试${id}`, exam_created_at: null })),
      session_ids: body.session_ids ?? [7, 8], semester_label: '合成八上', question_count: 3, missing_items: [], session_wrong_counts: { 7: 2, 8: 1 }, out_of_scope_count: body.scope_keys?.length ? 1 : 0, empty_students: [],
    }
    else if (path === '/api/students/wrong-question-books' || path === '/api/jobs/71') value = job
    else if (path.startsWith('/api/jobs') || path === '/api/training/personalized-drafts') value = { items: [], total: 0 }
    else { await route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ error: { code: 'TEST-unexpected', message: 'TEST', request_id: 'TEST' } }) }); return }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(value) })
  })
  return { calls, errors }
}

test('student selection, separate handout defaults, chapter layout, drawer focus, and responsive columns', async ({ page }) => {
  const { calls, errors } = await install(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/training?mode=student')
  await expect(page.getByRole('heading', { name: '按学生训练', exact: true })).toBeVisible()
  await expect(page.getByLabel('选择合成学生1', { exact: true })).toBeVisible()
  await expect(page.getByText('已选 0 人', { exact: true })).toBeVisible()
  await expect(page.getByTestId('go-paper')).toBeDisabled()
  await page.getByRole('button', { name: '合成学生1', exact: true }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toBeHidden()
  await page.getByRole('button', { name: '全选本班', exact: true }).first().click()
  await page.getByRole('button', { name: '刷题讲义', exact: true }).click()
  await expect(page.getByLabel('每卷题数', { exact: true })).toHaveValue('30')
  await expect(page.getByLabel('同一技能最多', { exact: true })).toHaveValue('3')
  await expect(page.getByLabel('巩固题最多', { exact: true })).toHaveValue('6')
  const columns = await page.locator('.practice-layout').evaluate(element => getComputedStyle(element).gridTemplateColumns.split(' '))
  expect(columns).toHaveLength(3); expect(columns[0]).toBe('300px'); expect(columns[2]).toBe('340px')
  expect(await page.locator('.paper-settings-panel').evaluate(element => getComputedStyle(element).position)).toBe('sticky')
  await page.screenshot({ path: '../output/test_practice_implementation_20261003/practice-layout-synthetic.png', animations: 'disabled' })
  await page.getByTestId('go-paper').click()
  await expect(page).toHaveURL(/mode=paper/)
  await expect(page.getByTestId('generate-paper-draft')).toBeEnabled()
  const diagnosisCalls = calls.filter(call => call.path === '/api/training/diagnosis')
  expect(diagnosisCalls[0]?.body.response_mode).toBe('display')
  expect(diagnosisCalls[diagnosisCalls.length - 1]?.body.response_mode).toBeUndefined()
  expect(calls.some(call => call.path === '/api/training/personalized-drafts' && Object.keys(call.body).length)).toBe(false)
  await page.goto('/training?mode=chapter')
  await expect(page.getByLabel('章节与小节')).toBeVisible()
  await expect(page.getByTestId('go-paper')).toBeDisabled()
  await page.setViewportSize({ width: 1100, height: 800 })
  expect(await page.locator('.practice-layout').evaluate(element => getComputedStyle(element).gridTemplateColumns.split(' ').length)).toBe(1)
  expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false)
  expect(errors).toEqual([])
})

test('wrong book preview, scope filtering, options, export, and download', async ({ page }) => {
  const { calls, errors } = await install(page)
  await page.goto('/training?mode=student')
  await page.getByLabel('选择合成学生1', { exact: true }).check()
  await page.getByLabel('选择合成学生3', { exact: true }).check()
  await page.getByRole('button', { name: '错题本', exact: true }).click()
  await expect(page.getByText(/可导出 3 道错题/)).toBeVisible()
  await page.getByRole('button', { name: '专项', exact: true }).click()
  await page.getByLabel('选择合成节', { exact: true }).check()
  await expect(page.getByText(/不在所选章节 1 道/)).toBeVisible()
  await page.getByLabel('每题标注来源（考试 · 题号）', { exact: true }).uncheck()
  await page.getByRole('button', { name: /导出错题本（2 份/ }).click()
  await expect(page.getByText('已完成 · 3 道错题', { exact: true })).toBeVisible()
  expect(calls.find(call => call.path === '/api/students/wrong-question-books')?.body).toMatchObject({ student_ids: [1, 3], scope_keys: [section], session_ids: [7, 8], include_source_label: false, include_answer_space: true })
  const downloadEvent = page.waitForEvent('download')
  await page.getByRole('button', { name: '下载全部错题本', exact: true }).click()
  expect((await downloadEvent).suggestedFilename()).toBe('TEST-books.zip')
  await expect(page.getByText(/下载已完成，本机临时导出文件已清除/)).toBeVisible()
  expect(errors).toEqual([])
})

const returnPaper = {
  paper_instance_id: 'a'.repeat(64),
  paper_batch_id: 'f'.repeat(64),
  draft_id: 'd'.repeat(64),
  draft_revision: 1,
  student_id: 'SYN-S01',
  student_code: 'S01',
  student_name: '合成学生',
  class_id: 'SYN-C01',
  series_version: 1,
  status: 'frozen',
  revision: 2,
  layout_version: 'personalized-paper-school-a4-v1',
  budget: {
    version: 'whole-paper-context-budget-v1',
    status: 'ready',
    context_window_tokens: 32768,
    question_count: 1,
    criterion_point_count: 2,
    image_count: 0,
    page_count: 2,
    page_count_is_estimate: false,
    estimated_input_tokens: 1000,
    estimated_output_tokens: 500,
    estimated_total_tokens: 1500,
    limits: { questions: 12, criterion_points: 120, images: 48, pages: 20 },
    blockers: [],
  },
  question_count: 1,
  criterion_point_count: 2,
  items: [],
  pages: [{ page_number: 1 }, { page_number: 2 }],
  downloads: {
    review_docx: null,
    reviewed_docx: null,
    frozen_pdf: '/api/training/returnPaper.pdf',
  },
  created_at: '2026-07-30T08:00:00+00:00',
} satisfies PersonalizedPaperInstance

const returnEmptyBatch = {
  batch_id: 'b'.repeat(64),
  paper_batch_id: 'f'.repeat(64),
  status: 'manual_review',
  revision: 1,
  duplicate_upload: false,
  submissions: [{
    submission_id: 'c'.repeat(64),
    paper_instance_id: returnPaper.paper_instance_id,
    student_id: returnPaper.student_id,
    student_code: returnPaper.student_code,
    student_name: returnPaper.student_name,
    class_id: returnPaper.class_id,
    series_version: 1,
    status: 'manual_review',
    revision: 1,
    expected_total_pages: 2,
    missing_pages: [1, 2],
    issue_codes: [],
    assessment_started: false,
  }],
  pages: [],
  candidates: [{
    paper_instance_id: returnPaper.paper_instance_id,
    student_id: returnPaper.student_id,
    student_code: returnPaper.student_code,
    student_name: returnPaper.student_name,
    series_version: 1,
    total_pages: 2,
  }],
  history: [],
  created_at: '2026-07-30T08:00:00+00:00',
  updated_at: '2026-07-30T08:00:00+00:00',
} satisfies TrainingScanBatch

const returnAnomalyBatch = {
  ...returnEmptyBatch,
  revision: 2,
  pages: [{
    scan_page_id: 'e'.repeat(64),
    upload_id: '9'.repeat(64),
    upload_page_number: 1,
    submission_id: null,
    paper_instance_id: null,
    page_number: null,
    total_pages: null,
    issue_code: 'identity_unreadable',
    state: 'unassigned',
    rotation_degrees: 0,
    preview_url: `/api/training/scan-batches/${'b'.repeat(64)}/pages/${'e'.repeat(64)}/preview`,
  }],
} satisfies TrainingScanBatch

const returnPending = {
  run_id: 'a'.repeat(64),
  submission_id: returnEmptyBatch.submissions[0]!.submission_id,
  submission_revision: 1,
  status: 'partial',
  request_count: 1,
  expected_question_count: 1,
  expected_point_count: 2,
  model_name: 'synthetic-model',
  usage: {
    prompt_tokens: 100,
    completion_tokens: 20,
    total_tokens: 120,
  },
  latency_ms: 50,
  issue_codes: [],
  error_code: null,
  questions: [{
    task_item_code: 'P4-SYN-Q01',
    item_order: 1,
    status: 'review_required',
    met_count: 1,
    not_met_count: 0,
    uncertain_count: 1,
    unreadable_count: 0,
    total_count: 2,
    review_status: 'pending',
    review_points: [{
      point_id: 'p1',
      content: '写出关键步骤',
      state: 'met',
      evidence: '步骤可见',
      teacher_locked: false,
      teacher_reason: null,
      actor_ref: null,
      lock_revision: 0,
    }, {
      point_id: 'p2',
      content: '得到正确结论',
      state: 'uncertain',
      evidence: '原图字迹不清',
      teacher_locked: false,
      teacher_reason: null,
      actor_ref: null,
      lock_revision: 0,
    }],
  }],
  review_revision: 1,
  control_state: 'active',
  workflow_status: 'review_required',
  action_message: '请复核不确定判定点。',
  attempts: [],
} satisfies TrainingAssessmentOutcome

const returnComplete = {
  ...returnPending,
  workflow_status: 'completed',
  review_revision: 2,
  questions: [{
    ...returnPending.questions[0]!,
    status: 'complete',
    met_count: 1,
    not_met_count: 1,
    uncertain_count: 0,
    review_status: 'completed',
    review_points: [
      returnPending.questions[0]!.review_points[0]!,
      {
        ...returnPending.questions[0]!.review_points[1]!,
        state: 'not_met',
        evidence: '教师核对后确认结论错误',
        teacher_locked: true,
        teacher_reason: '教师核对原始训练答卷',
        actor_ref: 'local_teacher',
        lock_revision: 2,
      },
    ],
  }],
} satisfies TrainingAssessmentOutcome

async function installReturn(page: Page, count = 3, performance = false) {
  const base = await install(page)
  const submissions = Array.from({ length: count }, (_, i) => ({ ...returnEmptyBatch.submissions[0]!,
    submission_id: (i + 1).toString(16).padStart(64, '0'), paper_instance_id: (i + 100).toString(16).padStart(64, '0'), student_id: String(i + 1), student_name: `测试学生${i + 1}`, student_code: `TEST-${String(i + 1).padStart(2, '0')}`, status: 'ready' as const, missing_pages: [] }))
  const batch: TrainingScanBatch = { ...returnEmptyBatch, status: 'ready', submissions,
    pages: submissions.map(s => ({ ...returnAnomalyBatch.pages[0]!, scan_page_id: s.submission_id, submission_id: s.submission_id, state: 'assigned', issue_code: null, page_number: 1, preview_url: `/api/TEST-scan/${s.submission_id}` })) }
  const item = { item_id: 'TEST-item', item_order: 1, slot: 1, question_id: 101, question_number: '1', question_text: '合成训练题',
    stage: 'direct' as const, target: { stable_key: skill }, matched_key: skill, matched_name: '合成技能', criterion_version_id: 'c'.repeat(64), criterion_point_count: 2,
    difficulty: 3, source_paper: 'TEST', reason: '合成推荐理由', locked: false, replacement_history: [] }
  const draft: PersonalizedRecommendationDraft = { draft_id: returnPaper.draft_id, status: 'draft', revision: 3, result_version: 'e'.repeat(64), engine_version: 'TEST-v1', source_version: 'f'.repeat(64),
    config: { paper_mode: 'individual', purpose: 'training', question_count: performance ? 10 : 1, difficulty_max: 8 }, warnings: [], history: [],
    students: submissions.map(s => ({ student_id: s.student_id, student_name: s.student_name!, student_code: s.student_code!, class_id: 'TEST一班', selection_mode: 'mastery_targeted', targets: [{ stable_key: skill }], items: [item], shortages: [], warnings: [] })) }
  const paperInstances = submissions.map(s => ({ ...returnPaper, paper_instance_id: s.paper_instance_id, student_id: s.student_id, student_name: s.student_name, student_code: s.student_code, question_count: performance ? 10 : 1, criterion_point_count: performance ? 25 : 2, review_docx_sha256: null, reviewed_docx_sha256: null, frozen_pdf_sha256: null, error_code: null, frozen_at: null }))
  const assessed = new Map<string, TrainingAssessmentOutcome>()
  submissions.forEach((s, i) => {
    if (!performance && i === 1) return
    const a = performance || i === 2 ? returnComplete : returnPending
    const questions: TrainingAssessmentQuestion[] = performance ? Array.from({ length: 10 }, (_, n) => ({ ...returnComplete.questions[0]!, task_item_code: `TEST-q${n}`, item_order: n + 1,
      total_count: n < 5 ? 3 : 2, met_count: n < 5 ? 3 : 2, not_met_count: 0, review_points: Array.from({ length: n < 5 ? 3 : 2 }, (_, p) => ({ ...returnComplete.questions[0]!.review_points[0]!, point_id: `TEST-p${p}`, candidate_state: 'met' as const })) }))
      : a.questions.map(q => ({ ...q, review_points: q.review_points.map(p => ({ ...p, candidate_state: p.state })) }))
    assessed.set(s.submission_id, { ...a, submission_id: s.submission_id, expected_question_count: performance ? 10 : 1, expected_point_count: performance ? 25 : 2, questions })
  })
  const writes: string[] = [], reads: string[] = []
  let concurrent = 0, maximum = 0
  await page.route(/^https?:\/\/[^/]+\/api\//, async route => {
    const path = new URL(route.request().url()).pathname, method = route.request().method()
    if (path.startsWith('/api/TEST-scan/')) {
      await route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="850"><rect width="600" height="850" fill="white"/><text x="40" y="60" font-size="24">TEST synthetic answer sheet</text><path d="M40 160H560M40 360H560M40 560H560" stroke="#ddd"/><text x="50" y="220" font-size="22" fill="#385479">x = 3</text></svg>' }); return
    }
    let value: unknown
    if (path === `/api/training/personalized-drafts/${draft.draft_id}`) value = draft
    else if (path.endsWith('/paper-instances')) value = { items: paperInstances }
    else if (path.endsWith('/paper-batches')) value = { items: [] }
    else if (path === '/api/training/scan-batches') value = { items: [{ ...batch, submission_count: count }] }
    else if (path === `/api/training/scan-batches/${batch.batch_id}`) value = batch
    else if (/\/submissions\/[^/]+\/(assessment|assessment\/reviews|feedback)$/.test(path)) {
      const id = path.split('/')[4]!
      if (method === 'POST') {
        writes.push(path)
        const body = route.request().postDataJSON()
        const old = assessed.get(id)!
        const a = { ...old, review_revision: old.review_revision + 1, questions: old.questions.map(q => ({ ...q, review_status: 'completed', uncertain_count: 0,
          review_points: q.review_points.map(p => p.point_id === body.point_id ? { ...p, state: body.final_state, teacher_locked: true, lock_revision: 2, teacher_reason: body.teacher_reason } : p) })) }
        assessed.set(id, a); value = a
      } else {
        reads.push(path); concurrent++; maximum = Math.max(maximum, concurrent)
        await new Promise(resolve => setTimeout(resolve, 10)); concurrent--
        value = path.endsWith('/feedback') ? null : assessed.get(id)
        if (!value) { await route.fulfill({ status: 404, headers: { 'x-request-id': 'TEST' }, json: { error: { kind: 'not_found', code: path.endsWith('/feedback') ? 'training_feedback_not_found' : 'training_assessment_not_found', message: 'TEST', request_id: 'TEST', retryable: false, details: {} } } }); return }
      }
    } else { await route.fallback(); return }
    await route.fulfill({ json: value })
  })
  return { ...base, writes, reads, maximum: () => maximum, draftId: draft.draft_id }
}

test('return workspace matches the draft header and responsive prototype, cancels costs and locks one point', async ({ page }) => {
  const mock = await installReturn(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(`/training?mode=paper&draft=${mock.draftId}&from=workbench&focus=review`)
  await expect(page.getByRole('heading', { name: '训练卷', exact: true })).toBeVisible()
  await expect(page.locator('.page-header__navigation')).toHaveCount(0)
  await expect(page.locator('.page-header [aria-label="草稿步骤"]')).toBeVisible()
  await expect(page.locator('.return-queue__rows>button[aria-pressed=true]')).toContainText('测试学生1')
  await expect(page.locator('[data-pending=true]').first()).toBeFocused()
  await expect(page).not.toHaveURL(/focus=/)
  await page.getByRole('button', { name: '判定 1 份（1 次模型请求）', exact: true }).click()
  await expect(page.getByRole('dialog')).toContainText('共 1 次模型请求，产生费用')
  await page.getByRole('button', { name: '取消', exact: true }).click()
  expect(mock.writes).toEqual([])
  await expect(page.getByRole('button', { name: '判定 1 份（1 次模型请求）', exact: true })).toBeFocused()
  await page.locator('[data-pending=true] [data-final-state=not_met]').click()
  await expect(page.locator('.ledger-point')).toContainText(['写出关键步骤', '得到正确结论'])
  expect(mock.writes.filter(path => path.endsWith('/assessment/reviews'))).toHaveLength(1)
  const expectedLayouts: Record<number, { display: string; direction: string; columns: number }> = {
    1440: { display: 'grid', direction: 'row', columns: 3 }, 1280: { display: 'grid', direction: 'row', columns: 3 },
    1100: { display: 'grid', direction: 'row', columns: 2 }, 900: { display: 'flex', direction: 'column', columns: 0 },
  }
  for (const width of [1440, 1280, 1100, 900]) {
    await page.setViewportSize({ width, height: 900 })
    const layout = await page.locator('.return-workspace').evaluate(element => {
      const style = getComputedStyle(element)
      return { display: style.display, direction: style.flexDirection, columns: Number(style.display === 'grid') * style.gridTemplateColumns.split(' ').length }
    })
    expect(layout).toEqual(expectedLayouts[width])
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true)
    await expect(page.locator('.return-ledger__footer')).toBeVisible()
    const overlap = await page.locator('.return-ledger').evaluate(el => {
      const body = el.querySelector('.return-ledger__body')!.getBoundingClientRect(), footer = el.querySelector('footer')!.getBoundingClientRect()
      return body.bottom > footer.top + 1
    })
    expect(overlap).toBe(false)
    await page.screenshot({ path: `../output/TEST-training-return-20261004/synthetic-${width}.png`, animations: 'disabled', fullPage: true })
  }
  await page.locator('.return-ledger').scrollIntoViewIfNeeded()
  await page.screenshot({ path: '../output/TEST-training-return-20261004/synthetic-900-ledger.png', animations: 'disabled' })
  await page.emulateMedia({ reducedMotion: 'reduce' })
  expect(await page.locator('.training-scan-panel').evaluate(el => [...el.getAnimations({ subtree: true })].filter(a => a.playState === 'running').length)).toBe(0)
  expect(mock.errors).toEqual([])
})

test('forty returned papers load at most eighty status requests and four at a time', async ({ page }, testInfo) => {
  const mock = await installReturn(page, 40, true)
  const start = Date.now()
  await page.goto(`/training?mode=paper&draft=${mock.draftId}`)
  await expect(page.locator('.return-queue__rows>button')).toHaveCount(40)
  await expect(page.locator('.return-summary__counts')).toContainText('待更新 40 份')
  expect(mock.reads).toHaveLength(80); expect(mock.maximum()).toBeLessThanOrEqual(4)
  await testInfo.attach('performance', { body: JSON.stringify({ students: 40, questionsPerPaper: 10, pointsPerPaper: 25, enterToAllStatesMs: Date.now() - start, statusRequests: mock.reads.length, maximumConcurrent: mock.maximum() }), contentType: 'application/json' })
  expect(mock.errors).toEqual([])
})
