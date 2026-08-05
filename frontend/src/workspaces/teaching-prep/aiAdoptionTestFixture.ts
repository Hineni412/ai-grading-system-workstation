import type { OpaqueRef, WorkspaceAITask } from '../shared/ai-tasks/contracts'

export function adoptedTeachingPrepTask(options: {
  taskId: string
  taskKind: string
  proposalId: string
  sourceRef: OpaqueRef
  draftKind: string
  draftRevision?: string
}): WorkspaceAITask {
  const draftRevision = options.draftRevision ?? '3'
  return {
    contract_version: 'teacher_workspace_ai_task.v1',
    task_id: options.taskId,
    operation_id: `operation-${options.taskId}`,
    module: 'teaching_prep',
    task_kind: options.taskKind,
    source_ref: options.sourceRef,
    context_refs: [],
    return_target: 'teaching_prep.lesson.plan',
    status: 'proposal_ready',
    phase: 'proposal_ready',
    progress: 1,
    send_attempt_count: 1,
    dispatch_evidence: 'response_persisted',
    cancel_requested: false,
    job_id: 1,
    proposal_ref_id: options.proposalId,
    proposal_revision: draftRevision,
    error_code: null,
    revision: 5,
    safe_title: '已采用建议',
    safe_source: '备课',
    teacher_message: '已采用',
    next_action: '查看',
    handoffs: [{
      contract_version: 'teacher_workspace_handoff.v1',
      handoff_id: `handoff-${options.taskId}`,
      work_item_id: `work-item-${options.taskId}`,
      module: 'teaching_prep',
      intent: 'review',
      handling_mode: 'record',
      destination_key: 'teaching_prep.lesson.plan',
      subject_refs: [],
      draft_ref: {
        kind: options.draftKind,
        id: options.proposalId,
        revision: draftRevision,
      },
      adoption_state: 'adopted',
      prefill_keys: [],
      missing_fields: [],
      source_task_id: options.taskId,
      source_turn_id: null,
      return_destination_key: 'teaching_prep.lesson.plan',
      return_focus_ref: options.proposalId,
      expires_on_source_change: true,
      adoption_id: `adoption-${options.taskId}`,
      revision: 3,
    }],
    handoff_total: 1,
    adopted_count: 1,
    discarded_count: 0,
    stale_count: 0,
    pending_count: 0,
    created_at: '',
    updated_at: '',
    finished_at: '',
  }
}
