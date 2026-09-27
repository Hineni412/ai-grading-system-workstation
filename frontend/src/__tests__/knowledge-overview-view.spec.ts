import { createApp, nextTick, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { fetchStudents } from '../api/students'
import { trainingApi, type TrainingOverview } from '../api/training'
import { createAppRouter } from '../router'
import { useCurriculumScopeStore } from '../stores/curriculum-scope'
import KnowledgeOverviewView from '../views/KnowledgeOverviewView.vue'

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

function distribution(weak: number, review = 0, stable = 0, missing = 0) {
  return { weak, review, stable, missing }
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
        knowledge_key: 't1', group_mastery: 0.5, evidence_student_count: 2,
        display_name: '册｜第一章｜1｜用勾股定理求边长',
        distribution: distribution(2, 0, 1, 0),
        students: [
          { student_id: '1', mastery: 0.4 },
          { student_id: '2', mastery: 0.55 },
          { student_id: '3', mastery: 0.8 },
        ],
      }),
      node({
        knowledge_key: 't2', chapter_key: 'ch1', section_key: 'sec2',
        group_mastery: 0.65, evidence_student_count: 1,
        display_name: '册｜第一章｜2｜拼图验证',
        distribution: distribution(0, 1, 0, 2),
        students: [{ student_id: '1', mastery: 0.65 }],
      }),
      node({
        knowledge_key: 'sk1', kind: 'skill', group_mastery: 0.3,
        evidence_student_count: 3,
        display_name: '技能·列勾股等式',
        distribution: distribution(3, 0, 0, 0),
        students: [
          { student_id: '1', mastery: 0.2 },
          { student_id: '2', mastery: 0.3 },
          { student_id: '3', mastery: 0.4 },
        ],
      }),
      node({
        knowledge_key: 't0', evidence_student_count: 0,
        distribution: distribution(0, 0, 0, 3),
      }),
    ],
    students: [
      {
        student_id: '1', student_code: 'S1', student_name: '学生甲', class_id: '八年级1班',
        score_rate: 0.5, score_rate_source: 'current_exam',
        topics: { weak: 1, review: 1, stable: 0, evidence: 2 },
        skills: { weak: 1, review: 0, stable: 0, evidence: 1 },
      },
      {
        student_id: '2', student_code: 'S2', student_name: '学生乙', class_id: '八年级1班',
        score_rate: 0.9, score_rate_source: 'current_exam',
        topics: { weak: 1, review: 0, stable: 0, evidence: 1 },
        skills: { weak: 1, review: 0, stable: 0, evidence: 1 },
      },
      {
        student_id: '3', student_code: 'S3', student_name: '学生丙', class_id: '八年级2班',
        score_rate: null, score_rate_source: 'none',
        topics: { weak: 0, review: 0, stable: 0, evidence: 0 },
        skills: { weak: 0, review: 0, stable: 0, evidence: 0 },
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
async function settle() { await nextTick(); await Promise.resolve(); await nextTick() }

async function mountView(volumeId: string | null = 'bnu24-math-g8-upper') {
  const pinia = createPinia()
  setActivePinia(pinia)
  useCurriculumScopeStore(pinia).$patch({ loadState: 'ready', selectedVolumeId: volumeId })
  const router = createAppRouter(createMemoryHistory())
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

})
