import { expect, test, type Page } from '@playwright/test'
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
function diagnosis(body: { scope: { mode: string; class_ids?: string[]; student_ids?: string[] } }) {
  const selected = students.filter(student => body.scope.mode === 'class' ? body.scope.class_ids?.includes(student.class_name) : body.scope.mode === 'selected' ? body.scope.student_ids?.includes(String(student.id)) : true)
  return { scope: body.scope, exam_scope: { mode: 'semester', curriculum_volume_id: volumeId, session_ids: [7, 8], sessions: [{ session_id: 7, session_name: 'TEST考试7' }, { session_id: 8, session_name: 'TEST考试8' }] },
    students: selected.map(student => ({ student_id: String(student.id), student_name: student.name, student_code: student.student_code, class_id: student.class_name, score_rate: .6, score_rate_source: 'current_exam', weak_points: [weak] })),
    knowledge_catalog: [{ knowledge_key: chapter, knowledge_point: '合成章', node_kind: 'chapter' }, { knowledge_key: section, knowledge_point: '合成节', node_kind: 'section', parent_knowledge_key: chapter }, { knowledge_key: skill, knowledge_point: '合成技能', node_kind: 'skill', parent_knowledge_key: section }],
    group_weak_points: [], knowledge_associations: [], coverage: { covered_items: 3, total_items: 3, missing_items: {} }, confirmed_concept_ids: [], suggested_terms: [], unmapped_terms: [], warnings: [], diagnosis_identity: 'question_tag' }
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
  await expect(page.getByRole('dialog')).not.toBeVisible()
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
