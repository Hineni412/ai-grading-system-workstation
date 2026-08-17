import { describe, expect, it } from 'vitest'

import type { WorkspaceAITask } from './contracts'
import { jobDetailLine, jobTitle, returnLocation } from './taskPresentation'

function task(destination: string): WorkspaceAITask {
  return {
    contract_version: 'teacher_workspace_ai_task.v1', task_id: 'task-1', operation_id: 'operation-1',
    module: 'teaching_prep', task_kind: 'teaching_prep.exercise_suggestions',
    source_ref: { kind: 'lesson', id: 'lesson-1', revision: '3' }, context_refs: [], return_target: destination,
    status: 'proposal_ready', phase: 'handoff_ready', progress: 1, send_attempt_count: 1,
    dispatch_evidence: 'response_persisted', cancel_requested: false, job_id: 1,
    proposal_ref_id: 'proposal-1', proposal_revision: '1', error_code: null, revision: 4,
    error_detail: null,
    safe_title: '候选练习', safe_source: '备课', teacher_message: '待审', next_action: '返回',
    handoffs: [], handoff_total: 0, adopted_count: 0, discarded_count: 0, stale_count: 0, pending_count: 0,
    created_at: '', updated_at: '', finished_at: '',
  }
}

describe('job presentation', () => {
  it('names common background jobs instead of a generic fallback', () => {
    const base = {
      id: 1, payload: {}, result: {}, status: 'running' as const, progress: 0.2,
      stage: '', detail: '', error: null, cancel_requested: false,
      created_at: '', started_at: null, updated_at: '', finished_at: null,
    }
    expect(jobTitle({ ...base, job_type: 'question_bank_sync' })).toBe('题库同步')
    expect(jobTitle({ ...base, job_type: 'tagging_sync' })).toBe('题库分析')
    expect(jobTitle({ ...base, job_type: 'teaching_prep.semester_mapping' })).toBe('整理学期资料')
    expect(jobTitle({ ...base, job_type: 'unknown_kind' })).toBe('后台任务')
    expect(jobDetailLine({
      ...base,
      job_type: 'tagging_sync',
      progress: 1,
      status: 'running',
      detail: 'AI 分析已处理 5/20 道题',
    })).toBe('题库分析进行中，点这里回到试卷库')
    expect(jobDetailLine({
      ...base,
      job_type: 'grading_run',
      progress: 0.35,
      detail: '正在批改',
    })).toBe('35% · 正在批改')
  })
})

describe('AI task return location', () => {
  it('returns a materials-step task to the lesson first step', () => {
    expect(returnLocation(task('teaching_prep.lesson.exercises'))).toEqual({
      path: '/teaching-prep',
      query: { view: 'lesson', lesson: 'lesson-1', step: '1' },
    })
  })

  it('returns a slides task to the review step', () => {
    expect(returnLocation(task('teaching_prep.lesson.slides'))).toEqual({
      path: '/teaching-prep',
      query: { view: 'lesson', lesson: 'lesson-1', step: '2' },
    })
  })

  it('returns a package task to the copies step', () => {
    expect(returnLocation(task('teaching_prep.lesson.package'))).toEqual({
      path: '/teaching-prep',
      query: { view: 'lesson', lesson: 'lesson-1', step: '3' },
    })
  })

  it('uses only view/lesson/step even before a handoff exists', () => {
    const queued = {
      ...task('teaching_prep.lesson.plan'),
      status: 'queued' as const,
      proposal_ref_id: null,
      proposal_revision: null,
      context_refs: [{ kind: 'semester', id: 'semester-2', revision: '4' }],
    }

    expect(returnLocation(queued)).toEqual({
      path: '/teaching-prep',
      query: { view: 'lesson', lesson: 'lesson-1', step: '1' },
    })
  })
})
