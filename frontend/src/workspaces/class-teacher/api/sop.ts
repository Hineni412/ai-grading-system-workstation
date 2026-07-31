import { apiClient } from '../../../api/client'

const CLIENT_HEADER = 'class-teacher-browser-v1'

export interface SopTemplate {
  template_version_id: string
  revision: number
  template_key: string
  version: number
  title: string
  steps: SopStepDefinition[]
  workflow_scope: 'personal_checklist' | 'school_confirmed'
  risk_level: 'ordinary' | 'elevated' | 'emergency'
  emergency_prompt: string | null
  school_config_gaps: string[]
  model_enabled: boolean
  physical_request_count: number
  frozen: boolean
  created_at: string
}

export interface DecisionOption {
  value: string
  label: string
}

export interface CommunicationTemplate {
  kind: string
  audience: string
  content: string
  basis: string
  unknowns: string[]
  status: 'unsent'
}

export interface SopStepDefinition {
  key: string
  title: string
  details: string | null
  required: boolean
  waivable: boolean
  safety_required: boolean
  depends_on: string[]
  activation: Record<string, unknown> | null
  decision_key: string | null
  decision_prompt: string | null
  decision_options: DecisionOption[]
  communication_templates: CommunicationTemplate[]
}

export interface AffairStep extends SopStepDefinition {
  step_instance_id: string
  revision: number
  action_id: string | null
  state: 'blocked' | 'ready' | 'in_progress' | 'waiting' | 'completed' | 'waived' | 'superseded'
  result: string | null
  completed_at: string | null
}

export interface AffairDecision {
  decision_id: string
  revision: number
  decision_kind: 'teacher' | 'school' | 'ai_suggestion'
  step_instance_id: string | null
  summary: string
  decision_key: string | null
  selected_option: string | null
  can_drive_high_impact_branch: boolean
  created_at: string
}

export interface Affair {
  affair_id: string
  revision: number
  template_version_id: string
  plan_id: string
  title: string
  summary: string | null
  state: 'active' | 'closed'
  template_key: string
  template_version: number
  workflow_scope: 'personal_checklist' | 'school_confirmed'
  risk_level: 'ordinary' | 'elevated' | 'emergency'
  emergency_prompt: string | null
  school_config_gaps: string[]
  model_enabled: boolean
  physical_request_count: number
  current_occurrence_sequence: number
  closure_summary: string | null
  occurrence_id: string
  occurrence_sequence: number
  participants: Array<{ participant_id: string; reference: string }>
  current_steps: AffairStep[]
  completed_steps: AffairStep[]
  preview_steps: AffairStep[]
  decisions: AffairDecision[]
  created_at: string
  updated_at: string
  closed_at: string | null
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

function option(payload: unknown): DecisionOption {
  const value = record(payload)
  return { value: string(value.value), label: string(value.label) }
}

function communication(payload: unknown): CommunicationTemplate {
  const value = record(payload)
  return {
    kind: string(value.kind),
    audience: string(value.audience),
    content: string(value.content),
    basis: string(value.basis),
    unknowns: stringArray(value.unknowns),
    status: string(value.status) as 'unsent',
  }
}

function definition(payload: unknown): SopStepDefinition {
  const value = record(payload)
  if (!Array.isArray(value.decision_options) || !Array.isArray(value.communication_templates)) {
    throw new Error('contract')
  }
  return {
    key: string(value.key),
    title: string(value.title),
    details: nullableString(value.details),
    required: boolean(value.required),
    waivable: boolean(value.waivable),
    safety_required: boolean(value.safety_required),
    depends_on: stringArray(value.depends_on),
    activation: value.activation === null ? null : record(value.activation),
    decision_key: nullableString(value.decision_key),
    decision_prompt: nullableString(value.decision_prompt),
    decision_options: value.decision_options.map(option),
    communication_templates: value.communication_templates.map(communication),
  }
}

function template(payload: unknown): SopTemplate {
  const value = record(payload)
  if (!Array.isArray(value.steps)) throw new Error('contract')
  return {
    template_version_id: string(value.template_version_id),
    revision: number(value.revision),
    template_key: string(value.template_key),
    version: number(value.version),
    title: string(value.title),
    steps: value.steps.map(definition),
    workflow_scope: string(value.workflow_scope) as SopTemplate['workflow_scope'],
    risk_level: string(value.risk_level) as SopTemplate['risk_level'],
    emergency_prompt: nullableString(value.emergency_prompt),
    school_config_gaps: stringArray(value.school_config_gaps),
    model_enabled: boolean(value.model_enabled),
    physical_request_count: number(value.physical_request_count),
    frozen: boolean(value.frozen),
    created_at: string(value.created_at),
  }
}

function affairStep(payload: unknown): AffairStep {
  const value = record(payload)
  return {
    ...definition(value),
    step_instance_id: string(value.step_instance_id),
    revision: number(value.revision),
    action_id: nullableString(value.action_id),
    state: string(value.state) as AffairStep['state'],
    result: nullableString(value.result),
    completed_at: nullableString(value.completed_at),
  }
}

function decision(payload: unknown): AffairDecision {
  const value = record(payload)
  return {
    decision_id: string(value.decision_id),
    revision: number(value.revision),
    decision_kind: string(value.decision_kind) as AffairDecision['decision_kind'],
    step_instance_id: nullableString(value.step_instance_id),
    summary: string(value.summary),
    decision_key: nullableString(value.decision_key),
    selected_option: nullableString(value.selected_option),
    can_drive_high_impact_branch: boolean(value.can_drive_high_impact_branch),
    created_at: string(value.created_at),
  }
}

function affair(payload: unknown): Affair {
  const value = record(payload)
  if (
    !Array.isArray(value.participants)
    || !Array.isArray(value.current_steps)
    || !Array.isArray(value.completed_steps)
    || !Array.isArray(value.preview_steps)
    || !Array.isArray(value.decisions)
  ) throw new Error('contract')
  return {
    affair_id: string(value.affair_id),
    revision: number(value.revision),
    template_version_id: string(value.template_version_id),
    plan_id: string(value.plan_id),
    title: string(value.title),
    summary: nullableString(value.summary),
    state: string(value.state) as Affair['state'],
    template_key: string(value.template_key),
    template_version: number(value.template_version),
    workflow_scope: string(value.workflow_scope) as Affair['workflow_scope'],
    risk_level: string(value.risk_level) as Affair['risk_level'],
    emergency_prompt: nullableString(value.emergency_prompt),
    school_config_gaps: stringArray(value.school_config_gaps),
    model_enabled: boolean(value.model_enabled),
    physical_request_count: number(value.physical_request_count),
    current_occurrence_sequence: number(value.current_occurrence_sequence),
    closure_summary: nullableString(value.closure_summary),
    occurrence_id: string(value.occurrence_id),
    occurrence_sequence: number(value.occurrence_sequence),
    participants: value.participants.map((item) => {
      const entry = record(item)
      return {
        participant_id: string(entry.participant_id),
        reference: string(entry.reference),
      }
    }),
    current_steps: value.current_steps.map(affairStep),
    completed_steps: value.completed_steps.map(affairStep),
    preview_steps: value.preview_steps.map(affairStep),
    decisions: value.decisions.map(decision),
    created_at: string(value.created_at),
    updated_at: string(value.updated_at),
    closed_at: nullableString(value.closed_at),
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

export const sopApi = {
  ensureBaselines(token: string) {
    return apiClient.request('/api/class-teacher/sop/baselines/ensure', {
      method: 'POST',
      headers: headers(token),
      body: {},
      decode: (payload) => {
        const value = record(payload)
        if (!Array.isArray(value.items)) throw new Error('contract')
        return value.items.map(template)
      },
    })
  },
  listTemplates(token: string) {
    return apiClient.request('/api/class-teacher/sop/templates', {
      headers: { 'x-class-teacher-session': token },
      decode: (payload) => {
        const value = record(payload)
        if (!Array.isArray(value.items)) throw new Error('contract')
        return value.items.map(template)
      },
    })
  },
  listAffairs(token: string) {
    return apiClient.request('/api/class-teacher/sop/affairs', {
      headers: { 'x-class-teacher-session': token },
      decode: (payload) => {
        const value = record(payload)
        if (!Array.isArray(value.items)) throw new Error('contract')
        return value.items.map(affair)
      },
    })
  },
  createAffair(
    token: string,
    input: {
      template_version_id: string
      title: string
      summary: string | null
      participant_refs: string[]
    },
  ) {
    return apiClient.request('/api/class-teacher/sop/affairs', {
      method: 'POST',
      headers: headers(token),
      body: { ...input, operation_id: operationId() },
      decode: affair,
    })
  },
  completeStep(
    token: string,
    value: Affair,
    step: AffairStep,
    result: string,
    outcome: 'completed' | 'waived' = 'completed',
  ) {
    return apiClient.request(
      `/api/class-teacher/sop/affairs/${value.affair_id}/steps/${step.step_instance_id}/complete`,
      {
        method: 'POST',
        headers: headers(token),
        body: {
          operation_id: operationId(),
          revision: step.revision,
          outcome,
          result,
        },
        decode: affair,
      },
    )
  },
  recordDecision(
    token: string,
    value: Affair,
    step: AffairStep,
    selectedOption: string,
  ) {
    const label = step.decision_options.find((item) => item.value === selectedOption)?.label
      ?? selectedOption
    return apiClient.request(`/api/class-teacher/sop/affairs/${value.affair_id}/decisions`, {
      method: 'POST',
      headers: headers(token),
      body: {
        operation_id: operationId(),
        decision_kind: 'teacher',
        summary: `教师选择：${label}`,
        step_instance_id: step.step_instance_id,
        decision_key: step.decision_key,
        selected_option: selectedOption,
      },
      decode: affair,
    })
  },
  closeAffair(token: string, value: Affair, summary: string) {
    return apiClient.request(`/api/class-teacher/sop/affairs/${value.affair_id}/close`, {
      method: 'POST',
      headers: headers(token),
      body: { operation_id: operationId(), revision: value.revision, closure_summary: summary },
      decode: affair,
    })
  },
  reopenAffair(token: string, value: Affair, reason: string) {
    return apiClient.request(`/api/class-teacher/sop/affairs/${value.affair_id}/reopen`, {
      method: 'POST',
      headers: headers(token),
      body: { operation_id: operationId(), revision: value.revision, reason },
      decode: affair,
    })
  },
}
