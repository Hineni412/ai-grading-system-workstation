import { describe, expect, it } from 'vitest'

import type { WorkspaceAITask } from './contracts'
import { returnLocation } from './taskPresentation'

function task(destination: string): WorkspaceAITask {
  return {
    contract_version: 'teacher_workspace_ai_task.v1', task_id: 'task-1', operation_id: 'operation-1',
    module: 'teaching_prep', task_kind: 'teaching_prep.exercise_suggestions',
    source_ref: { kind: 'lesson', id: 'lesson-1', revision: '3' }, context_refs: [], return_target: destination,
    status: 'proposal_ready', phase: 'handoff_ready', progress: 1, send_attempt_count: 1,
    dispatch_evidence: 'response_persisted', cancel_requested: false, job_id: 1,
    proposal_ref_id: 'proposal-1', proposal_revision: '1', error_code: null, revision: 4,
    safe_title: '候选练习', safe_source: '备课', teacher_message: '待审', next_action: '返回',
    handoffs: [], handoff_total: 0, adopted_count: 0, discarded_count: 0, stale_count: 0, pending_count: 0,
    created_at: '', updated_at: '', finished_at: '',
  }
}

describe('AI task return location', () => {
  it('returns an A lesson task to its exact lesson stage panel and focus', () => {
    expect(returnLocation(task('teaching_prep.lesson.exercises'))).toEqual({
      path: '/teaching-prep',
      query: { view: 'lesson', lesson: 'lesson-1', stage: 'materials', panel: 'exercises', focus_ref: 'proposal-1', source_task_id: 'task-1' },
    })
  })

  it('keeps the semester in the return link before a handoff exists', () => {
    const queued = {
      ...task('teaching_prep.lesson.plan'),
      status: 'queued' as const,
      proposal_ref_id: null,
      proposal_revision: null,
      context_refs: [{ kind: 'semester', id: 'semester-2', revision: '4' }],
    }

    expect(returnLocation(queued)).toEqual({
      path: '/teaching-prep',
      query: {
        view: 'lesson', semester: 'semester-2', lesson: 'lesson-1',
        stage: 'plan', panel: 'plan', source_task_id: 'task-1',
      },
    })
  })
})
