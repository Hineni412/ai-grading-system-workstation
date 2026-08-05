import { afterEach, describe, expect, it, vi } from 'vitest'

import type { WorkspaceAITask } from '../shared/ai-tasks/contracts'
import { adoptTeachingPrepProposal, findTeachingPrepAdoption } from './aiAdoption'
import { teachingPrepWorkbenchApi } from './api/workbench'

function task(
  taskId: string,
  taskKind: string,
  proposalId: string,
  adoptionState = 'opened',
): WorkspaceAITask {
  return {
    contract_version: 'teacher_workspace_ai_task.v1',
    task_id: taskId,
    operation_id: `operation-${taskId}`,
    module: 'teaching_prep',
    task_kind: taskKind,
    source_ref: { kind: 'lesson', id: 'lesson-1', revision: '7' },
    context_refs: [], return_target: 'teaching_prep.lesson.plan',
    status: 'proposal_ready', phase: 'proposal_ready', progress: 1,
    send_attempt_count: 1, dispatch_evidence: 'response_persisted',
    cancel_requested: false, job_id: 1,
    proposal_ref_id: proposalId, proposal_revision: '3', error_code: null,
    revision: 4, safe_title: '合成任务', safe_source: '备课',
    teacher_message: '请审核', next_action: '审核',
    handoffs: [{
      contract_version: 'teacher_workspace_handoff.v1',
      handoff_id: `handoff-${taskId}`, work_item_id: `item-${taskId}`,
      module: 'teaching_prep', intent: 'review', handling_mode: 'record',
      destination_key: 'teaching_prep.lesson.plan', subject_refs: [],
      draft_ref: { kind: 'draft', id: proposalId, revision: '3' },
      adoption_state: adoptionState, prefill_keys: [], missing_fields: [],
      source_task_id: taskId, source_turn_id: null,
      return_destination_key: 'teaching_prep.lesson.plan',
      return_focus_ref: proposalId, expires_on_source_change: true,
      adoption_id: adoptionState === 'adopted' ? `adoption-${taskId}` : null, revision: 2,
    }],
    handoff_total: 1, adopted_count: 0, discarded_count: 0,
    stale_count: 0, pending_count: 1,
    created_at: '2026-08-05T00:00:00Z',
    updated_at: '2026-08-05T00:00:00Z', finished_at: null,
  }
}

afterEach(() => vi.restoreAllMocks())

describe('teaching-prep AI adoption routing', () => {
  it('matches only the exact proposal, task kind, and adoptable handoff', () => {
    const tasks = [
      task('new-wrong-proposal', 'teaching_prep.lesson_plan', 'proposal-new'),
      task('wrong-kind', 'teaching_prep.slide_change_proposal', 'proposal-current'),
      task('not-routable', 'teaching_prep.lesson_plan', 'proposal-current', 'stale'),
      task('exact', 'teaching_prep.lesson_plan', 'proposal-current'),
    ]

    expect(findTeachingPrepAdoption(
      tasks,
      'teaching_prep.lesson_plan',
      'proposal-current',
    )?.task.task_id).toBe('exact')
  })

  it('keeps an adopted exact handoff available for idempotent receipt recovery', () => {
    const adopted = task(
      'already-adopted',
      'teaching_prep.lesson_plan',
      'proposal-current',
      'adopted',
    )

    expect(findTeachingPrepAdoption(
      [adopted],
      'teaching_prep.lesson_plan',
      'proposal-current',
    )?.handoff.handoff_id).toBe('handoff-already-adopted')
  })

  it('sends the handoff draft revision and current task target revision', async () => {
    const current = task('exact', 'teaching_prep.lesson_plan', 'proposal-current')
    const receipt = {
      adoption_id: 'adoption-1', handoff_id: 'handoff-exact',
      task_kind: current.task_kind, proposal_ref_id: 'proposal-current',
      object_kind: 'lesson_draft', object_id: 'teacher-version',
      object_ref: 'teaching_prep:lesson_draft:teacher-version',
      object_status: 'confirmed', draft_revision: '3', target_revision: '7',
      receipt_revision: '1', adopted_at: '2026-08-05T00:00:01Z',
    }
    const adopt = vi.spyOn(teachingPrepWorkbenchApi, 'adoptAIHandoff')
      .mockResolvedValue(receipt)

    const result = await adoptTeachingPrepProposal(
      [current],
      current.task_kind,
      'proposal-current',
      {
        kind: 'confirm_lesson_draft',
        payload: {
          knowledge_objectives: [], focus_points: [], anticipated_difficulties: [],
          lesson_flow: [], exercise_recommendations: [], slide_adaptations: [],
          uncertainties: [],
        },
      },
    )

    expect(result?.receipt).toEqual(receipt)
    expect(adopt).toHaveBeenCalledWith(
      'handoff-exact',
      '3',
      '7',
      {
        kind: 'confirm_lesson_draft',
        payload: {
          knowledge_objectives: [], focus_points: [], anticipated_difficulties: [],
          lesson_flow: [], exercise_recommendations: [], slide_adaptations: [],
          uncertainties: [],
        },
      },
    )
  })
})
