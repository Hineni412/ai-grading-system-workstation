import { assertNoPathLikeKeys, isNullableString, isRecord } from '../../../api/validation'

export const WORKSPACE_AI_TASK_STATUSES = [
  'prepared',
  'queued',
  'running',
  'needs_input',
  'proposal_ready',
  'failed_before_dispatch',
  'failed',
  'result_unknown',
  'invalid_result',
  'cancelled_before_dispatch',
  'discarded',
] as const

export type WorkspaceAITaskStatus = (typeof WORKSPACE_AI_TASK_STATUSES)[number]

export interface OpaqueRef {
  kind: string
  id: string
  revision: string
}

export interface WorkspaceAIHandoff {
  contract_version: 'teacher_workspace_handoff.v1'
  handoff_id: string
  work_item_id: string
  module: 'class_teacher'
  intent: string
  handling_mode: string
  destination_key: string
  subject_refs: OpaqueRef[]
  draft_ref: OpaqueRef
  adoption_state: string
  prefill_keys: string[]
  missing_fields: string[]
  source_task_id: string
  source_turn_id: string | null
  return_destination_key: string
  return_focus_ref: string | null
  expires_on_source_change: boolean
  adoption_id: string | null
  revision: number
}

export interface WorkspaceAITask {
  contract_version: 'teacher_workspace_ai_task.v1'
  task_id: string
  operation_id: string
  module: 'class_teacher'
  task_kind: string
  source_ref: OpaqueRef
  context_refs: OpaqueRef[]
  return_target: string
  status: WorkspaceAITaskStatus
  phase: string
  progress: number
  send_attempt_count: 0 | 1
  dispatch_evidence: 'not_started' | 'may_have_started' | 'response_persisted'
  cancel_requested: boolean
  job_id: number | null
  proposal_ref_id: string | null
  proposal_revision: string | null
  error_code: string | null
  error_detail: string | null
  revision: number
  safe_title: string
  safe_source: string
  teacher_message: string
  next_action: string
  handoffs: WorkspaceAIHandoff[]
  handoff_total: number
  adopted_count: number
  discarded_count: number
  stale_count: number
  pending_count: number
  created_at: string
  updated_at: string
  finished_at: string | null
}

export interface PrepareWorkspaceAITaskInput {
  operation_id: string
  module: WorkspaceAITask['module']
  task_kind: string
  source_ref: OpaqueRef
  context_refs: OpaqueRef[]
  prompt_contract_version: string
  model_destination_fingerprint: string
  return_target: string
}

export function decodeWorkspaceAITask(value: unknown): WorkspaceAITask {
  assertNoPathLikeKeys(value)
  if (
    !isRecord(value)
    || value.contract_version !== 'teacher_workspace_ai_task.v1'
    || typeof value.task_id !== 'string'
    || !value.task_id
    || typeof value.operation_id !== 'string'
    || value.module !== 'class_teacher'
    || !Array.isArray(value.context_refs)
    || !WORKSPACE_AI_TASK_STATUSES.includes(value.status as WorkspaceAITaskStatus)
    || typeof value.progress !== 'number'
    || !Number.isSafeInteger(value.revision)
    || !Array.isArray(value.handoffs)
    || typeof value.safe_title !== 'string'
    || typeof value.teacher_message !== 'string'
    || typeof value.next_action !== 'string'
    || !isNullableString(value.error_detail)
    || !isNullableString(value.finished_at)
  ) {
    throw new Error('Invalid workspace AI task response')
  }
  for (const handoff of value.handoffs) {
    if (
      !isRecord(handoff)
      || handoff.contract_version !== 'teacher_workspace_handoff.v1'
      || typeof handoff.handoff_id !== 'string'
      || typeof handoff.destination_key !== 'string'
      || 'prefill' in handoff
    ) throw new Error('Invalid workspace AI handoff response')
  }
  return value as unknown as WorkspaceAITask
}

export function decodeWorkspaceAITaskList(value: unknown): WorkspaceAITask[] {
  if (!Array.isArray(value)) throw new Error('Invalid workspace AI task list')
  return value.map(decodeWorkspaceAITask)
}

export const TERMINAL_WORKSPACE_AI_TASK_STATUSES = new Set<WorkspaceAITaskStatus>([
  'needs_input',
  'proposal_ready',
  'failed_before_dispatch',
  'failed',
  'result_unknown',
  'invalid_result',
  'cancelled_before_dispatch',
  'discarded',
])
