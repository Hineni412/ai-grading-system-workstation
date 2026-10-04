import { describe, expect, it } from 'vitest'
import type { TrainingAssessmentOutcome, TrainingAssessmentPoint, TrainingAssessmentQuestion, TrainingFeedback, TrainingScanBatch, TrainingSubmission } from '../api/training'
import { buildQueue, initialSelection, needsReview, nextPending, queueCounts, resultTotals, reviewBody, submissionStage, summaryActions } from '../features/training/training-return'
const submission: TrainingSubmission = { submission_id: 'TEST-s', paper_instance_id: 'TEST-p', student_id: 'TEST-student', student_name: '测试甲', student_code: '01', series_version: 1, status: 'ready', revision: 1, expected_total_pages: 1, missing_pages: [], issue_codes: [], assessment_started: false }
const point: TrainingAssessmentPoint = { point_id: 'p1', content: '测试点', state: 'uncertain', candidate_state: 'uncertain', evidence: 'AI依据', teacher_locked: false, lock_revision: 0 }
const question: TrainingAssessmentQuestion = { task_item_code: 'q1', item_order: 1, status: 'partial', met_count: 0, not_met_count: 0, uncertain_count: 1, unreadable_count: 0, total_count: 1, review_status: 'review_required', review_points: [point] }
const assessment: TrainingAssessmentOutcome = { run_id: 'TEST-run', submission_id: submission.submission_id, submission_revision: 1, status: 'partial', request_count: 1, expected_question_count: 1, expected_point_count: 1, usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 }, latency_ms: 0, issue_codes: [], review_revision: 1, control_state: 'active', workflow_status: 'review_required', action_message: '', attempts: [], questions: [question] }
const complete: TrainingAssessmentOutcome = { ...assessment, status: 'succeeded', questions: [{ ...question, review_status: 'completed', review_points: [{ ...point, state: 'met' }] }] }
const feedback: TrainingFeedback = { schema_version: 'training-feedback-v1', feedback_id: 'TEST-f', submission_id: submission.submission_id, submission_revision: 1, source_review_revision: 1, status: 'complete', student: {}, summary: { published_question_count: 1, ready_question_count: 1, total_question_count: 1, pending_outbox_count: 0, message: '' }, questions: [], mastery_changes: [], next_round: { status: '', message: '', changes: [] }, timeline: [], safety: { is_exam_score: false, changes_exam_score: false, auto_paper_created: false, auto_printed: false }, evidence_version: 'TEST' }
describe('training return rules', () => {
  it('derives stages from question completion and the current feedback revision', () => {
    expect(submissionStage({ ...submission, status: 'cancelled' }, assessment, feedback, true)).toBe('cancelled')
    expect(submissionStage({ ...submission, status: 'manual_review' }, null, null, true)).toBe('scan_issue')
    expect(submissionStage(submission, null, null, true)).toBe('unknown')
    expect(submissionStage(submission, null, null)).toBe('unjudged')
    expect(submissionStage(submission, { ...assessment, status: 'running' }, null)).toBe('running')
    for (const status of ['failed', 'cancelled']) {
      expect(submissionStage(submission, { ...assessment, status, questions: [{ ...question, review_points: [{ ...point, state: null }] }] }, null)).toBe('failed')
      expect(submissionStage(submission, { ...assessment, status }, null)).toBe('review')
    }
    expect(submissionStage(submission, { ...assessment, questions: [{ ...question, review_points: [] }] }, null)).toBe('review')
    expect(submissionStage(submission, complete, null)).toBe('update')
    expect(submissionStage(submission, complete, { ...feedback, status: 'withdrawn' })).toBe('update')
    expect(submissionStage(submission, complete, { ...feedback, source_review_revision: 2 })).toBe('update')
    expect(submissionStage(submission, complete, feedback)).toBe('done')
  })
  it('counts pending points using teacher locks rather than aggregate uncertain counts', () => {
    expect(needsReview({ ...point, state: null, teacher_locked: true })).toBe(true)
    for (const state of ['unreadable', 'uncertain'] as const) {
      expect(needsReview({ ...point, state })).toBe(true)
      expect(needsReview({ ...point, state, teacher_locked: true })).toBe(false)
    }
    expect(resultTotals({ ...assessment, questions: [{ ...question, uncertain_count: 8, review_points: [{ ...point, teacher_locked: true }] }] }).pending).toBe(0)
  })
  it('excludes retired and cancelled issue pages, sorts students, and cycles actionable rows', () => {
    const batch: TrainingScanBatch = { batch_id: 'TEST', paper_batch_id: 'TEST', status: 'ready', revision: 1, duplicate_upload: false, created_at: '', updated_at: '', history: [], candidates: [],
      submissions: [submission, { ...submission, submission_id: 'TEST-b', student_code: '02', status: 'manual_review' }, { ...submission, submission_id: 'TEST-c', status: 'cancelled' }],
      pages: ['unassigned', 'replaced', 'dismissed', 'assigned'].map((state, i) => ({ scan_page_id: `p${i}`, upload_id: 'TEST', upload_page_number: i + 1, preview_url: '/TEST', issue_code: 'identity_unreadable', state: state as 'unassigned' | 'replaced' | 'dismissed' | 'assigned', rotation_degrees: 0, submission_id: i === 3 ? 'TEST-c' : null })) }
    const rows = buildQueue(batch, {})
    expect(rows.map(r => r.id)).toEqual(['page:p0', 'TEST-b', 'TEST-s', 'TEST-c'])
    expect(queueCounts(rows)).toMatchObject({ counts: { issue: 1, scan_issue: 1, unknown: 1, cancelled: 1 }, total: 2 })
    expect(nextPending(rows, 'TEST-b')).toBe('page:p0')
    expect(initialSelection(rows, 'scan')).toBe('page:p0')
    const others = rows.map(r => r.id === 'TEST-s' ? { ...r, stage: 'review' as const } : r.id === 'TEST-b' ? { ...r, stage: 'update' as const } : r)
    expect(initialSelection(others, 'review')).toBe('TEST-s')
    expect(initialSelection(others, 'publish')).toBe('TEST-b')
    expect(summaryActions(others).primary).toBe('issue')
    const actionable = others.filter(r => r.submission).map(r => r.id === 'TEST-s' ? { ...r, stage: 'unjudged' as const } : r)
    expect(summaryActions(actionable)).toEqual({ primary: 'assess', secondaryPublish: true })
    expect(summaryActions(actionable.filter(r => r.stage !== 'unjudged')).primary).toBe('publish')
  })
  it('builds automatic lock reasons, evidence and the 500 character limit', () => {
    expect(reviewBody(question, point, 'met', '', 'token')).toMatchObject({ teacher_reason: 'AI 不确定 → 教师达成', teacher_evidence: '教师查看原始训练答卷后判定', operation_token: 'token' })
    expect(reviewBody(question, { ...point, candidate_state: 'met' }, 'met', '', 't').teacher_evidence).toBe('AI依据')
    expect(reviewBody(question, { ...point, candidate_state: null }, 'unreadable', '核对备注', 't')).toMatchObject({ teacher_reason: 'AI 未判定 → 教师无法辨认；核对备注', teacher_evidence: '核对备注' })
    const body = reviewBody(question, point, 'uncertain', '字'.repeat(600), 't')
    expect(body.teacher_reason).toHaveLength(500); expect(body.teacher_evidence).toHaveLength(500)
  })
})
