import { createApp, nextTick, type App } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { PersonalizedPaperInstance, TrainingScanBatch, TrainingAssessmentOutcome, TrainingFeedback } from '../api/training'
import { ApiError } from '../api/errors'
import TrainingScanBatchPanel from '../components/training/TrainingScanBatchPanel.vue'
const trainingApiMock = vi.hoisted(() => ({
  listTrainingScanBatches: vi.fn(), getTrainingScanBatch: vi.fn(), createTrainingScanBatch: vi.fn(), uploadTrainingScan: vi.fn(),
  resolveTrainingScanPage: vi.fn(), cancelTrainingSubmission: vi.fn(), getTrainingAssessment: vi.fn(), getTrainingFeedback: vi.fn(),
  startTrainingAssessment: vi.fn(), reviewTrainingPoint: vi.fn(), controlTrainingAssessment: vi.fn(), syncTrainingEvidence: vi.fn(), replayTrainingEvidence: vi.fn(),
}))
vi.mock('../api/training', async original => ({ ...await original<typeof import('../api/training')>(), trainingApi: trainingApiMock }))
const paper = {
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
    frozen_pdf: '/api/training/paper.pdf',
  },
  created_at: '2026-07-30T08:00:00+00:00',
} satisfies PersonalizedPaperInstance

const emptyBatch = {
  batch_id: 'b'.repeat(64),
  paper_batch_id: 'f'.repeat(64),
  status: 'manual_review',
  revision: 1,
  duplicate_upload: false,
  submissions: [{
    submission_id: 'c'.repeat(64),
    paper_instance_id: paper.paper_instance_id,
    student_id: paper.student_id,
    student_code: paper.student_code,
    student_name: paper.student_name,
    class_id: paper.class_id,
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
    paper_instance_id: paper.paper_instance_id,
    student_id: paper.student_id,
    student_code: paper.student_code,
    student_name: paper.student_name,
    series_version: 1,
    total_pages: 2,
  }],
  history: [],
  created_at: '2026-07-30T08:00:00+00:00',
  updated_at: '2026-07-30T08:00:00+00:00',
} satisfies TrainingScanBatch

const anomalyBatch = {
  ...emptyBatch,
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

const pendingAssessment = {
  run_id: 'a'.repeat(64),
  submission_id: emptyBatch.submissions[0]!.submission_id,
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
  workflow_status: 'completed',
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
  submission_id: emptyBatch.submissions[0]!.submission_id,
  submission_revision: 1,
  source_review_revision: 2,
  status: 'complete',
  student: { student_id: paper.student_id },
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
    mastery_after: { value: 0.55, tier: 'weak', interval_low: .3, interval_high: .72,
      observation_count: 10, full_correct_count: 4 },
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
function notFound(code = 'training_assessment_not_found') { return new ApiError({ kind: 'not_found', status: 404, code, message: 'TEST', details: {}, requestId: 'TEST', retryable: false }) }
function timeout() { return new ApiError({ kind: 'timeout', status: null, code: 'request_timeout', message: 'TEST', details: {}, requestId: 'TEST', retryable: false }) }
const readySubmission = { ...emptyBatch.submissions[0]!, status: 'ready' as const, missing_pages: [] }
function readyBatch(count = 1): TrainingScanBatch {
  return { ...emptyBatch, status: 'ready', submissions: Array.from({ length: count }, (_, i) => ({ ...readySubmission, submission_id: i ? `TEST-sub-${i}` : readySubmission.submission_id, student_name: `合成学生${i}`, student_code: `TEST-${i}` })) }
}
async function settle() { for (let i = 0; i < 12; i++) { await nextTick(); await Promise.resolve() } }
async function mount(batch: TrainingScanBatch | null = readyBatch(), initialFocus?: 'scan' | 'review' | 'publish', history?: TrainingScanBatch[]) {
  trainingApiMock.listTrainingScanBatches.mockResolvedValue(history ? history.map(b => ({ ...b, submission_count: b.submissions.length })) : batch ? [{ ...batch, submission_count: batch.submissions.length }] : [])
  if (batch) trainingApiMock.getTrainingScanBatch.mockResolvedValue(batch)
  const host = document.createElement('div'); document.body.append(host)
  const openDraft = vi.fn()
  const app = createApp(TrainingScanBatchPanel, { instances: [paper], initialFocus, onOpenDraft: openDraft }); mounted.push(app); app.mount(host)
  await settle(); return { host, app, openDraft }
}
function button(text: string, host: ParentNode = document) { return [...host.querySelectorAll<HTMLButtonElement>('button')].find(b => b.textContent?.trim() === text)! }
function change(select: HTMLSelectElement, value: string) { select.value = value; select.dispatchEvent(new Event('change', { bubbles: true })) }
async function chooseFile(host: HTMLElement) {
  const input = host.querySelector<HTMLInputElement>('input[type=file]')!
  Object.defineProperty(input, 'files', { value: [new File(['TEST'], 'TEST-scan.png', { type: 'image/png' })] })
  input.dispatchEvent(new Event('change', { bubbles: true })); await settle()
}
beforeEach(() => {
  vi.resetAllMocks()
  trainingApiMock.getTrainingAssessment.mockRejectedValue(notFound())
  trainingApiMock.getTrainingFeedback.mockRejectedValue(notFound('training_feedback_not_found'))
  trainingApiMock.reviewTrainingPoint.mockResolvedValue(completedAssessment)
  trainingApiMock.syncTrainingEvidence.mockResolvedValue(feedback)
  trainingApiMock.createTrainingScanBatch.mockResolvedValue(emptyBatch)
  trainingApiMock.uploadTrainingScan.mockResolvedValue(anomalyBatch)
})
afterEach(() => { mounted.splice(0).forEach(app => app.unmount()); vi.useRealTimers(); vi.restoreAllMocks(); document.body.innerHTML = '' })
describe('training scan return workspace', () => {
  it('imports in one step and retains a created batch if upload fails', async () => {
    trainingApiMock.uploadTrainingScan.mockRejectedValue(new Error('TEST upload failed'))
    const { host } = await mount(null)
    expect(host.textContent).toContain('本次回收 1 份训练卷')
    expect(host.textContent).not.toContain('建立扫描批次')
    await chooseFile(host); button('导入并归组（1 个文件）', host).click(); await settle()
    expect(trainingApiMock.createTrainingScanBatch).toHaveBeenCalledWith([paper.paper_instance_id], expect.any(String))
    expect(trainingApiMock.uploadTrainingScan).toHaveBeenCalledOnce()
    expect(host.querySelector('.return-summary')).not.toBeNull()
    expect(host.textContent).toContain('已有归组结果保持不变')
  })
  it('restores the latest batch and puts anomalies first with default missing-page matching', async () => {
    trainingApiMock.resolveTrainingScanPage.mockResolvedValue(emptyBatch)
    const { host } = await mount(anomalyBatch, 'scan')
    expect(trainingApiMock.getTrainingScanBatch).toHaveBeenCalledWith(emptyBatch.batch_id)
    expect(host.querySelector('.return-queue__rows>button[aria-pressed=true]')?.textContent).toContain('上传文件第 1 页')
    expect(host.querySelector<HTMLSelectElement>('.return-issue-form select')?.value).toBe(paper.paper_instance_id)
    button('人工匹配', host).click(); await settle()
    expect(trainingApiMock.resolveTrainingScanPage).toHaveBeenCalledWith(anomalyBatch, anomalyBatch.pages[0], expect.objectContaining({ action: 'match', page_number: 1 }))
    expect(host.querySelector('.return-queue__rows>button[aria-pressed=true]')?.textContent).toContain('缺 2 页')
    change(host.querySelector<HTMLSelectElement>('[aria-label="扫描批次"]')!, ''); await settle()
    expect(host.querySelector('.return-start')).not.toBeNull()
    change(host.querySelector<HTMLSelectElement>('[aria-label="扫描批次"]')!, emptyBatch.batch_id); await settle()
    expect(host.querySelector('.return-summary')).not.toBeNull()
  })
  it('limits state reads to four simultaneous requests and ignores old-batch results', async () => {
    let concurrent = 0, maximum = 0
    const resolves: Array<() => void> = []
    trainingApiMock.getTrainingAssessment.mockImplementation(async () => {
      concurrent++; maximum = Math.max(maximum, concurrent)
      await new Promise<void>(resolve => resolves.push(resolve)); concurrent--
      return completedAssessment
    })
    const { host } = await mount(readyBatch(8))
    expect(maximum).toBe(4)
    const select = host.querySelector<HTMLSelectElement>('[aria-label="扫描批次"]')!
    // Initial reads remain in flight; component removal must discard every late result.
    mounted[0]!.unmount(); mounted.splice(0, 1)
    for (const release of resolves) release(); await settle()
    expect(trainingApiMock.getTrainingFeedback).not.toHaveBeenCalled()
    expect(select.isConnected).toBe(false)
  })
  it('confirms a batch once, cancels without writes, and sends each submission exactly once in order', async () => {
    const batch = readyBatch(3)
    trainingApiMock.startTrainingAssessment.mockImplementation(async (id: string) => ({ ...completedAssessment, submission_id: id }))
    const { host } = await mount(batch)
    button('判定 3 份（3 次模型请求）', host).click(); await settle()
    expect(document.querySelector('[role=dialog]')?.textContent).toContain('共 3 次模型请求，产生费用')
    button('取消').click(); await settle(); expect(trainingApiMock.startTrainingAssessment).not.toHaveBeenCalled()
    button('判定 3 份（3 次模型请求）', host).click(); await settle(); button('确认判定 3 份').click(); await settle()
    expect(trainingApiMock.startTrainingAssessment.mock.calls.map(([id]) => id)).toEqual(batch.submissions.map(s => s.submission_id))
    expect(host.textContent).toContain('已判定 3 份')
  })
  it('queries an ambiguous write without resubmission, continues after a failure, and stops the remainder', async () => {
    vi.useFakeTimers()
    const batch = readyBatch(3)
    let release: (() => void) | undefined
    trainingApiMock.startTrainingAssessment.mockRejectedValueOnce(timeout()).mockRejectedValueOnce(new Error('TEST')).mockResolvedValueOnce(completedAssessment)
    const { host } = await mount(batch)
    trainingApiMock.getTrainingAssessment.mockResolvedValue({ ...pendingAssessment, status: 'running' })
    button('判定 3 份（3 次模型请求）', host).click(); await settle(); button('确认判定 3 份').click(); await settle()
    expect(trainingApiMock.startTrainingAssessment).toHaveBeenCalledTimes(3)
    expect(host.textContent).toContain('2 份未完成，不会自动重试')
    trainingApiMock.getTrainingAssessment.mockResolvedValue(completedAssessment)
    await vi.advanceTimersByTimeAsync(3000); await settle()
    expect(trainingApiMock.startTrainingAssessment).toHaveBeenCalledTimes(3)
    const second = await mount(readyBatch(2))
    trainingApiMock.getTrainingAssessment.mockRejectedValue(notFound())
    await settle()
    // Fresh reload establishes the unjudged list used by the next confirmation.
    button('刷新', second.host).click(); await settle()
    trainingApiMock.startTrainingAssessment.mockImplementation(() => new Promise<TrainingAssessmentOutcome>(resolve => { release = () => resolve(completedAssessment) }))
    button('判定 2 份（2 次模型请求）', second.host).click(); await settle(); button('确认判定 2 份').click(); await settle()
    button('停止发送剩余', second.host).click(); release!(); await settle()
    expect(trainingApiMock.startTrainingAssessment).toHaveBeenCalledTimes(4)
    expect(second.host.textContent).toContain('1 份已跳过')
  })
  it('locks in one click, disables all points during save, retains notes, and publishes then opens the next draft', async () => {
    const batch = readyBatch(2)
    trainingApiMock.getTrainingAssessment.mockImplementation(async (id: string) => ({ ...pendingAssessment, submission_id: id, questions: pendingAssessment.questions.map(q => ({ ...q, review_points: q.review_points.map(p => ({ ...p, candidate_state: p.state })) })) }))
    const { host, openDraft } = await mount(batch, 'review')
    const pending = host.querySelector<HTMLElement>('[data-pending=true]')!
    expect(document.activeElement).toBe(pending)
    const menu = pending.querySelector<HTMLDetailsElement>('details')!; menu.open = true
    const note = menu.querySelector<HTMLInputElement>('input')!; note.value = '核对备注'; note.dispatchEvent(new Event('input')); await settle()
    const queue = () => [...host.querySelectorAll<HTMLButtonElement>('.return-queue__rows>button')]
    queue()[1]!.click(); await settle(); queue()[0]!.click(); await settle()
    expect(host.querySelector<HTMLInputElement>('[data-pending=true] input')?.value).toBe('核对备注')
    let release: ((value: TrainingAssessmentOutcome) => void) | undefined
    trainingApiMock.reviewTrainingPoint.mockImplementation(() => new Promise<TrainingAssessmentOutcome>(resolve => { release = resolve }))
    host.querySelector<HTMLButtonElement>('[data-pending=true] [data-final-state=not_met]')!.click(); await settle()
    expect([...host.querySelectorAll<HTMLButtonElement>('[data-final-state]')].every(b => b.disabled)).toBe(true)
    expect(trainingApiMock.reviewTrainingPoint).toHaveBeenCalledExactlyOnceWith(expect.anything(), expect.objectContaining({ teacher_evidence: '核对备注', teacher_reason: 'AI 不确定 → 教师未达成；核对备注', final_state: 'not_met', point_id: 'p2' }))
    release!(completedAssessment); await settle()
    host.querySelector<HTMLButtonElement>('[data-point-key$=":p2"] [data-final-state=not_met]')!.click(); await settle()
    expect(trainingApiMock.reviewTrainingPoint).toHaveBeenCalledTimes(1)
    button('确认并更新掌握度', host).click(); await settle()
    expect(trainingApiMock.syncTrainingEvidence).toHaveBeenCalledWith(completedAssessment, 'publish', expect.stringMatching(/^[0-9a-f]{32}$/))
    expect(host.textContent).toContain('已保存 1/1 题证据'); expect(host.textContent).toContain('40%'); expect(host.textContent).toContain('55%')
    expect(host.querySelector('.ledger-view-tabs [aria-pressed=true]')?.textContent).toBe('掌握度变化')
    button('打开下一轮草稿', host).click(); await settle()
    expect(openDraft).toHaveBeenCalledExactlyOnceWith(feedback.next_round.draft_id)
    trainingApiMock.syncTrainingEvidence.mockResolvedValue({ ...feedback, status: 'withdrawn' })
    button('撤回本次证据', host).click(); await settle()
    expect(trainingApiMock.syncTrainingEvidence).toHaveBeenLastCalledWith(completedAssessment, 'withdraw', expect.any(String))
    expect(host.querySelector('.return-queue__rows>button[aria-pressed=true]')?.textContent).toContain('待更新')
  })
  it('refreshes a conflicting teacher lock without retrying it', async () => {
    trainingApiMock.getTrainingAssessment.mockResolvedValue(pendingAssessment)
    trainingApiMock.reviewTrainingPoint.mockRejectedValue(new ApiError({ kind: 'conflict', status: 409, code: 'training_assessment_review_conflict', message: 'TEST', details: {}, requestId: 'TEST', retryable: false }))
    const { host } = await mount()
    host.querySelector<HTMLButtonElement>('[data-pending=true] [data-final-state=met]')!.click(); await settle()
    expect(trainingApiMock.getTrainingAssessment).toHaveBeenCalledTimes(2)
    expect(trainingApiMock.reviewTrainingPoint).toHaveBeenCalledOnce()
    expect(host.textContent).toContain('已刷新，请重新确认这一点')
  })
  it('publishes only fully completed submissions and checks an ambiguous publication without resending', async () => {
    const batch = readyBatch(3)
    trainingApiMock.getTrainingAssessment.mockImplementation(async (id: string) => ({ ...(id === batch.submissions[2]!.submission_id ? pendingAssessment : completedAssessment), submission_id: id }))
    trainingApiMock.syncTrainingEvidence.mockRejectedValueOnce(timeout()).mockResolvedValue(feedback)
    const { host } = await mount(batch, 'publish')
    trainingApiMock.getTrainingFeedback.mockResolvedValue(feedback)
    button('确认并更新 2 份掌握度', host).click(); await settle()
    expect(trainingApiMock.syncTrainingEvidence.mock.calls.map(([a]) => a.submission_id)).toEqual(batch.submissions.slice(0, 2).map(s => s.submission_id))
    expect(host.textContent).toContain('已更新 2 份掌握度；另有 1 份待复核，需逐份确认')
  })
  it('asks for a separate retry confirmation and pauses after three result-query failures', async () => {
    vi.useFakeTimers()
    trainingApiMock.getTrainingAssessment.mockResolvedValue({ ...pendingAssessment, status: 'failed' })
    trainingApiMock.controlTrainingAssessment.mockResolvedValue(completedAssessment)
    const { host } = await mount()
    button('教师确认后重试一次（1 次请求）', host).click(); await settle()
    expect(document.querySelector('[role=dialog]')?.textContent).toContain('追加 1 次模型请求并产生费用')
    button('取消').click(); await settle(); expect(trainingApiMock.controlTrainingAssessment).not.toHaveBeenCalled()
    button('教师确认后重试一次（1 次请求）', host).click(); await settle(); button('确认重试一次').click(); await settle()
    expect(trainingApiMock.controlTrainingAssessment).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ status: 'failed' }), expect.objectContaining({ action: 'retry' }))
    trainingApiMock.getTrainingAssessment.mockResolvedValue({ ...pendingAssessment, status: 'running' })
    const running = await mount()
    trainingApiMock.getTrainingAssessment.mockRejectedValue(new Error('TEST query failed'))
    await vi.advanceTimersByTimeAsync(9000); await settle()
    expect(running.host.textContent).toContain('不会自动追加模型请求')
    const reads = trainingApiMock.getTrainingAssessment.mock.calls.length
    await vi.advanceTimersByTimeAsync(9000)
    expect(trainingApiMock.getTrainingAssessment).toHaveBeenCalledTimes(reads)
    expect(button('重新查询', running.host).disabled).toBe(false)
  })
  it('moves focus to the next pending point and keeps shortcuts out of note fields', async () => {
    const question = pendingAssessment.questions[0]!
    const p = question.review_points[1]!
    const pending = { ...pendingAssessment, questions: [{ ...question, review_points: [p, { ...p, point_id: 'TEST-p3' }] }] }
    trainingApiMock.getTrainingAssessment.mockResolvedValue(pending)
    const { host } = await mount()
    const points = () => [...host.querySelectorAll<HTMLElement>('[data-pending=true]')]
    const first = points()[0]!, second = points()[1]!
    expect(document.activeElement).toBe(first)
    first.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }))
    expect(document.activeElement).toBe(second)
    const note = second.querySelector<HTMLInputElement>('input')!
    note.focus(); note.dispatchEvent(new KeyboardEvent('keydown', { key: '1', bubbles: true }))
    expect(trainingApiMock.reviewTrainingPoint).not.toHaveBeenCalled()
    trainingApiMock.reviewTrainingPoint.mockResolvedValue({ ...pending, review_revision: pending.review_revision + 1, questions: [{ ...question,
      review_points: [{ ...p, state: 'not_met', teacher_locked: true }, { ...p, point_id: 'TEST-p3' }] }] })
    first.querySelector<HTMLButtonElement>('[data-final-state=not_met]')!.click(); await settle()
    expect(document.activeElement).toBe(host.querySelector('[data-point-key$=":TEST-p3"]'))
  })
  it('discards a previous batch read when history switches', async () => {
    trainingApiMock.getTrainingAssessment.mockRejectedValue(new Error('TEST old batch unread'))
    const old = readyBatch(), latest = { ...readyBatch(), batch_id: 'TEST-history', student_name: 'TEST-history',
      submissions: [{ ...readySubmission, submission_id: 'TEST-history-sub', student_name: 'TEST-history-student' }] }
    const { host } = await mount(old, undefined, [old, latest])
    let release: ((a: TrainingAssessmentOutcome) => void) | undefined
    trainingApiMock.getTrainingAssessment.mockImplementationOnce(() => new Promise<TrainingAssessmentOutcome>(resolve => { release = resolve }))
    button('重新读取', host).click(); await settle()
    trainingApiMock.getTrainingAssessment.mockResolvedValue(pendingAssessment)
    trainingApiMock.getTrainingScanBatch.mockResolvedValue(latest)
    const history = host.querySelector<HTMLSelectElement>('.return-summary select')!
    change(history, latest.batch_id); await settle()
    release!({ ...completedAssessment, review_revision: 99 }); await settle()
    expect(host.querySelector('.return-queue__rows')?.textContent).toContain('TEST-history-student')
    expect(host.querySelector('.return-ledger')?.textContent).toContain('待复核')
    expect(host.querySelector('.return-ledger')?.textContent).not.toContain('全部确定')
  })
  it('skips a queued submission whose revision changes during batch execution', async () => {
    const batch = readyBatch(2)
    const { host } = await mount(batch)
    let release: ((a: TrainingAssessmentOutcome) => void) | undefined
    trainingApiMock.startTrainingAssessment.mockImplementation(() => new Promise<TrainingAssessmentOutcome>(resolve => { release = resolve }))
    button('判定 2 份（2 次模型请求）', host).click(); await settle(); button('确认判定 2 份').click(); await settle()
    batch.submissions[1]!.revision++
    release!(completedAssessment); await settle()
    expect(trainingApiMock.startTrainingAssessment).toHaveBeenCalledOnce()
    expect(host.textContent).toContain('1 份已跳过')
  })
  it.each(['scan', 'review', 'publish'] as const)('selects the initial %s workbench target', async focus => {
    const batch = { ...readyBatch(2), pages: anomalyBatch.pages }
    trainingApiMock.getTrainingAssessment.mockImplementation(async (id: string) => ({ ...(id === batch.submissions[0]!.submission_id ? pendingAssessment : completedAssessment), submission_id: id }))
    const { host } = await mount(batch, focus)
    const selected = host.querySelector<HTMLButtonElement>('.return-queue__rows>button[aria-pressed=true]')!
    expect(selected.dataset.rowId).toBe(focus === 'scan' ? `page:${anomalyBatch.pages[0]!.scan_page_id}` : batch.submissions[focus === 'review' ? 0 : 1]!.submission_id)
  })
})
