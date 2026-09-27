import { createApp, nextTick, type App } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  TrainingAssessmentOutcome,
  TrainingFeedback,
  TrainingSubmission,
} from '../api/training'
import TrainingAssessmentPanel from '../components/training/TrainingAssessmentPanel.vue'
import { ApiError } from '../api/errors'

const trainingApiMock = vi.hoisted(() => ({
  getTrainingAssessment: vi.fn(),
  getTrainingFeedback: vi.fn(),
  startTrainingAssessment: vi.fn(),
  reviewTrainingPoint: vi.fn(),
  controlTrainingAssessment: vi.fn(),
  syncTrainingEvidence: vi.fn(),
  replayTrainingEvidence: vi.fn(),
}))

vi.mock('../api/training', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/training')>(),
  trainingApi: trainingApiMock,
}))

const submission = {
  submission_id: 'c'.repeat(64),
  paper_instance_id: 'a'.repeat(64),
  student_id: 'SYN-S01',
  student_code: 'S01',
  student_name: '合成学生',
  class_id: 'SYN-C01',
  series_version: 1,
  status: 'ready',
  revision: 1,
  expected_total_pages: 2,
  missing_pages: [],
  issue_codes: [],
  assessment_started: false,
} satisfies TrainingSubmission

const pendingAssessment = {
  run_id: 'a'.repeat(64),
  submission_id: submission.submission_id,
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

const completedAssessment = {
  ...pendingAssessment,
  workflow_status: 'complete',
  review_revision: 2,
  questions: [{
    ...pendingAssessment.questions[0]!,
    status: 'complete',
    met_count: 1,
    not_met_count: 1,
    uncertain_count: 0,
    review_status: 'completed',
    review_points: [
      pendingAssessment.questions[0]!.review_points[0]!,
      {
        ...pendingAssessment.questions[0]!.review_points[1]!,
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

const feedback = {
  schema_version: 'training-feedback-v1',
  feedback_id: 'f'.repeat(64),
  submission_id: submission.submission_id,
  submission_revision: 1,
  source_review_revision: 2,
  status: 'complete',
  student: { student_id: submission.student_id },
  summary: {
    published_question_count: 1,
    ready_question_count: 1,
    total_question_count: 1,
    pending_outbox_count: 0,
    message: '本次 1 道题均已形成逐题训练证据。',
  },
  questions: [],
  mastery_changes: [{
    stable_key: 'kp_alg_linear_equation',
    display_name: '一元一次方程',
    mastery_before: { value: 0.4 },
    mastery_after: { value: 0.55 },
    reason: '按每题覆盖比例重算。',
  }],
  next_round: {
    status: 'draft',
    draft_id: 'd'.repeat(64),
    message: '下一轮草稿等待教师确认。',
    changes: [],
  },
  timeline: [],
  safety: {
    is_exam_score: false,
    changes_exam_score: false,
    auto_paper_created: false,
    auto_printed: false,
  },
  evidence_version: 'e'.repeat(64),
} satisfies TrainingFeedback

const mounted: App[] = []

async function settle(): Promise<void> {
  await nextTick()
  await Promise.resolve()
  await nextTick()
}

beforeEach(() => {
  vi.resetAllMocks()
  trainingApiMock.getTrainingAssessment.mockResolvedValue(pendingAssessment)
  trainingApiMock.getTrainingFeedback.mockRejectedValue(new Error('not found'))
  trainingApiMock.reviewTrainingPoint.mockResolvedValue(completedAssessment)
  trainingApiMock.syncTrainingEvidence.mockResolvedValue(feedback)
})

afterEach(() => {
  mounted.splice(0).forEach((app) => app.unmount())
  vi.useRealTimers()
  document.body.innerHTML = ''
})

describe('training assessment panel', () => {

  it('queries a timed-out assessment until complete without submitting it again', async () => {
    vi.useFakeTimers()
    trainingApiMock.getTrainingAssessment
      .mockRejectedValueOnce(new ApiError({
        kind: 'not_found', status: 404, code: 'training_assessment_not_found',
        message: 'not found', details: {}, requestId: 'read', retryable: false,
      }))
      .mockResolvedValueOnce({ ...pendingAssessment, status: 'running' })
      .mockResolvedValue(completedAssessment)
    trainingApiMock.startTrainingAssessment.mockRejectedValue(new ApiError({
      kind: 'timeout', status: null, code: 'request_timeout', message: 'timeout',
      details: {}, requestId: 'start', retryable: false,
    }))
    const host = document.createElement('div')
    document.body.append(host)
    const app = createApp(TrainingAssessmentPanel, { submission })
    mounted.push(app)
    app.mount(host)
    await settle()
    ;[...host.querySelectorAll('button')].find((b) => b.textContent?.includes('开始整卷判定'))?.click()
    await settle()
    expect(host.textContent).toContain('判定进行中')
    await vi.advanceTimersByTimeAsync(3000)
    await settle()
    expect(host.textContent).toContain('复核完成')
    expect(trainingApiMock.startTrainingAssessment).toHaveBeenCalledTimes(1)
    expect(trainingApiMock.getTrainingAssessment).toHaveBeenCalledTimes(3)
    await vi.advanceTimersByTimeAsync(9000)
    expect(trainingApiMock.getTrainingAssessment).toHaveBeenCalledTimes(3)
  })

  it('locks an uncertain point, publishes evidence, and opens only a draft', async () => {
    const host = document.createElement('div')
    document.body.append(host)
    const openDraft = vi.fn()
    const app = createApp(TrainingAssessmentPanel, {
      submission,
      onOpenDraft: openDraft,
    })
    mounted.push(app)
    app.mount(host)
    await settle()

    expect(host.textContent).toContain('得到正确结论')
    expect(host.textContent).toContain('不确定')
    expect(host.textContent).toContain('已请求 1 次')

    const uncertain = [...host.querySelectorAll('details')].find(
      (item) => item.textContent?.includes('得到正确结论'),
    )
    const select = uncertain?.querySelector('select')
    if (select) {
      select.value = 'not_met'
      select.dispatchEvent(new Event('change'))
    }
    const lock = [...(uncertain?.querySelectorAll('button') ?? [])].find(
      (button) => button.textContent?.includes('锁定此判定点'),
    )
    lock?.click()
    await settle()

    expect(trainingApiMock.reviewTrainingPoint).toHaveBeenCalledWith(
      pendingAssessment,
      expect.objectContaining({
        final_state: 'not_met',
        point_id: 'p2',
      }),
    )

    const publish = [...host.querySelectorAll('button')].find(
      (button) => button.textContent?.includes('确认并更新掌握度'),
    )
    publish?.click()
    await settle()

    expect(trainingApiMock.syncTrainingEvidence).toHaveBeenCalledWith(
      completedAssessment,
      'publish',
      expect.stringMatching(/^[0-9a-f]{32}$/),
    )
    expect(host.textContent).toContain('40%')
    expect(host.textContent).toContain('55%')
    expect(host.textContent).toContain('草稿待确认')
    expect(host.textContent).toContain('不会自动冻结、打印或发送')

    const open = [...host.querySelectorAll('button')].find(
      (button) => button.textContent?.includes('打开下一轮草稿'),
    )
    open?.click()
    expect(openDraft).toHaveBeenCalledWith('d'.repeat(64))
  })
})
