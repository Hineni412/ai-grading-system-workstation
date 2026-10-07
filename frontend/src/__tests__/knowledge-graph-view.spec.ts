import { createApp, nextTick, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchStudents } from '../api/students'
import { trainingApi, type TrainingOverview, type TrainingOverviewNode } from '../api/training'
import { createAppRouter } from '../router'
import { useCurriculumScopeStore } from '../stores/curriculum-scope'
import { useMasteryOverviewStore } from '../stores/mastery-overview'
import { heatLevel, overviewMetrics, relatedNodes, weakRate } from '../components/knowledge-overview/metrics'
import KnowledgeGraphView from '../views/KnowledgeGraphView.vue'
vi.mock('../api/students', async importOriginal => ({ ...await importOriginal<typeof import('../api/students')>(), fetchStudents: vi.fn() }))
vi.mock('../api/training', async importOriginal => ({ ...await importOriginal<typeof import('../api/training')>(), trainingApi: { overview: vi.fn() } }))
vi.mock('../api/assembly', async importOriginal => ({ ...await importOriginal<typeof import('../api/assembly')>(),
  assemblyApi: { resolveQuestions: vi.fn(async (ids: number[]) => ({ items: ids.map(id => ({ id, revision: 'r1', question_number: '3', question_type: '解答题',
    question_text: `题库题 ${id} 的题干`, answer_text: null, difficulty: '3', paper_title: null, tags: [], asset_urls: [], score_value: null,
    rich_content: { available: true, question_block_count: 1, answer_block_count: 0,
      question_blocks: [{ kind: 'paragraph' as const, text: `题型典型题干 ${id}`, asset_urls: [] }], answer_blocks: [] } })), missing_question_ids: [] })) } }))
function node(overrides: Partial<TrainingOverviewNode> = {}): TrainingOverviewNode {
  return { knowledge_key: 'topic', display_name: '册｜第一章｜第一节｜用勾股定理求边长', kind: 'topic',
    chapter_key: 'ch', section_key: 'sec', in_volume: true, definition: '直角三角形三边的关系。',
    group_mastery: .5, group_interval_low: .3, group_interval_high: .7, evidence_student_count: 2,
    distribution: { weak: 1, unsteady: 0, stable: 1, insufficient: 0 },
    students: [{ student_id: '1', mastery: .2, tier: 'weak' }, { student_id: '2', mastery: .8, tier: 'stable' }], ...overrides }
}
function fixture(): TrainingOverview {
  return { scope: { mode: 'all', student_ids: ['1', '2'] },
    exam_scope: { mode: 'semester', session_ids: [7], sessions: [{ session_id: 7, session_name: 'TEST考试' }], curriculum_volume_id: 'bnu24-math-g8-upper' }, warnings: [],
    summary: { student_count: 2, evidence_student_count: 2, exam_student_count: 2, exam_score_rate: .6, topic_count: 2, skill_count: 2, weak_topic_count: 1, weak_skill_count: 2 },
    students: [{ student_id: '1', student_code: 'S1', student_name: '测试甲', class_id: '一班', score_rate: .4, score_rate_source: 'current_exam', topics: { weak: 1, stable: 0, unsteady: 0, insufficient: 0, evidence: 1 }, skills: { weak: 2, stable: 0, unsteady: 0, insufficient: 0, evidence: 2 } }],
    nodes: [node({ knowledge_key: 'ch', kind: 'chapter', display_name: '册｜第一章' }),
      node({ knowledge_key: 'sec', kind: 'section', display_name: '册｜第一章｜第一节' }), node(),
      node({ knowledge_key: 'skill', kind: 'skill', display_name: '册｜第一章｜第一节｜技能·列勾股等式' }),
      node({ knowledge_key: 'skill2', kind: 'skill', section_key: 'sec2', display_name: '册｜第一章｜第二节｜技能·推导关系' }),
      node({ knowledge_key: 'unrelated', display_name: '册｜第一章｜第一节｜无关联知识', distribution: { weak: 0, unsteady: 0, stable: 2, insufficient: 0 } }),
      node({ knowledge_key: 'old', in_volume: false, chapter_key: 'old-ch', section_key: 'old-sec', display_name: '往届册｜往届章｜往届节｜旧知识' })],
    associations: [{ topic_key: 'topic', skill_key: 'skill', question_count: 12, same_part_question_count: 10, basis: 'same_part' },
      { topic_key: 'topic', skill_key: 'skill2', question_count: 3, same_part_question_count: 0, basis: 'question_cooccurrence' },
      { topic_key: 'old', skill_key: 'skill2', question_count: 1, same_part_question_count: 0, basis: 'question_cooccurrence' }] }
}
function typeFixture(): TrainingOverview {
  const base = fixture()
  const typeNode = (overrides: Partial<TrainingOverviewNode>): TrainingOverviewNode => node({
    kind: 'type', ...overrides })
  return { ...base, target_kind: 'type', associations: [],
    summary: { ...base.summary, topic_count: 0, skill_count: 0, weak_topic_count: 0, weak_skill_count: 0,
      type_count: 3, weak_type_count: 2 },
    nodes: [node({ knowledge_key: 'ch', kind: 'chapter', display_name: '册｜第一章' }),
      node({ knowledge_key: 'sec', kind: 'section', display_name: '册｜第一章｜第一节' }),
      typeNode({ knowledge_key: 'type-weak', display_name: '册｜第一章｜第一节｜题型·构造直角求边',
        definition: '构造直角三角形后用勾股定理求边。',
        typical_question: { bank_question_id: 42, session_name: '第三周学情反馈', question_label: '8', class_rate: .35 },
        other_questions: [{ session_name: '期中考试', question_label: '12', class_rate: .5 }] }),
      typeNode({ knowledge_key: 'type-ok', display_name: '册｜第一章｜第一节｜题型·面积拼图推理',
        distribution: { weak: 0, unsteady: 0, stable: 2, insufficient: 0 } }),
      typeNode({ knowledge_key: 'type-hidden', display_name: '册｜第一章｜第一节｜题型·未考查的题型',
        evidence_student_count: 0, distribution: { weak: 0, unsteady: 0, stable: 0, insufficient: 0 },
        students: [] })] }
}
const mounted: App[] = []
async function settle() { await nextTick(); await Promise.resolve(); await nextTick() }
async function mountView(target = '/knowledge-graph') {
  const pinia = createPinia(); setActivePinia(pinia)
  useCurriculumScopeStore().$patch({ loadState: 'ready', selectedVolumeId: 'bnu24-math-g8-upper' })
  const router = createAppRouter(createMemoryHistory()); await router.push(target); await router.isReady()
  const host = document.createElement('div'); document.body.append(host)
  const app = createApp(KnowledgeGraphView); app.use(pinia); app.use(router); app.mount(host); mounted.push(app)
  await vi.waitFor(() => expect(host.querySelector('.mastery-map-root')).not.toBeNull()); await settle()
  return { host, router }
}
function button(host: HTMLElement, key: string) { return host.querySelector<HTMLButtonElement>(`.mastery-map-node[data-knowledge="${key}"]`)! }
beforeEach(() => {
  vi.clearAllMocks(); localStorage.clear()
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  vi.mocked(fetchStudents).mockResolvedValue([{ id: 1, student_code: 'S1', name: '测试甲', class_name: '一班', created_at: null }])
  vi.mocked(trainingApi.overview).mockImplementation(async () => fixture())
})
afterEach(() => { for (const app of mounted.splice(0)) app.unmount(); document.body.innerHTML = ''; vi.unstubAllGlobals() })
describe('knowledge mastery map', () => {
  it('uses overview, ignores old scope links, and excludes previous volumes from the header', async () => {
    const { host, router } = await mountView('/knowledge-graph?session=7&class=old')
    expect(trainingApi.overview).toHaveBeenCalledWith({ scope: { mode: 'all', student_ids: [], use_historical_fallback: false }, exam_scope: { mode: 'semester', curriculum_volume_id: 'bnu24-math-g8-upper', session_ids: [] } }, expect.any(AbortSignal))
    const metrics = overviewMetrics(fixture())
    expect(host.querySelector('.mastery-map-summary')?.textContent).toContain(`本册 ${metrics.total} 项 · 有证据 ${metrics.evidence} 项 · 有学生明显薄弱 ${metrics.weak} 项`)
    expect(router.currentRoute.value.query).toEqual({})
    expect(host.textContent).toContain('往届内容（本学期考试涉及 · 1 项）')
    expect(host.querySelector('.mastery-map-column[data-kind="topic"]')?.textContent).toContain('知识点 · 学什么')
    expect(host.querySelector('.mastery-map-column[data-kind="skill"]')?.textContent).toContain('技能 · 会做什么')
    expect(host.textContent).not.toContain('颜色模式')
  })
  it.each([[0, 10, 0], [1, 10, 1], [101, 1000, 2], [4, 10, 4], [401, 1000, 5], [0, 0, -1]])('heat boundary weak=%i evidence=%i maps to %i', (weak, evidence, expected) => {
    const item = node({ evidence_student_count: evidence, distribution: { weak, unsteady: 0, stable: evidence - weak, insufficient: 0 } })
    expect(heatLevel(item)).toBe(expected)
    expect(weakRate(item)).toBe(evidence ? weak / evidence : null)
  })
  it('filters node types and weakness without changing metrics', async () => {
    const { host, router } = await mountView()
    await router.replace('/knowledge-graph?filter=skill'); await settle()
    expect(host.querySelectorAll('.mastery-map-node.is-topic')).toHaveLength(0)
    expect(host.querySelectorAll('.mastery-map-node.is-skill')).toHaveLength(2)
    await router.replace('/knowledge-graph?filter=weak'); await settle()
    expect(button(host, 'unrelated')).toBeNull()
    expect(host.querySelector('.mastery-map-summary')?.textContent).toContain('有学生明显薄弱 3 项')
  })
  it('opens details, highlights only associations, and restores the cell focus on Escape', async () => {
    const { host } = await mountView()
    const cell = button(host, 'topic'); cell.focus(); cell.click()
    await vi.waitFor(() => expect(host.querySelector('.mastery-map-drawer')).not.toBeNull()); await settle()
    const drawer = host.querySelector('.mastery-map-drawer')!
    expect(document.activeElement).toBe(drawer)
    expect(button(host, 'unrelated').classList.contains('is-dimmed')).toBe(true)
    expect(button(host, 'skill').classList.contains('is-related')).toBe(true)
    expect(drawer.textContent).toContain('直角三角形三边的关系。')
    expect(drawer.textContent).toContain('80% 群体区间 30%–70%')
    expect(drawer.textContent).toContain('同一小问 10 题')
    expect(drawer.textContent).toContain('仅同题出现 3 题（虚线）')
    expect(drawer.textContent).not.toContain('出训练卷')
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await vi.waitFor(() => expect(host.querySelector('.mastery-map-drawer')).toBeNull())
    await settle(); expect(document.activeElement).toBe(cell)
  })
  it('keeps hover highlights independent of backend mastery and orders same-part associations first', async () => {
    const { host } = await mountView()
    button(host, 'topic').dispatchEvent(new MouseEvent('mouseenter'))
    await settle(); expect(button(host, 'unrelated').classList.contains('is-dimmed')).toBe(true)
    button(host, 'topic').dispatchEvent(new MouseEvent('mouseleave'))
    await settle(); expect(button(host, 'unrelated').classList.contains('is-dimmed')).toBe(false)
    expect(relatedNodes(fixture(), 'topic').map(r => r.node.knowledge_key)).toEqual(['skill', 'skill2'])
  })
  it('renders a single type column, hides unexamined types, and shows the typical question without students or links', async () => {
    vi.mocked(trainingApi.overview).mockImplementation(async () => typeFixture())
    const { host } = await mountView()
    expect(host.querySelector('.mastery-map-column[data-kind="type"]')?.textContent).toContain('题型 · 考什么')
    expect(host.querySelector('.mastery-map-column[data-kind="topic"]')).toBeNull()
    expect(host.querySelector('.mastery-map-column[data-kind="skill"]')).toBeNull()
    expect(button(host, 'type-hidden')).toBeNull()
    expect(host.textContent).toContain('尚未考查的 1 个题型已隐藏')
    expect(host.querySelector('.mastery-map-connections')).toBeNull()
    const cell = button(host, 'type-weak'); cell.click()
    await vi.waitFor(() => expect(host.querySelector('.mastery-map-drawer')).not.toBeNull()); await settle()
    const drawer = host.querySelector('.mastery-map-drawer')!
    expect(drawer.textContent).toContain('题型')
    expect(drawer.textContent).toContain('构造直角三角形后用勾股定理求边。')
    expect(drawer.textContent).toContain('典型题：本班考过的这类题中得分率最低的一道 · 第三周学情反馈 第8题 · 本班得分率 35%')
    expect(drawer.textContent).toContain('题型典型题干 42')
    expect(drawer.textContent).toContain('本班还考过这类题：')
    expect(drawer.textContent).toContain('期中考试 第12题 · 得分率 50%')
    expect(drawer.textContent).toContain('给明显薄弱的 1 人出训练卷')
    expect(drawer.textContent).not.toContain('相关知识点')
    expect(drawer.querySelector('.student-tier-chips, [class*="tier-chip"]')).toBeNull()
  })
  it('retains cached results when moving from overview to map', async () => {
    const pinia = createPinia(); setActivePinia(pinia)
    await useMasteryOverviewStore().load('bnu24-math-g8-upper')
    await useMasteryOverviewStore().load('bnu24-math-g8-upper')
    expect(trainingApi.overview).toHaveBeenCalledTimes(1)
  })
})
