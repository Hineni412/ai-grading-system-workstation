import { createApp, nextTick, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { fetchStudents } from '../api/students'
import { trainingApi, type TrainingOverview } from '../api/training'
import { createAppRouter } from '../router'
import { useCurriculumScopeStore } from '../stores/curriculum-scope'
import KnowledgeOverviewView from '../views/KnowledgeOverviewView.vue'
import { overviewMetrics, chapterMetrics } from '../components/knowledge-overview/metrics'
import { useMasteryOverviewStore } from '../stores/mastery-overview'
import { presetFocusedTraining, loadPaperSelectionSession, savePaperSelectionSession } from '../features/training/paper-selection-session'

vi.mock('../api/students', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/students')>(),
  fetchStudents: vi.fn(),
}))
vi.mock('../api/training', async (importOriginal) => {
  const original = await importOriginal<typeof import('../api/training')>()
  return {
    ...original,
    trainingApi: { ...original.trainingApi, overview: vi.fn() },
  }
})

const roster = [
  { id: 1, student_code: 'S1', name: '学生甲', class_name: '八年级1班', created_at: null },
  { id: 2, student_code: 'S2', name: '学生乙', class_name: '八年级1班', created_at: null },
  { id: 3, student_code: 'S3', name: '学生丙', class_name: '八年级2班', created_at: null },
  { id: 4, student_code: 'S4', name: '学生丁', class_name: '10', created_at: null },
]

function distribution(weak: number, unsteady = 0, stable = 0, insufficient = 0) {
  return { weak, unsteady, stable, insufficient }
}

function node(partial: Partial<TrainingOverview['nodes'][number]> & { knowledge_key: string }) {
  return {
    display_name: `册｜章｜节｜${partial.knowledge_key}`,
    kind: 'topic' as const,
    chapter_key: 'ch1',
    section_key: 'sec1',
    group_mastery: null,
    evidence_student_count: 0,
    distribution: distribution(0, 0, 0, 3),
    students: [],
    ...partial,
  }
}

function overviewFixture(): TrainingOverview {
  return {
    scope: { mode: 'all', student_ids: ['1', '2', '3'] },
    exam_scope: {
      mode: 'semester',
      curriculum_volume_id: 'bnu24-math-g8-upper',
      session_ids: [7],
      sessions: [{ session_id: 7, session_name: '匿名考试' }],
    },
    warnings: [],
    associations: [{ topic_key: 't1', skill_key: 'sk1', question_count: 4, same_part_question_count: 3, basis: 'same_part' }],
    nodes: [
      node({
        knowledge_key: 'ch1', kind: 'chapter', section_key: '',
        display_name: '册｜第一章 勾股定理',
        distribution: distribution(2, 1, 0, 0),
      }),
      node({
        knowledge_key: 'sec1', kind: 'section', section_key: 'sec1',
        display_name: '册｜第一章｜1 探索勾股定理',
        distribution: distribution(1, 1, 1, 0),
      }),
      node({
        knowledge_key: 'sec2', kind: 'section', section_key: 'sec2',
        display_name: '册｜第一章｜2 验证勾股定理',
        distribution: distribution(1, 0, 0, 2),
      }),
      node({
        knowledge_key: 't1', group_mastery: 0.5, evidence_student_count: 3, tier: 'weak',
        display_name: '册｜第一章｜1｜用勾股定理求边长',
        distribution: distribution(2, 0, 1, 0),
        students: [
          { student_id: '1', mastery: 0.4, tier: 'weak' },
          { student_id: '2', mastery: 0.55, tier: 'weak' },
          { student_id: '3', mastery: 0.8, tier: 'stable', interval_low: .75, interval_high: .9,
            observation_count: 10, full_correct_count: 8 },
        ],
      }),
      node({
        knowledge_key: 't2', chapter_key: 'ch1', section_key: 'sec2',
        group_mastery: 0.65, evidence_student_count: 1,
        display_name: '册｜第一章｜2｜拼图验证',
        distribution: distribution(0, 0, 0, 1),
        students: [{ student_id: '1', mastery: 0.65, tier: "insufficient" }],
      }),
      node({
        knowledge_key: 'sk1', kind: 'skill', group_mastery: 0.3,
        evidence_student_count: 3,
        display_name: '技能·列勾股等式',
        distribution: distribution(1, 1, 1, 0),
        students: [
          { student_id: '1', mastery: 0.2, tier: 'weak' },
          { student_id: '2', mastery: 0.65, tier: 'unsteady' },
          { student_id: '3', mastery: 0.8, tier: 'stable', interval_low: .75, interval_high: .9, observation_count: 10, full_correct_count: 8 },
        ],
      }),
      node({
        knowledge_key: 't0', evidence_student_count: 0,
        distribution: distribution(0, 0, 0, 0),
      }),
    ],
    students: [
      {
        student_id: '1', student_code: 'S1', student_name: '学生甲', class_id: '八年级1班',
        score_rate: 0.5, score_rate_source: 'current_exam',
        topics: { weak: 1, unsteady: 1, stable: 0, insufficient: 0, evidence: 2 },
        skills: { weak: 1, unsteady: 0, stable: 0, insufficient: 0, evidence: 1 },
      },
      {
        student_id: '2', student_code: 'S2', student_name: '学生乙', class_id: '八年级1班',
        score_rate: 0.9, score_rate_source: 'current_exam',
        topics: { weak: 1, unsteady: 0, stable: 0, insufficient: 0, evidence: 1 },
        skills: { weak: 1, unsteady: 0, stable: 0, insufficient: 0, evidence: 1 },
      },
      {
        student_id: '3', student_code: 'S3', student_name: '学生丙', class_id: '八年级2班',
        score_rate: null, score_rate_source: 'none',
        topics: { weak: 0, unsteady: 0, stable: 0, insufficient: 0, evidence: 0 },
        skills: { weak: 0, unsteady: 0, stable: 0, insufficient: 0, evidence: 0 },
      },
    ],
    summary: {
      student_count: 3,
      evidence_student_count: 3,
      exam_student_count: 2,
      exam_score_rate: 0.7,
      topic_count: 3,
      skill_count: 1,
      weak_topic_count: 1,
      weak_skill_count: 1,
    },
  }
}

const mounted: App[] = []
let activeRouter: ReturnType<typeof createAppRouter>
async function settle() { await nextTick(); await Promise.resolve(); await nextTick() }

async function mountView(volumeId: string | null = 'bnu24-math-g8-upper') {
  const pinia = createPinia()
  setActivePinia(pinia)
  useCurriculumScopeStore(pinia).$patch({ loadState: 'ready', selectedVolumeId: volumeId })
  const router = createAppRouter(createMemoryHistory())
  activeRouter = router
  await router.push('/knowledge-overview')
  await router.isReady()
  const host = document.createElement('div')
  document.body.append(host)
  const app = createApp(KnowledgeOverviewView)
  app.use(pinia)
  app.use(router)
  app.mount(host)
  mounted.push(app)
  await settle()
  return host
}

beforeEach(() => {
  vi.clearAllMocks()
  globalThis.localStorage?.clear()
  vi.mocked(fetchStudents).mockResolvedValue(roster)
  vi.mocked(trainingApi.overview).mockImplementation(async () => overviewFixture())
})

afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  document.body.innerHTML = ''
})

describe('knowledge overview view', () => {

  it('opens the backend tier list with interval and observation details', async () => {
    const host = await mountView()
    await vi.waitFor(() => expect(trainingApi.overview).toHaveBeenCalledTimes(1))
    await settle()
    const point = host.querySelector<HTMLDetailsElement>('.overview-layer-list')!
    point.open = true
    await settle()
    expect(host.textContent).toContain('明显薄弱（1 人）')
    expect(host.textContent).toContain('较稳定（1 人）')
    const stable = [...host.querySelectorAll<HTMLButtonElement>('.student-tier-chip')].find(button => button.title.includes('掌握度 80%'))!
    expect(stable.title).toContain('掌握度 80%（75%–90%）')
    expect(stable.title).toContain('作答 10 处、全对 8 处')
  })

  it('switches scope to a class and saves the shared evidence scope', async () => {
    const host = await mountView()
    await vi.waitFor(() => expect(trainingApi.overview).toHaveBeenCalledTimes(1))
    const classButton = [...host.querySelectorAll('.knowledge-overview-scope-bar button')]
      .find(button => button.textContent === '八年级2班') as HTMLButtonElement
    classButton.click()
    await vi.waitFor(() => expect(trainingApi.overview).toHaveBeenCalledTimes(2))
    expect(trainingApi.overview).toHaveBeenLastCalledWith(
      {
        scope: {
          mode: 'class',
          class_id: '八年级2班',
          class_ids: ['八年级2班'],
          student_ids: [],
          use_historical_fallback: false,
        },
        exam_scope: {
          mode: 'semester',
          curriculum_volume_id: 'bnu24-math-g8-upper',
          session_ids: [],
        },
      },
      expect.any(AbortSignal),
    )
    const saved = JSON.parse(globalThis.localStorage!.getItem('p4-evidence-scope-v1')!)
    expect(saved.scope.mode).toBe('class')
    expect(saved.scope.class_ids).toEqual(['八年级2班'])
  })

  it('shows the semester prompt and sends no request without a volume', async () => {
    const host = await mountView(null)
    await settle()
    expect(host.textContent).toContain('请先在顶部选择教学学期')
    expect(trainingApi.overview).not.toHaveBeenCalled()
  })

  it('shows metrics with denominators, related topics, and chapter totals', async () => {
    const host = await mountView()
    await vi.waitFor(() => expect(host.querySelector('.overview-priority')).not.toBeNull())
    expect(host.querySelector('.overview-summary-strip')?.textContent).toContain('有证据学生3 / 3 人')
    expect(host.querySelector('.overview-summary-strip')?.textContent).toContain('有成绩 2 人')
    expect(host.querySelector('.overview-summary-strip')?.textContent).toContain('2人 / 有证据 3 人')
    expect(host.querySelector('.overview-related')?.textContent).toContain('用勾股定理求边长')
    const data = overviewFixture()
    expect(chapterMetrics(data.nodes).reduce((sum, chapter) => sum + chapter.weak, 0)).toBe(overviewMetrics(data).weak)
    expect(overviewMetrics(data).weak).toBe(data.summary.weak_topic_count + data.summary.weak_skill_count)
  })

  it('presets only weak students, the skill and section, and enters student training without generating', async () => {
    const host = await mountView()
    await vi.waitFor(() => expect(host.querySelector('.overview-priority')).not.toBeNull())
    const button = [...host.querySelectorAll<HTMLButtonElement>('.overview-priority-actions button')].find(b => b.textContent?.includes('出训练卷'))!
    button.click()
    await vi.waitFor(() => expect(activeRouter.currentRoute.value.fullPath).toBe('/training?mode=student'), { timeout: 8000 })
    expect(JSON.parse(localStorage.getItem('p4-evidence-scope-v1')!).scope).toMatchObject({ mode: 'selected', student_ids: ['1'] })
    expect(loadPaperSelectionSession()).toMatchObject({ targetKeys: ['sk1'], rangeKeys: ['sec1'], scopeMode: 'focused',
      paperMode: 'individual', questionCount: 10, difficultyMax: 8, adoptedGroup: null, groupEditor: null })
  })

  it('opens group wrong questions with the exact node and return origin', async () => {
    const host = await mountView()
    await vi.waitFor(() => expect(host.querySelector('.overview-priority')).not.toBeNull())
    const button = [...host.querySelectorAll<HTMLButtonElement>('.overview-priority-actions button')].find(b => b.textContent === '看错题')!
    button.click()
    await vi.waitFor(() => expect(activeRouter.currentRoute.value.name).toBe('student-evidence'))
    expect(activeRouter.currentRoute.value.params.studentId).toBe('group')
    expect(activeRouter.currentRoute.value.query).toEqual({ mode: 'questions', knowledge: 'sk1', klabel: '技能·列勾股等式', from: 'overview' })
  })

  it('keeps existing numeric settings and clears group adoption in focused presets', () => {
    savePaperSelectionSession({ targetKeys: ['old'], rangeKeys: ['old-range'], questionCount: 12,
      difficultyMax: 6, paperMode: 'shared', excludeCurrentOriginals: true,
      adoptedGroup: { groupId: 'g', memberIds: ['1'], targetKeys: ['old'], scopeKeys: ['old-range'], sourceVersion: 'v' },
      groupEditor: { memberIds: ['1'], targetKeys: ['old'], scopeKeys: ['old-range'] } })
    presetFocusedTraining({ targetKeys: ['sk1'], rangeKeys: [] })
    expect(loadPaperSelectionSession()).toMatchObject({ targetKeys: ['sk1'], rangeKeys: [], questionCount: 12,
      difficultyMax: 6, scopeMode: 'focused', paperMode: 'individual', adoptedGroup: null, groupEditor: null })
  })

  it('shares the ready response, restores only a single class, and resets selected students on return', async () => {
    const host = await mountView()
    await vi.waitFor(() => expect(host.querySelector('.overview-priority')).not.toBeNull())
    const store = useMasteryOverviewStore()
    await store.load('bnu24-math-g8-upper')
    expect(trainingApi.overview).toHaveBeenCalledTimes(1)
    localStorage.setItem('p4-evidence-scope-v1', JSON.stringify({ scope: { mode: 'selected', student_ids: ['1'] }, exam_scope: { mode: 'semester', curriculum_volume_id: 'bnu24-math-g8-upper' } }))
    store.activate('bnu24-math-g8-upper')
    expect(store.scopeSelection).toBe('all')
    const restoredScope = JSON.parse(localStorage.getItem('p4-evidence-scope-v1')!).scope
    expect(restoredScope.mode).toBe('all')
    expect(restoredScope.student_ids).toBeUndefined()
    expect(trainingApi.overview).toHaveBeenCalledTimes(1)
  })

  it('shows retained results on a failed retry and replaces them when scope changes', async () => {
    const host = await mountView()
    await vi.waitFor(() => expect(host.querySelector('.overview-priority')).not.toBeNull())
    vi.mocked(trainingApi.overview).mockRejectedValue(new Error('TEST-unavailable'))
    const store = useMasteryOverviewStore()
    await store.load('bnu24-math-g8-upper', true)
    await settle()
    expect(store.loadState).toBe('stale-error')
    expect(host.textContent).toContain('当前显示上次成功读取的结果')
    store.selectScope('八年级2班', 'bnu24-math-g8-upper')
    await vi.waitFor(() => expect(store.loadState).toBe('error'))
    expect(store.overview).toBeNull()
  })

  it('sorts the first five skills and keeps prior-volume weaknesses out of priorities', async () => {
    const data = overviewFixture()
    data.nodes.push(...Array.from({ length: 6 }, (_, i) => node({ knowledge_key: `sk${i + 2}`, kind: 'skill', distribution: distribution(i + 2, 0, 0, 0) })),
      node({ knowledge_key: 'old-skill', kind: 'skill', in_volume: false, distribution: distribution(99, 0, 0, 0) }))
    vi.mocked(trainingApi.overview).mockResolvedValue(data)
    const host = await mountView()
    await vi.waitFor(() => expect(host.querySelectorAll('.overview-priority')).toHaveLength(5))
    expect([...host.querySelectorAll<HTMLElement>('.overview-priority')].map(el => el.dataset.knowledge)).toEqual(['sk7', 'sk6', 'sk5', 'sk4', 'sk3'])
  })

})
