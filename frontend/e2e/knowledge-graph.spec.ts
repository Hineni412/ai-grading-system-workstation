import { expect, test, type Page } from '@playwright/test'
const volumeId = 'bnu24-math-g8-upper'
const catalog = { schema_version: 2, catalog_id: 'TEST-catalog', knowledge_standard_id: 'TEST-standard',
  publisher: 'TEST出版社', subject: '数学', edition: '2024',
  statistics: { raw_nodes: 10, excluded_nodes: 0, retained_nodes: 10, chapters: 5, sections: 5, knowledge_points: 0 },
  volumes: Array.from({ length: 5 }, (_, i) => ({ id: i === 2 ? volumeId : `TEST-volume-${i}`, order: i + 1,
    label: i === 2 ? '八年级上册' : `TEST第${i + 1}册`, grade: '八年级', semester: '上学期', textbook_version: '北师大版2024',
    source: { provider: 'TEST' }, statistics: { raw_nodes: 2, excluded_nodes: 0, retained_nodes: 2 },
    chapters: [{ id: 'ch', knowledge_id: 'ch', order: 1, number: '第一章', title: '测试章', label: '测试章', kind: 'chapter',
      display_name: '测试册｜测试章', source_ref: { node_id: 'ch', relative_url: '/TEST/ch' }, exam_scope_values: ['TEST'],
      sections: [{ id: 'sec', knowledge_id: 'sec', order: 1, number: '第一节', title: '测试节', label: '测试节', kind: 'lesson',
        display_name: '测试册｜测试章｜测试节', source_ref: { node_id: 'sec', relative_url: '/TEST/sec' }, knowledge_points: [] }] }] })) }
const students = [{ id: 1, student_code: 'S1', name: '测试甲', class_name: '一班', created_at: null }, { id: 2, student_code: 'S2', name: '测试乙', class_name: '二班', created_at: null }]
const distribution = { weak: 1, unsteady: 0, stable: 1, insufficient: 0 }
function node(key: string, kind: string, name: string, section = 'sec') {
  return { knowledge_key: key, kind, display_name: `测试册｜测试章｜测试节｜${name}`, chapter_key: 'ch', section_key: section,
    definition: '测试定义：验证详情展示。', in_volume: true, group_mastery: .5, group_interval_low: .3, group_interval_high: .7, evidence_student_count: 2, distribution,
    students: [{ student_id: '1', mastery: .2, tier: 'weak', interval_low: .1, interval_high: .4 }, { student_id: '2', mastery: .8, tier: 'stable', interval_low: .7, interval_high: .9 }] }
}
function overview() {
  return { scope: { mode: 'all', student_ids: ['1', '2'] }, exam_scope: { mode: 'semester', curriculum_volume_id: volumeId, session_ids: [7], sessions: [{ session_id: 7, session_name: 'TEST考试' }] }, warnings: [],
    summary: { student_count: 2, evidence_student_count: 2, exam_student_count: 2, exam_score_rate: .6, topic_count: 2, skill_count: 2, weak_topic_count: 1, weak_skill_count: 2 },
    students: students.map(s => ({ student_id: String(s.id), student_name: s.name, student_code: s.student_code, class_id: s.class_name, score_rate: .6, score_rate_source: 'current_exam', topics: { weak: 1, unsteady: 0, stable: 0, insufficient: 0, evidence: 1 }, skills: { weak: 2, unsteady: 0, stable: 0, insufficient: 0, evidence: 2 } })),
    nodes: [node('ch', 'chapter', '测试章'), node('sec', 'section', '测试节'), node('topic', 'topic', '勾股关系'),
      node('topic2', 'topic', '不相关知识点'), node('skill', 'skill', '技能·列等式'), node('skill2', 'skill', '技能·跨节推导', 'sec2'),
      { ...node('old', 'topic', '旧知识'), in_volume: false, display_name: '往届册｜往届章｜往届节｜旧知识' }],
    associations: [{ topic_key: 'topic', skill_key: 'skill', question_count: 12, same_part_question_count: 10, basis: 'same_part' },
      { topic_key: 'topic', skill_key: 'skill2', question_count: 3, same_part_question_count: 0, basis: 'question_cooccurrence' }] }
}
function diagnosis(body: { scope: { student_ids: string[] } }) {
  const data = overview()
  return { ...data, students: data.students.filter(s => body.scope.student_ids.includes(s.student_id)).map(s => ({ ...s, weak_points: [] })),
    group_weak_points: [], knowledge_catalog: data.nodes.map(n => ({ knowledge_key: n.knowledge_key, knowledge_point: n.display_name, parent_knowledge_key: n.kind === 'chapter' ? null : n.kind === 'section' ? 'ch' : n.section_key, node_kind: n.kind })),
    knowledge_associations: data.associations, coverage: { covered_items: 0, total_items: 0, missing_items: {} }, confirmed_concept_ids: [], suggested_terms: [], unmapped_terms: [], diagnosis_identity: 'question_tag' }
}
async function install(page: Page) {
  page.on('pageerror', error => { throw error })
  const requests: Array<{ path: string; method: string }> = []
  await page.addInitScript(id => { localStorage.clear(); localStorage.setItem('ai-grading:curriculum-scope:v1', id) }, volumeId)
  await page.route(/^https?:\/\/[^/]+\/api\//, async route => {
    const request = route.request(), path = new URL(request.url()).pathname
    requests.push({ path, method: request.method() })
    let value: unknown
    if (path === '/api/question-bank/curriculum') value = catalog
    else if (path === '/api/students') value = { items: students, total: students.length }
    else if (path === '/api/sessions') value = { items: [], total: 0 }
    else if (path === '/api/training/overview') value = overview()
    else if (path === '/api/training/diagnosis') value = diagnosis(request.postDataJSON())
    else if (path === '/api/training/personalized-drafts') value = { items: [], total: 0 }
    else if (path.startsWith('/api/jobs')) value = { items: [], total: 0 }
    else { await route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ error: { code: 'TEST-unexpected', message: 'TEST-unexpected', request_id: 'TEST' } }) }); return }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(value) })
  })
  return requests
}
test('map groups, same-part and cooccurrence lines, drawer, keyboard focus, and shared requests', async ({ page }) => {
  const requests = await install(page)
  await page.goto('/knowledge-overview')
  await expect(page.getByRole('heading', { name: '本周建议优先处理' })).toBeVisible()
  await page.getByRole('link', { name: '知识结构', exact: true }).click()
  await expect(page.locator('.mastery-map-node[data-knowledge="topic"]')).toBeVisible()
  expect(requests.filter(r => r.path === '/api/training/overview')).toHaveLength(1)
  const source = page.locator('.mastery-map-node[data-knowledge="topic"]')
  await source.click()
  await expect(page.locator('.mastery-map-drawer')).toBeVisible()
  await expect(page.locator('.mastery-map-drawer')).toContainText('同一小问 10 题')
  await expect(page.locator('.mastery-map-drawer')).toContainText('仅同题出现 3 题（虚线）')
  await page.mouse.move(1000, 100)
  await expect(page.locator('.mastery-map-connections path')).toHaveCount(2)
  await expect(page.locator('.mastery-map-connections path.is-dashed')).toHaveCount(1)
  const crossings = await page.locator('.mastery-map-root').evaluate(root => {
    const svg = root.querySelector('svg')!.getBoundingClientRect()
    const cells = [...root.querySelectorAll<HTMLElement>('.mastery-map-node')].map(cell => ({ key: cell.dataset.knowledge!, rect: cell.getBoundingClientRect() }))
    const hits: string[] = []
    for (const path of root.querySelectorAll<SVGPathElement>('path')) {
      const length = path.getTotalLength()
      for (let step = 1; step < 200; step++) {
        const point = path.getPointAtLength(length * step / 200)
        const x = point.x + svg.left, y = point.y + svg.top
        for (const cell of cells) {
          if (x > cell.rect.left + 1 && x < cell.rect.right - 1 && y > cell.rect.top + 1 && y < cell.rect.bottom - 1) hits.push(cell.key)
        }
      }
    }
    return [...new Set(hits)]
  })
  expect(crossings).toEqual([])
  await expect(page.locator('.mastery-map-node[data-knowledge="topic2"]')).toHaveClass(/is-dimmed/)
  await page.keyboard.press('Escape')
  await expect(page.locator('.mastery-map-drawer')).toHaveCount(0)
  await expect(source).toBeFocused()
  await page.getByRole('button', { name: '只看技能', exact: true }).click()
  await expect(page.locator('.mastery-map-node.is-topic')).toHaveCount(0)
  expect(requests.filter(r => r.path.startsWith('/api/graph'))).toHaveLength(0)
})
test('narrow map stacks both columns and uses a full-width bottom drawer without lines', async ({ page }) => {
  await install(page); await page.setViewportSize({ width: 800, height: 900 }); await page.goto('/knowledge-graph')
  const source = page.locator('.mastery-map-node[data-knowledge="topic"]')
  await source.click(); await expect(page.locator('.mastery-map-drawer')).toBeVisible()
  expect(await page.locator('.mastery-map-columns').first().evaluate(el => getComputedStyle(el).gridTemplateColumns.split(' ').length)).toBe(1)
  const box = await page.locator('.mastery-map-drawer').boundingBox()
  expect(box?.width).toBeCloseTo(800, 1); await expect(page.locator('.mastery-map-connections')).toHaveCount(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})
test('overview opens existing student training with presets and sends no create request', async ({ page }) => {
  const requests = await install(page); await page.goto('/knowledge-overview')
  await page.locator('.overview-priority').first().getByRole('button', { name: /给这 1 人出训练卷/ }).click()
  await expect(page).toHaveURL(/\/training\?mode=student$/)
  await expect(page.getByRole('heading', { name: '按学生训练', exact: true })).toBeVisible()
  const selection = await page.evaluate(() => ({ scope: JSON.parse(localStorage.getItem('p4-evidence-scope-v1')!), paper: JSON.parse(localStorage.getItem('ai-grading:personalized-paper-selection:v1')!) }))
  expect(selection.scope.scope.student_ids).toEqual(['1'])
  expect(selection.paper).toMatchObject({ targetKeys: ['skill'], rangeKeys: ['sec'], scopeMode: 'focused', paperMode: 'individual' })
  expect(requests.filter(r => r.method === 'POST' && !['/api/training/overview', '/api/training/diagnosis'].includes(r.path))).toHaveLength(0)
})
