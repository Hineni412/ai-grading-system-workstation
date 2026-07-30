import { createApp, nextTick, type App } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  PersonalizedRecommendationDraft,
  TrainingDiagnosis,
} from '../api/training'
import PersonalizedRecommendationDraftView from '../components/training/PersonalizedRecommendationDraft.vue'

const trainingApiMock = vi.hoisted(() => ({
  createPersonalizedDraft: vi.fn(),
  editPersonalizedDraft: vi.fn(),
}))

vi.mock('../api/training', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/training')>(),
  trainingApi: trainingApiMock,
}))

const diagnosis = {
  scope: { mode: 'student', student_ids: ['SYN-S01'] },
  exam_scope: {
    mode: 'current',
    session_ids: [7],
    sessions: [{ session_id: 7, session_name: '合成考试' }],
  },
  students: [{
    student_id: 'SYN-S01',
    student_code: 'S01',
    student_name: '合成学生',
    class_id: 'SYN-C01',
    score_rate: 45,
    weak_points: [{
      knowledge_key: 'knowledge_point:一元一次方程',
      knowledge_point: '一元一次方程',
      mastery: 0.45,
      score_sum: 9,
      full_score_sum: 20,
      deduction_count: 1,
      evidence_count: 1,
      exam_count: 1,
      source_question_refs: [],
      actionable_reasons: ['方程变形需要巩固'],
      tag_context: {},
      error_counts: {},
    }],
  }],
  coverage: {
    covered_items: 1,
    total_items: 1,
    missing_items: {},
  },
  confirmed_concept_ids: [],
  suggested_terms: [],
  unmapped_terms: [],
  warnings: [],
  diagnosis_identity: 'question_tag',
} satisfies TrainingDiagnosis

const draft = {
  draft_id: 'd'.repeat(64),
  status: 'draft',
  revision: 1,
  result_version: 'a'.repeat(64),
  engine_version: 'personalized-recommendation-v1',
  source_version: 'b'.repeat(64),
  config: {},
  students: [{
    student_id: 'SYN-S01',
    student_code: 'S01',
    student_name: '合成学生',
    class_id: 'SYN-C01',
    selection_mode: 'mastery_targeted',
    targets: [],
    items: [{
      item_id: 'item-1',
      item_order: 1,
      slot: 1,
      question_id: 31,
      question_number: '3',
      stage: 'direct',
      target: {},
      matched_key: 'kp_alg_linear_equation',
      matched_name: '一元一次方程',
      relation: null,
      criterion_version_id: 'c'.repeat(64),
      criterion_point_count: 3,
      difficulty: 5,
      estimated_minutes: 6,
      source_paper: '合成题源',
      reason: '直接巩固一元一次方程。',
      locked: false,
      replacement_history: [],
    }],
    shortages: [],
    warnings: [],
    estimated_minutes: 6,
  }],
  warnings: [],
  history: [],
} satisfies PersonalizedRecommendationDraft

const mounted: App[] = []

async function settle(): Promise<void> {
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

beforeEach(() => {
  vi.clearAllMocks()
  trainingApiMock.createPersonalizedDraft.mockResolvedValue(draft)
  trainingApiMock.editPersonalizedDraft.mockResolvedValue({
    ...draft,
    revision: 2,
    students: [{
      ...draft.students[0]!,
      items: [{ ...draft.students[0]!.items[0]!, locked: true }],
    }],
  })
})

afterEach(() => {
  mounted.splice(0).forEach((app) => app.unmount())
  document.body.innerHTML = ''
})

describe('personalized recommendation draft', () => {
  it('lets the teacher select a target, generate, explain and lock an item', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(PersonalizedRecommendationDraftView, {
      diagnosis,
      scope: { mode: 'student', student_ids: ['SYN-S01'] },
      examScope: { mode: 'current', session_ids: [7] },
      questionCount: 8,
      stageRatios: {
        direct: 0.6,
        prerequisite: 0.3,
        transfer: 0.1,
      },
      excludeCurrentExamOriginals: true,
    })
    app.mount(host)
    mounted.push(app)
    await settle()

    const generate = host.querySelector<HTMLButtonElement>(
      '[data-testid="generate-personalized-draft"]',
    )
    expect(generate?.disabled).toBe(false)
    generate?.click()
    await settle()

    expect(trainingApiMock.createPersonalizedDraft).toHaveBeenCalledWith(
      expect.objectContaining({
        target_names: ['一元一次方程'],
        question_count: 8,
      }),
    )
    expect(host.textContent).toContain('直接巩固一元一次方程')
    expect(host.textContent).toContain('3 个已批准判定点')
    expect(host.textContent).toContain('尚未形成正式训练卷')

    const lock = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.trim() === '锁定')
    lock?.click()
    await settle()

    expect(trainingApiMock.editPersonalizedDraft).toHaveBeenCalledWith(
      draft.draft_id,
      expect.objectContaining({
        expected_revision: 1,
        action: 'lock',
        student_id: 'SYN-S01',
        item_id: 'item-1',
      }),
    )
    expect(host.textContent).toContain('已锁定该题')
    expect(host.textContent).toContain('解锁')
  })
})
