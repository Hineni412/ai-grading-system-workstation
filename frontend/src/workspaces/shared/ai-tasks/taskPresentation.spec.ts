import { describe, expect, it } from 'vitest'

import type { WorkspaceAITask } from './contracts'
import { jobDetailLine, jobTitle, returnLocation } from './taskPresentation'

function task(returnTarget: string): WorkspaceAITask {
  return {
    contract_version: 'teacher_workspace_ai_task.v1', task_id: 'task-1', operation_id: 'operation-1',
    module: 'class_teacher', task_kind: 'class_teacher.intake_triage',
    source_ref: { kind: 'student', id: 'student-1', revision: '3' }, context_refs: [], return_target: returnTarget,
    status: 'proposal_ready', phase: 'handoff_ready', progress: 1, send_attempt_count: 1,
    dispatch_evidence: 'response_persisted', cancel_requested: false, job_id: 1,
    proposal_ref_id: 'proposal-1', proposal_revision: '1', error_code: null, revision: 4,
    error_detail: null,
    safe_title: '事项整理', safe_source: '班主任', teacher_message: '待审', next_action: '返回',
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
  it('returns the task to the class-teacher destination with its source context', () => {
    expect(returnLocation(task('class_teacher.home'))).toEqual({
      path: '/class-teacher',
      query: {
        destination: 'class_teacher.home',
        source_task_id: 'task-1',
        source_ref: 'student-1',
      },
    })
  })

  it('uses the destination mapping even before a handoff exists', () => {
    const queued = {
      ...task('class_teacher.student.record'),
      status: 'queued' as const,
      proposal_ref_id: null,
      proposal_revision: null,
      context_refs: [{ kind: 'class', id: 'class-2', revision: '4' }],
    }

    expect(returnLocation(queued)).toEqual({
      path: '/class-teacher',
      query: {
        destination: 'class_teacher.student.record',
        source_task_id: 'task-1',
        source_ref: 'student-1',
      },
    })
  })
})
