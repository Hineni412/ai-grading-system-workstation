import { apiClient } from '../../../api/client'

const CLIENT_HEADER = 'class-teacher-browser-v1'

export interface ModelPreview {
  preview_id: string
  purpose: string
  classification: 'restricted'
  exact_payload: {
    purpose: string
    student_alias: string
    task_text: string
    instructions: string
  }
  removed_categories: string[]
  fingerprint: string
  expires_at: string
  model_name: string
  model_enabled: boolean
  max_physical_requests: 1
  estimated_cost: number | null
}

export interface ModelOperation {
  preview_id: string
  operation_id: string
  state:
    | 'previewed'
    | 'confirmed'
    | 'claimed'
    | 'succeeded'
    | 'failed_before_send'
    | 'result_unknown'
    | 'cancelled_before_send'
  physical_request_count: number
  error_category: string | null
  draft_text: string | null
  response_kind: 'follow_up' | 'proposal' | null
  follow_up_questions: string[]
  proposal: Record<string, unknown> | null
  teacher_confirmation_required: boolean
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('contract')
  return value as Record<string, unknown>
}

function string(value: unknown): string {
  if (typeof value !== 'string') throw new Error('contract')
  return value
}

function nullableString(value: unknown): string | null {
  return value === null ? null : string(value)
}

function number(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) throw new Error('contract')
  return value
}

function boolean(value: unknown): boolean {
  if (typeof value !== 'boolean') throw new Error('contract')
  return value
}

function preview(value: unknown): ModelPreview {
  const item = record(value)
  const payload = record(item.exact_payload)
  if (!Array.isArray(item.removed_categories)) throw new Error('contract')
  return {
    preview_id: string(item.preview_id),
    purpose: string(item.purpose),
    classification: string(item.classification) as 'restricted',
    exact_payload: {
      purpose: string(payload.purpose),
      student_alias: string(payload.student_alias),
      task_text: string(payload.task_text),
      instructions: string(payload.instructions),
    },
    removed_categories: item.removed_categories.map(string),
    fingerprint: string(item.fingerprint),
    expires_at: string(item.expires_at),
    model_name: string(item.model_name),
    model_enabled: boolean(item.model_enabled),
    max_physical_requests: number(item.max_physical_requests) as 1,
    estimated_cost: item.estimated_cost === null ? null : number(item.estimated_cost),
  }
}

function operation(value: unknown): ModelOperation {
  const item = record(value)
  if (!Array.isArray(item.follow_up_questions)) throw new Error('contract')
  return {
    preview_id: string(item.preview_id),
    operation_id: string(item.operation_id),
    state: string(item.state) as ModelOperation['state'],
    physical_request_count: number(item.physical_request_count),
    error_category: nullableString(item.error_category),
    draft_text: nullableString(item.draft_text),
    response_kind: item.response_kind === null
      ? null
      : string(item.response_kind) as ModelOperation['response_kind'],
    follow_up_questions: item.follow_up_questions.map(string),
    proposal: item.proposal === null ? null : record(item.proposal),
    teacher_confirmation_required: boolean(item.teacher_confirmation_required),
  }
}

function headers(token: string): Record<string, string> {
  return {
    'x-class-teacher-client': CLIENT_HEADER,
    'x-class-teacher-session': token,
  }
}

export const modelApprovalApi = {
  prepare(token: string, sourceText: string, subjectId?: string) {
    return apiClient.request('/api/class-teacher/model/previews', {
      method: 'POST',
      headers: headers(token),
      body: {
        purpose: 'student_support_note',
        source_text: sourceText,
        subject_id: subjectId,
      },
      decode: preview,
    })
  },
  confirm(token: string, value: ModelPreview, operationId: string) {
    return apiClient.request(`/api/class-teacher/model/previews/${value.preview_id}/confirm`, {
      method: 'POST',
      headers: headers(token),
      body: { fingerprint: value.fingerprint, operation_id: operationId },
      decode: operation,
    })
  },
}
