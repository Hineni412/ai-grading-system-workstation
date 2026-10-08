import assert from 'node:assert/strict'
import { expect, test } from '@playwright/test'
import { defaultPaperRules, type AssemblyAssistantResult, type AssemblyDraft, type AssemblyExamResult } from '../src/api/assembly'
import { decodeTrainingOverview, type TrainingOverview, type TrainingOverviewNode } from '../src/api/training'
import { decodeQuestionListResponse, decodeQuestionSkillIndex, type QuestionBankFacets } from '../src/api/question-bank'

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('ai-grading:curriculum-scope:v1', 'bnu24-math-g8-upper'))
  await page.emulateMedia({ reducedMotion: 'reduce' })
})

test('real API question edits, guarded dialogs and shared assembly basket persist', async ({ page }, testInfo) => {
  const requests: string[] = []
  page.on('request', request => requests.push(new URL(request.url()).pathname))
  await page.goto('/question-bank?tab=skill')
  await expect(page.getByRole('heading', { name: '题库管理', exact: true })).toBeVisible()
  await expect(page.locator('.qb-chapter-options')).toBeVisible()
  await expect(page.locator('.qb-question-card').first()).toBeVisible({ timeout: 60_000 })
  await expect(page.locator('.page-tabs .is-active')).toHaveText('按标签')
  await expect(page.getByRole('checkbox', { name: '折叠重复题' })).toBeChecked()
  for (const width of [1440, 1280]) {
    await page.setViewportSize({ width, height: 900 })
    const panes = await page.locator('.qb-tag-layout > .qb-browse-pane').evaluateAll(elements => elements.map(element => { const rect = element.getBoundingClientRect(); return { x: rect.x, y: rect.y, width: rect.width, height: rect.height } }))
    expect(panes).toHaveLength(2)
    expect(panes[0]?.width).toBe(300)
    expect(panes[1]?.x).toBeGreaterThan((panes[0]?.x ?? 0) + 300)
    expect(panes[1]?.height).toBeCloseTo(panes[0]?.height ?? 0, 0)
    await page.screenshot({ path: testInfo.outputPath(`question-bank-browse-${width}.png`) })
  }
  await page.getByRole('button', { name: '上传试卷', exact: true }).click()
  await expect(page.getByRole('dialog', { name: '上传试卷与任务' })).toBeVisible()
  await page.getByRole('dialog', { name: '上传试卷与任务' }).getByRole('button', { name: '关闭', exact: true }).press('Escape')
  await expect(page.getByRole('button', { name: '上传试卷', exact: true })).toBeFocused()
  await page.getByRole('searchbox', { name: '搜题干' }).fill('LONG_FORMULA')
  await expect(page.locator('.qb-question-card')).toHaveCount(1)
  const heading = page.getByRole('button', { name: '第 1 题', exact: true })
  await heading.click()
  await expect(page.locator('.qb-annotation')).toBeVisible()
  await expect(page.locator('.qb-evidence-row')).toHaveCount(2)
  await expect(page.locator('.qb-inline-card__heading')).toHaveCSS('position', 'sticky')
  for (const width of [1440, 1280]) {
    await page.setViewportSize({ width, height: 900 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false)
    expect(await page.locator('.qb-question-pane').evaluate(el => el.getBoundingClientRect().width)).toBeGreaterThan(940)
    await page.screenshot({ path: testInfo.outputPath(`question-bank-skill-${width}.png`) })
  }
  expect(requests.filter(path => path === '/api/question-bank/questions/1')).toHaveLength(1)
  expect(requests.filter(path => path === '/api/question-bank/questions/1/solution-evidence')).toHaveLength(1)
  expect(requests.filter(path => path === '/api/question-bank/criteria/questions/1')).toHaveLength(1)
  await page.locator('.qb-inline-card.is-expanded .qb-card-menu summary').click()
  await page.locator('.qb-inline-card.is-expanded .qb-card-menu summary').press('Escape')
  await expect(page.locator('.qb-inline-card.is-expanded .qb-card-menu')).not.toHaveAttribute('open')
  await expect(page.locator('.qb-annotation')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(heading).toBeFocused()
  await expect(page.locator('.qb-annotation')).toHaveCount(0)
  await heading.click()
  await page.getByText('编辑题目属性 / 选择小节', { exact: true }).click()
  await page.getByLabel('要添加的标签类别').selectOption('ability')
  await page.locator('.qb-tag-add').getByRole('button', { name: '添加', exact: true }).click()
  await page.getByLabel('能力标签值').last().fill('运算能力')
  await page.getByRole('button', { name: '保存标签', exact: true }).click()
  await expect(page.getByText('标签已经保存。', { exact: true })).toBeVisible()
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: '加入试卷篮', exact: true }).click()
  const basketButton = page.getByRole('button', { name: /试卷篮 · 1/ })
  await expect(basketButton).toBeVisible()
  await basketButton.click()
  await expect(page.getByRole('dialog').filter({ has: page.getByText('试卷篮 · 1', { exact: true }) })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(basketButton).toBeFocused()
  await page.getByRole('button', { name: '按试卷', exact: true }).click()
  const paperMenu = page.locator('.paper-card__more-toggle').first()
  await paperMenu.click()
  await page.getByRole('menuitem', { name: '编辑资料', exact: true }).click()
  await expect(page.getByRole('dialog', { name: '编辑试卷资料' })).toBeVisible()
  await page.getByRole('dialog', { name: '编辑试卷资料' }).press('Escape')
  await expect(paperMenu).toBeFocused()
  await expect(page.getByRole('button', { name: '维护', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: '待处理', exact: true }).click()
  const taxonomyButton = page.locator('.qb-todo-group').filter({ has: page.getByRole('heading', { name: '新词例外', exact: true }) }).getByRole('button', { name: '去处理 →' })
  await taxonomyButton.click()
  await expect(page.getByRole('dialog', { name: '标签治理', exact: true })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(taxonomyButton).toBeFocused()
  await page.getByRole('button', { name: '按标签', exact: true }).click()
  await expect(page.locator('.qb-chapter-pane')).toBeVisible()
  await expect(page.locator('.qb-question-card')).toHaveCount(20)
  const repairButton = page.getByRole('button', { name: 'AI 补齐', exact: true })
  await repairButton.click()
  await expect(page.getByRole('dialog', { name: '只补缺失部分' })).toContainText('技能关联')
  await expect(page.getByRole('dialog', { name: '只补缺失部分' })).toContainText('逐题清单')
  await page.screenshot({ path: testInfo.outputPath('question-bank-repair-preview.png') })
  await page.keyboard.press('Escape')
  await expect(repairButton).toBeFocused()
  await page.goto('/question-assembly?mode=browse')
  await expect(page.locator('.page-tabs .is-active')).toHaveText('整理与导出')
  await expect(page.locator('.page-tabs button')).toHaveCount(2)
  await expect(page.locator('.assembly-editor__workspace')).toBeVisible()
  for (const width of [1440, 1280]) {
    await page.setViewportSize({ width, height: 900 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false)
    expect(await page.locator('.assembly-editor__workspace > *').evaluateAll(elements => elements.map(element => { const rect = element.getBoundingClientRect(); return { x: rect.x, y: rect.y, width: rect.width, height: rect.height } }))).toEqual([{ x: 204, y: 72, width: 280, height: 808 }, { x: 498, y: 72, width: width - 836, height: 808 }, { x: width - 324, y: 72, width: 300, height: 808 }])
    await page.screenshot({ path: testInfo.outputPath(`question-assembly-${width}.png`) })
  }
  await page.getByRole('button', { name: '去题库选题', exact: true }).first().click()
  await expect(page).toHaveURL(/question-bank\?tab=skill/)
})

test('real API keeps all 2005 paper questions and restores the selected question', async ({ page }) => {
  await page.goto('/question-bank?tab=paper')
  await page.getByRole('button', { name: '按试卷', exact: true }).click()
  await page.getByRole('button', { name: '匿名九年级期末试卷', exact: true }).click()
  await expect(page.locator('.qb-paper-detail .qb-question-card')).toHaveCount(2005, { timeout: 60000 })
  await expect(page.locator('.qb-paper-detail .qb-number-nav button')).toHaveCount(2005)
  await page.locator('.qb-number-nav button').first().click()
  await expect(page.locator('.qb-paper-detail .qb-annotation')).toBeVisible()
  await expect(page).toHaveURL(/question=1/)
  await page.reload()
  await expect(page.locator('.qb-paper-detail .qb-annotation')).toBeVisible({ timeout: 60000 })
  await page.keyboard.press('Escape')
})

test('real API todo dialogs and paper jump preserve focus and responsive layout', async ({ page }, testInfo) => {
  await page.goto('/question-bank?tab=todo')
  await page.getByRole('button', { name: '待处理', exact: true }).click()
  await expect(page.locator('.qb-todo-stats')).toContainText('分析未完成')
  await expect(page.locator('.qb-todo-group').first().getByRole('button', { name: '判定点批量审核' })).toBeVisible()
  const criteriaButton = page.getByRole('button', { name: '判定点批量审核', exact: true })
  await criteriaButton.click()
  await expect(page.getByRole('dialog', { name: '判定点待审核', exact: true })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(criteriaButton).toBeFocused()
  expect(await page.locator('.qb-todo-group').evaluateAll(elements => elements.every(element => element.scrollHeight <= element.clientHeight + 1))).toBe(true)
  for (const width of [1440, 1280]) {
    await page.setViewportSize({ width, height: 900 })
    await page.screenshot({ path: testInfo.outputPath(`question-bank-todo-${width}.png`) })
  }
  await page.locator('.qb-todo-group').filter({ has: page.getByRole('heading', { name: /^未挂技能/ }) }).getByRole('button', { name: '去处理 →' }).first().click()
  await expect(page.locator('.qb-paper-detail .qb-annotation')).toBeVisible({ timeout: 60000 })
  await expect(page.locator('.qb-paper-detail .qb-inline-card.is-expanded .qb-inline-card__title')).toBeInViewport()
  for (const width of [1440, 1280]) {
    await page.setViewportSize({ width, height: 900 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false)
    await page.screenshot({ path: testInfo.outputPath(`question-bank-paper-${width}.png`) })
  }
})

test('tag combinations, chapter keyboard controls and readable counts at 1280 and 1920', async ({ page }, testInfo) => {
  const reads: string[] = []
  page.on('request', request => {
    const url = new URL(request.url())
    if (url.pathname === '/api/question-bank/questions' || url.pathname === '/api/question-bank/facets') reads.push(url.pathname)
  })
  const indexResponse = await page.request.get('/api/question-bank/skill-index?curriculum_volume_id=bnu24-math-g8-upper')
  const index = decodeQuestionSkillIndex(await indexResponse.json())
  const sampleResponse = await page.request.get('/api/question-bank/questions?keyword=LONG_FORMULA&page_size=1&include_skills=true')
  const sample = decodeQuestionListResponse(await sampleResponse.json()).items[0]
  expect(sample).toBeDefined()
  assert.ok(sample, 'TEST-question fixture is missing')
  const chapterData = index.chapters[0]
  const first = chapterData?.sections[0]
  const second = chapterData?.sections[1]
  assert.ok(chapterData && first && second, 'TEST-chapter fixture is missing')
  const catalog = Array.from({ length: 36 }, (_, offset) => ({ value: `kp_TEST_unrelated_t${String(offset + 1).padStart(2, '0')}`, label: `题型·TEST题型${offset + 1}`, section_id: offset % 2 ? second.id : first.id, count: 1, definition: '', common_knowledge_points: [] }))
  index.types = catalog
  index.question_count = 36
  index.coverage = { types: 36, knowledge_points: 36 }
  index.gaps = { types: 0, knowledge_points: 0 }
  decodeQuestionSkillIndex(index)
  const records = catalog.map((type, offset) => ({ ...sample, id: offset + 1, question_number: String(offset + 1),
    question_text: offset === 0 ? sample.question_text : `TEST标签筛选题目${offset + 1}：计算并说明过程。`,
    rich_content: offset === 0 ? sample.rich_content : { available: false, question_block_count: 0, answer_block_count: 0, question_blocks: [], answer_blocks: [] },
    asset_urls: offset === 0 ? sample.asset_urls : [], has_images: offset === 0,
    labels: { primary_type: { key: type.value, label: type.label }, secondary_types: [], knowledge_points: [{ key: `kpTEST${offset + 1}`, label: `TEST知识点${offset + 1}` }] },
  }))
  decodeQuestionListResponse({ items: records.slice(0, 20), total: 36, page: 1, page_size: 20, total_pages: 2 })
  const matches = (url: URL) => records.filter(question => {
    const sections = url.searchParams.getAll('curriculum_sections')
    const type = catalog.find(type => type.value === question.labels.primary_type.key)
    return (!sections.length || sections.includes(chapterData.id) || sections.includes(type?.section_id ?? ''))
      && (!url.searchParams.getAll('abilities').length || question.id === 1)
      && (!url.searchParams.getAll('methods').length || question.id === 1)
      && (!url.searchParams.get('keyword') || question.question_text.includes(url.searchParams.get('keyword') ?? ''))
  })
  await page.route('**/api/question-bank/skill-index?**', route => route.fulfill({ json: index }))
  await page.route('**/api/question-bank/repair-preview?**', route => route.fulfill({ json: { curriculum_volume_id: index.curriculum_volume_id, kind: 'all', fingerprint: 'a'.repeat(64), scanned_count: 36, question_count: 0, repairable_count: 0, model_calls: 0, items: [], counts: { tags: 0, evidence: 0, criteria: 0, skills: 0, types: 0, knowledge_points: 0 } } }))
  await page.route('**/api/question-bank/questions?**', route => {
    const url = new URL(route.request().url())
    const chosen = matches(url)
    const size = Number(url.searchParams.get('page_size') ?? 20)
    return route.fulfill({ json: { items: chosen.slice(0, size), total: chosen.length, page: 1, page_size: size, total_pages: Math.ceil(chosen.length / size) } })
  })
  await page.route('**/api/question-bank/facets?**', route => {
    const url = new URL(route.request().url())
    const chosen = matches(url)
    const facets: QuestionBankFacets = { primary_sections: [{ value: first.id, count: 18 }, { value: second.id, count: 18 }],
      task_types: catalog.filter(type => chosen.some(question => question.labels.primary_type.key === type.value)).map(type => ({ value: type.value, count: 1 })),
      knowledge_points: catalog.map((type, offset) => ({ value: `八年级上册｜${chapterData.label}｜${offset % 2 ? second.label : first.label}｜TEST知识点${offset + 1}`, count: 1 })),
      abilities: [{ value: '运算能力', count: 1 }], methods: [{ value: '配方法', count: 1 }],
      exam_scopes: [], curriculum_sections: [], curriculum_chapters: [], thoughts: [], models: [], special_types: [], error_types: [], error_pattern_categories: [], student_levels: [], teaching_stages: [], sub_skills: [], question_types: [], years: [], exam_types: [], grades: [] }
    return route.fulfill({ json: facets })
  })
  const roster = Array.from({ length: 30 }, (_, i) => ({ id: i + 1, student_code: `TEST-${i + 1}`, name: `合成学生${i + 1}`, class_name: i < 15 ? 'TEST一班' : 'TEST二班', created_at: null }))
  await page.route('**/api/students', route => route.fulfill({ json: { items: roster, total: roster.length } }))
  await page.route('**/api/training/overview', route => {
    const body = route.request().postDataJSON() as { scope: { mode: string; class_id?: string } }
    const scoped = body.scope.mode === 'class'
    const students = scoped ? roster.filter(s => s.class_name === body.scope.class_id) : roster
    const distribution = scoped ? { weak: 3, unsteady: 4, stable: 8, insufficient: 0 } : { weak: 5, unsteady: 7, stable: 18, insufficient: 0 }
    const node = (key: string, kind: 'chapter' | 'section', empty = false): TrainingOverviewNode => ({
      knowledge_key: key, kind, display_name: key, chapter_key: chapterData.id, section_key: kind === 'section' ? key : '',
      group_mastery: empty ? null : scoped ? .6 : .74, evidence_student_count: empty ? 0 : students.length,
      distribution: empty ? { weak: 0, unsteady: 0, stable: 0, insufficient: 0 } : distribution, students: [],
    })
    const overview: TrainingOverview = {
      scope: { mode: scoped ? 'class' : 'all', student_ids: students.map(s => String(s.id)), ...(scoped ? { class_ids: [body.scope.class_id!] } : {}) },
      exam_scope: { mode: 'semester', curriculum_volume_id: index.curriculum_volume_id, session_ids: [7, 8], sessions: [{ session_id: 7, session_name: 'TEST第一场考试' }, { session_id: 8, session_name: 'TEST第二场考试' }] },
      warnings: [], nodes: [node(chapterData.id, 'chapter'), node(first.id, 'section'), node(second.id, 'section', true)], students: [],
      summary: { student_count: students.length, evidence_student_count: students.length, exam_student_count: students.length, exam_score_rate: .7, topic_count: 0, skill_count: 0, weak_topic_count: 0, weak_skill_count: 0 },
    }
    decodeTrainingOverview(overview)
    return route.fulfill({ json: overview })
  })
  await page.goto('/question-bank?tab=tag')
  await expect(page.locator('.qb-question-card').first()).toBeVisible({ timeout: 60_000 })
  const mastery = page.locator(`.qb-chapter-mastery[data-knowledge="${chapterData.id}"]`)
  await expect(mastery).toContainText('74%')
  await expect(mastery).toContainText(/12.*30/)
  await expect(page.locator('.qb-chapter-overview-scope')).toContainText('2 场考试')
  await page.locator('.qb-chapter-overview-scope').getByRole('button', { name: 'TEST一班', exact: true }).click()
  await expect(mastery).toContainText('60%')
  await expect(mastery).toContainText(/7.*15/)
  await expect(page.locator('.qb-result-total')).toContainText('共 36 题')
  await page.locator('.qb-chapter-overview-scope').getByRole('button', { name: '全部学生', exact: true }).click()
  await expect(mastery).toContainText('74%')
  await expect(page.locator('.qb-result-total')).toContainText('本学期全部题目')
  for (const width of [1280, 1920]) {
    await page.setViewportSize({ width, height: 900 })
    await expect(page.locator('.qb-tag-lines')).toHaveClass(/is-clamped/)
    expect(await page.locator('.qb-tag-lines').evaluate(el => el.getBoundingClientRect().height)).toBeLessThanOrEqual(58)
    expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false)
    expect(await page.locator('.qb-tag-row').evaluateAll(rows => rows.every(row => {
      const bounds = row.getBoundingClientRect()
      return [...row.children].every(child => child.getBoundingClientRect().bottom <= bounds.bottom + 1)
    }))).toBe(true)
    await page.screenshot({ path: testInfo.outputPath(`tag-default-${width}.png`) })
  }
  await page.getByRole('button', { name: '展开全部 36 项', exact: true }).press('Enter')
  await expect(page.locator('.qb-tag-lines')).not.toHaveClass(/is-clamped/)
  expect(await page.locator('.qb-tag-lines').evaluate(el => el.getBoundingClientRect().height)).toBeGreaterThan(58)
  await page.getByRole('button', { name: '收起', exact: true }).press('Enter')
  await expect(page.locator('.qb-tag-lines')).toHaveClass(/is-clamped/)
  const dimensions = page.locator('.qb-dimension-tabs')
  await dimensions.getByRole('button', { name: '能力', exact: true }).click()
  const ability = page.locator('[data-tag-value="运算能力"]')
  await ability.focus(); await page.keyboard.press('Enter')
  await expect(page.locator('.qb-selected-conditions')).toContainText('能力：运算能力')
  await expect(page.locator('.qb-result-total')).toContainText('共 1 题')
  await expect(page.locator('.qb-question-card')).toHaveCount(1)
  const before = reads.length
  await dimensions.getByRole('button', { name: '方法', exact: true }).focus()
  await page.keyboard.press('Enter')
  await expect(page.locator('[data-tag-value="配方法"]')).toBeVisible()
  expect(reads).toHaveLength(before)
  await page.locator('[data-tag-value="配方法"]').press('Enter')
  await expect(page.locator('.qb-selected-conditions')).toContainText('方法：配方法')
  await expect(dimensions.getByRole('button', { name: /^能力/ }).locator('sup')).toHaveText('1')
  await page.getByRole('button', { name: '删除方法：配方法', exact: true }).press('Enter')
  await expect(page.locator('.qb-selected-conditions')).not.toContainText('方法：配方法')
  await page.getByRole('button', { name: '删除能力：运算能力', exact: true }).press('Enter')
  await expect(page.locator('.qb-question-card')).toHaveCount(20)
  const chapter = page.locator('.qb-chapter-name').nth(1)
  await chapter.press('Enter')
  await expect(page).toHaveURL(/sec=/)
  const children = page.locator('.qb-chapter-sections').first()
  await expect(children).toBeVisible()
  const chapterBox = page.locator('.qb-chapter-row input').first()
  await expect(chapterBox).toBeChecked()
  await children.locator('input').first().press('Space')
  expect(await chapterBox.evaluate(input => input instanceof HTMLInputElement && input.indeterminate)).toBe(true)
  await children.locator('input').first().press('Space')
  await expect(chapterBox).toBeChecked()
  for (const width of [1280, 1920]) {
    await page.setViewportSize({ width, height: 900 })
    await dimensions.getByRole('button', { name: '知识点', exact: true }).click()
    expect(await page.locator('.qb-tag-lines').evaluate(el => el.getBoundingClientRect().height)).toBeLessThanOrEqual(58)
    await page.screenshot({ path: testInfo.outputPath(`tag-chapter-${width}.png`) })
  }
  await page.getByRole('button', { name: '本学期全部章节', exact: true }).press('Enter')
  await page.getByRole('searchbox', { name: '搜题干' }).fill('LONG_FORMULA')
  const heading = page.getByRole('button', { name: '第 1 题', exact: true })
  await heading.click()
  await expect(page.locator('.qb-annotation')).toBeVisible()
  await expect(page.locator('.qb-chapter-options')).toBeHidden()
  await expect(page.getByRole('button', { name: /展开章节列表/ })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(heading).toBeFocused()
  await expect(page.locator('.qb-chapter-options')).toBeVisible()
})


test('assembly opens class workspace and keeps every subquestion available behind a compact target card', async ({ page }, testInfo) => {
  const volumeId = 'bnu24-math-g8-upper'
  const skill = 'sk_TEST_compact'
  await page.addInitScript(id => {
    localStorage.clear()
    localStorage.setItem('ai-grading:curriculum-scope:v1', id)
  }, volumeId)
  const roster = Array.from({ length: 30 }, (_, i) => ({ id: i + 1, student_code: `TEST-${i + 1}`, name: `合成学生${i + 1}`, class_name: 'TEST一班', created_at: null }))
  await page.route('**/api/students', route => route.fulfill({ json: { items: roster, total: roster.length } }))
  let draft: AssemblyDraft = {
    basket_ids: [], order_ids: [], sections: [], title: 'TEST班级补偿练习', header_text: '', include_answer: true,
    layout_mode: 'sequential', preview_mode: 'teacher', revision: 'a'.repeat(64), practice_rules: defaultPaperRules(),
  }
  const evidence: AssemblyExamResult = { student_count: 30, target_kind: 'skill', exams: [{
    session_id: 7, title: 'TEST多小问考试', date: '2026-10-02', class_ids: ['TEST一班'], student_count: 30, average_score: 50,
    questions: Array.from({ length: 15 }, (_, i) => ({
      key: `7:Q12(P${i + 1})`, session_id: 7, question_id: `Q12(P${i + 1})`, question_type: '解答题', full_score: 3,
      class_rate: Number((.35 + i * .02).toFixed(2)), student_count: 30, bank_question_id: null, difficulty: 4,
      class_rates: [{ class_id: 'TEST一班', student_count: 30, class_rate: Number((.35 + i * .02).toFixed(2)) }],
      cause_category_counts: i === 0 ? null : [{ category: '计算与化简', count: 28 }], cause_unclassified_count: i === 0 ? 30 : 2,
      cause_availability: { status: i === 0 ? 'stale' as const : 'current' as const, unclassified_by_reason: i === 0
        ? [{ reason: 'stale' as const, count: 30 }]
        : i === 2 ? [{ reason: 'positive_only' as const, count: 2 }]
          : [{ reason: 'review' as const, count: 1 }, { reason: 'missing' as const, count: 1 }] },
      skill_keys: [skill], skills: [{ key: skill, label: '根式化简与四则运算', in_volume: true }],
      question_text: `TEST第12(${i + 1})小问完整题干：化简根式并写出计算过程。`,
    })),
  }] }
  const result: AssemblyAssistantResult = {
    student_count: 30, evidence_student_count: 30, exam_student_count: 30, exam_count: 1, exam_score_rate: .5,
    weaknesses: [{ knowledge_key: skill, knowledge_point: '八年级上册｜第二章 实数｜3 二次根式｜根式化简与四则运算', mastery: .5, weak_student_count: 12, evidence_student_count: 30, exam_score_rate: .5, evidence_count: 450, candidate_count: 0 }],
    selected_target_keys: [skill], candidate_total: 0, candidates: [], target_kind: 'skill',
  }
  await page.route('**/api/question-assembly/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname
    if (path.endsWith('/draft')) {
      if (request.method() === 'PUT') draft = { ...draft, ...request.postDataJSON(), revision: 'b'.repeat(64) }
      await route.fulfill({ json: draft })
    } else if (path.endsWith('/exam-questions')) await route.fulfill({ json: evidence })
    else if (path.endsWith('/candidates')) await route.fulfill({ json: result })
    else if (path.endsWith('/records')) await route.fulfill({ json: { items: [], total: 0 } })
    else if (path.endsWith('/questions')) await route.fulfill({ json: { items: [], missing_question_ids: [] } })
    else await route.fulfill({ status: 404, json: { error: { code: 'TEST-unexpected', message: 'TEST-unexpected', request_id: 'TEST' } } })
  })
  await page.goto('/question-assembly')
  await expect(page.locator('.page-tabs .is-active')).toHaveText('班级组卷')
  const need = page.locator('.ca-need')
  await expect(need).toHaveCount(1)
  await expect(need).toHaveAttribute('aria-pressed', 'true')
  await expect(need.locator('.ca-related-summary')).toContainText('15 道相关题')
  await expect(need.locator('.ca-related-summary')).toContainText('12(1) 35%')
  await expect(need.locator('.ca-related-summary')).not.toContainText('12(2)')
  await expect(page.locator('.ca-related button')).toHaveCount(15)
  await expect(page.locator('.ca-original')).toContainText('错因归类已保存')
  await expect(need).toContainText('待复核')
  await expect(need).toContainText('仅有正向记录')
  await expect(need).not.toContainText('未归类')
  await page.locator('.ca-related button').nth(2).click()
  await expect(page.locator('.ca-original')).toContainText('仅有正向记录')
  await page.locator('.ca-related button').last().click()
  await expect(page.locator('.ca-original')).toContainText('第12(15)小问完整题干')
  for (const width of [1280, 1920]) {
    await page.setViewportSize({ width, height: 900 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false)
    await page.screenshot({ path: testInfo.outputPath(`class-assembly-compact-${width}.png`) })
  }
  await page.getByRole('button', { name: '整理与导出', exact: true }).click()
  await expect(page.locator('.page-tabs .is-active')).toHaveText('整理与导出')
  await page.getByRole('button', { name: '班级组卷', exact: true }).click()
  await expect(page.locator('.ca-related button')).toHaveCount(15)
  await page.locator('.ca-related button').first().press('Enter')
  await expect(page.locator('.ca-original')).toContainText('第12(1)小问完整题干')
})
