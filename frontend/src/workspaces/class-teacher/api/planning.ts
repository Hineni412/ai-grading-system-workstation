import { apiClient } from '../../../api/client'

const CLIENT_HEADER = 'class-teacher-browser-v1'

export interface PlanningAction {
  draft_action_id: string
  title: string
  details: string | null
  due_at: string | null
  depends_on_draft_action_ids: string[]
}

export interface CommunicationDraft {
  audience: string
  content: string
  basis: string
  unknowns: string[]
  status: 'unsent'
  sent_at: null
}

export interface PlanningDraft {
  draft_id: string
  revision: number
  status: 'draft' | 'cancelled' | 'confirmed'
  raw_input: string
  reference_at: string
  template_kind: string
  plan_title: string
  deadline_date: string | null
  final_deadline: string | null
  deadline_source: string
  actions: PlanningAction[]
  communication_drafts: CommunicationDraft[]
  sensitive_findings: string[]
  unknowns: string[]
  is_late: boolean
  send_preview: {
    model_enabled: boolean
    ready_to_send: boolean
    sent: boolean
    physical_request_count: number
    summary: string
    excluded_field_kinds: string[]
  }
  confirmed_plan_id: string | null
  confirmed_action_ids: string[]
  created_at: string
  updated_at: string
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

function stringArray(value: unknown): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== 'string')) {
    throw new Error('contract')
  }
  return [...value]
}

function planningAction(payload: unknown): PlanningAction {
  const value = record(payload)
  return {
    draft_action_id: string(value.draft_action_id),
    title: string(value.title),
    details: nullableString(value.details),
    due_at: nullableString(value.due_at),
    depends_on_draft_action_ids: stringArray(value.depends_on_draft_action_ids),
  }
}

function communication(payload: unknown): CommunicationDraft {
  const value = record(payload)
  return {
    audience: string(value.audience),
    content: string(value.content),
    basis: string(value.basis),
    unknowns: stringArray(value.unknowns),
    status: string(value.status) as 'unsent',
    sent_at: null,
  }
}

function draft(payload: unknown): PlanningDraft {
  const value = record(payload)
  const preview = record(value.send_preview)
  if (!Array.isArray(value.actions) || !Array.isArray(value.communication_drafts)) {
    throw new Error('contract')
  }
  return {
    draft_id: string(value.draft_id),
    revision: number(value.revision),
    status: string(value.status) as PlanningDraft['status'],
    raw_input: string(value.raw_input),
    reference_at: string(value.reference_at),
    template_kind: string(value.template_kind),
    plan_title: string(value.plan_title),
    deadline_date: nullableString(value.deadline_date),
    final_deadline: nullableString(value.final_deadline),
    deadline_source: string(value.deadline_source),
    actions: value.actions.map(planningAction),
    communication_drafts: value.communication_drafts.map(communication),
    sensitive_findings: stringArray(value.sensitive_findings),
    unknowns: stringArray(value.unknowns),
    is_late: boolean(value.is_late),
    send_preview: {
      model_enabled: boolean(preview.model_enabled),
      ready_to_send: boolean(preview.ready_to_send),
      sent: boolean(preview.sent),
      physical_request_count: number(preview.physical_request_count),
      summary: string(preview.summary),
      excluded_field_kinds: stringArray(preview.excluded_field_kinds),
    },
    confirmed_plan_id: nullableString(value.confirmed_plan_id),
    confirmed_action_ids: stringArray(value.confirmed_action_ids),
    created_at: string(value.created_at),
    updated_at: string(value.updated_at),
  }
}

function headers(token: string): Record<string, string> {
  return {
    'x-class-teacher-session': token,
    'x-class-teacher-client': CLIENT_HEADER,
  }
}

function operationId(): string {
  return globalThis.crypto.randomUUID()
}

export const planningApi = {
  list(token: string) {
    return apiClient.request('/api/class-teacher/planning/drafts', {
      headers: { 'x-class-teacher-session': token },
      decode: (payload) => {
        const value = record(payload)
        if (!Array.isArray(value.items)) throw new Error('contract')
        return value.items.map(draft)
      },
    })
  },
  create(token: string, rawInput: string, finalDeadline: string | null) {
    return apiClient.request('/api/class-teacher/planning/drafts', {
      method: 'POST',
      headers: headers(token),
      body: {
        operation_id: operationId(),
        raw_input: rawInput,
        reference_at: new Date().toISOString(),
        final_deadline: finalDeadline,
      },
      decode: draft,
    })
  },
  cancel(token: string, value: PlanningDraft) {
    return apiClient.request(`/api/class-teacher/planning/drafts/${value.draft_id}/cancel`, {
      method: 'POST',
      headers: headers(token),
      body: { operation_id: operationId(), revision: value.revision },
      decode: draft,
    })
  },
  confirm(token: string, value: PlanningDraft) {
    return apiClient.request(`/api/class-teacher/planning/drafts/${value.draft_id}/confirm`, {
      method: 'POST',
      headers: headers(token),
      body: {
        operation_id: operationId(),
        revision: value.revision,
        plan_title: value.plan_title,
        actions: value.actions,
      },
      decode: record,
    })
  },
}
