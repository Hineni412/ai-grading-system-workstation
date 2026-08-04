import { apiClient } from '../../../api/client'

const CLIENT_HEADER = 'class-teacher-browser-v1'

export type HomeIntakeRoute = 'ordinary' | 'sensitive' | 'emergency'
export type HomeIntakeRecommendedRoute = 'ordinary_plan' | 'affair' | 'student_support'
export type HomeIntakeState =
  | 'succeeded'
  | 'needs_information'
  | 'unavailable'
  | 'invalid_result'
  | 'result_unknown'
  | 'in_progress'
  | 'destination_changed'
  | 'unsafe_output_suppressed'
  | 'failed_before_send'
export type HomeIntakeResultKind =
  | 'ordinary_plan'
  | 'affair_recommendation'
  | 'student_support_recommendation'
  | 'follow_up'
  | 'plain_text'

export interface HomeIntakeDateInterpretation {
  status: 'resolved' | 'conflict' | 'pending'
  source:
    | 'explicit_numeric'
    | 'relative_day'
    | 'relative_weekday'
    | 'selected_date'
    | 'multiple_or_selected_conflict'
    | 'incomplete_week'
    | 'not_provided'
  resolved_date: string | null
  selected_date: string | null
  candidates: string[]
  pending_reason: string | null
}

export interface HomeIntakeEmergencyGuidance {
  priority: 'before_ai'
  title: string
  steps: string[]
}

export interface HomeIntakePreview {
  preview_id: string
  route: HomeIntakeRoute
  recommended_route: HomeIntakeRecommendedRoute
  date_interpretation: HomeIntakeDateInterpretation
  emergency_guidance: HomeIntakeEmergencyGuidance | null
  round_number: number
  prior_operations: string[]
  round_physical_request_count: number
  cumulative_physical_request_count: number
  physical_request_count: number
  dispatch_ready: boolean
  local_only: boolean
  blocked_categories: string[]
  removed_categories: string[]
  student_aliases: string[]
  exact_payload: Record<string, unknown> | null
  fingerprint: string | null
  expires_at: string | null
  model_provider: string | null
  model_endpoint: string | null
  model_name: string | null
  destination_fingerprint: string | null
  model_enabled: boolean
  max_physical_requests: number | null
  estimated_cost: number | null
  source_text: string | null
  final_due_date: string | null
  date_semantics: string | null
}

export interface HomeIntakePlanNode {
  draft_key: string
  kind: string
  title: string
  details: string | null
  rationale: string | null
  status: 'pending' | 'waiting'
  due_date: string | null
}

export interface HomeIntakePlanEdge {
  source_draft_key: string
  target_draft_key: string
  relation: string
}

export interface HomeIntakeOrdinaryPlan {
  nodes: HomeIntakePlanNode[]
  edges: HomeIntakePlanEdge[]
  assumptions: string[]
}

export interface HomeIntakeRecommendation {
  kind: 'affair_recommendation' | 'student_support_recommendation'
  summary: string | null
  reasons: string[]
  assumptions: string[]
  transaction_type: string
  template_key: string
  title: string
  to_verify: string[]
  steps: HomeIntakeWorkflowStep[]
  edges: Array<{ source_key: string; target_key: string; relation: string }>
  calendar_items: HomeIntakeCalendarItem[]
  student_aliases: string[]
  risk_level: string
  emergency_prompt: string | null
  model_advice?: string | null
}

export interface HomeIntakeWorkflowStep {
  key: string
  title: string
  details: string
  depends_on: string[]
  required: boolean
  waivable: boolean
  safety_required: boolean
}

export interface HomeIntakeCalendarItem {
  key: string
  step_key: string
  title: string
  due_date: string | null
  depends_on: string[]
}

export type HomeIntakeDecodedResult =
  | { kind: 'ordinary_plan'; value: HomeIntakeOrdinaryPlan }
  | { kind: 'affair_recommendation' | 'student_support_recommendation'; value: HomeIntakeRecommendation }
  | { kind: 'plain_text'; value: { text: string } }
  | { kind: 'follow_up'; value: null }
  | null

export interface HomeIntakeOperation {
  operation_id: string
  route: HomeIntakeRoute
  state: HomeIntakeState
  result_kind: HomeIntakeResultKind | null
  result: HomeIntakeDecodedResult
  follow_up_questions: string[]
  can_follow_up: boolean
  assistant_message: string | null
  validation_issue: string | null
  error_category: string | null
  round_number: number
  round_physical_request_count: number
  cumulative_physical_request_count: number
  physical_request_count: number
  teacher_confirmation_required: boolean
  result_fingerprint: string | null
  local_context: Record<string, unknown>
  draft_id?: string | null
  draft_version?: number | null
  draft_saved_at?: string | null
  draft_persistence_error?: string | null
  draft_persistence_message?: string | null
  previous_result_preserved?: boolean
  preserved_result_kind?: 'ordinary_plan' | 'affair_recommendation' | 'student_support_recommendation' | null
  preserved_result?: HomeIntakeDecodedResult
}

export interface HomeIntakeDraftSummary {
  draft_id: string
  version: number
  route: HomeIntakeRoute
  result_kind: 'ordinary_plan' | 'affair_recommendation' | 'student_support_recommendation'
  title: string
  updated_at: string
}

export interface HomeIntakeDraft {
  draft_id: string
  version: number
  state: 'open' | 'adopting' | 'adopted' | 'discarded'
  route: HomeIntakeRoute
  result_kind: 'ordinary_plan' | 'affair_recommendation' | 'student_support_recommendation'
  source_operation_id: string
  content_fingerprint: string
  created_at: string
  updated_at: string
  adopted_affair_id: string | null
  operation: HomeIntakeOperation
}

export interface HomeIntakeHandoff {
  id: string
  destination: 'affair' | 'student_support'
  sourceText: string
  aiReference: HomeIntakeRecommendation
  route: HomeIntakeRoute
  interpretedDate: string | null
}

export interface HomeIntakeManualFallback {
  created: boolean
  manual_fallback: true
  source_operation_id: string
  node: {
    node_id: string
    kind: string
    classification: string
    title: string
    details: string | null
    status: string
    due_date: string | null
    revision: number
    created_at: string
    updated_at: string
    projection_type: string | null
  }
  physical_request_count: 0
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('home intake contract')
  return value as Record<string, unknown>
}
function string(value: unknown): string {
  if (typeof value !== 'string') throw new Error('home intake contract')
  return value
}
function nullableString(value: unknown): string | null {
  if (value === null || value === undefined) return null
  return string(value)
}
function number(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) throw new Error('home intake contract')
  return value
}
function boolean(value: unknown): boolean {
  if (typeof value !== 'boolean') throw new Error('home intake contract')
  return value
}
function strings(value: unknown): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== 'string')) throw new Error('home intake contract')
  return [...value]
}
function literal<T extends string>(value: unknown, allowed: readonly T[]): T {
  const decoded = string(value)
  if (!allowed.includes(decoded as T)) throw new Error('home intake contract')
  return decoded as T
}

const routes = ['ordinary', 'sensitive', 'emergency'] as const
const recommendedRoutes = ['ordinary_plan', 'affair', 'student_support'] as const
const states = ['succeeded', 'needs_information', 'unavailable', 'invalid_result', 'result_unknown', 'in_progress', 'destination_changed', 'unsafe_output_suppressed', 'failed_before_send'] as const
const kinds = ['ordinary_plan', 'affair_recommendation', 'student_support_recommendation', 'follow_up', 'plain_text'] as const

function dateInterpretation(value: unknown): HomeIntakeDateInterpretation {
  const item = record(value)
  return {
    status: literal(item.status, ['resolved', 'conflict', 'pending'] as const),
    source: literal(item.source, ['explicit_numeric', 'relative_day', 'relative_weekday', 'selected_date', 'multiple_or_selected_conflict', 'incomplete_week', 'not_provided'] as const),
    resolved_date: nullableString(item.resolved_date),
    selected_date: nullableString(item.selected_date),
    candidates: strings(item.candidates),
    pending_reason: nullableString(item.pending_reason),
  }
}

function emergencyGuidance(value: unknown): HomeIntakeEmergencyGuidance | null {
  if (value === null) return null
  const item = record(value)
  return { priority: literal(item.priority, ['before_ai'] as const), title: string(item.title), steps: strings(item.steps) }
}

function preview(value: unknown): HomeIntakePreview {
  const item = record(value)
  const exactPayload = item.exact_payload === null ? null : record(item.exact_payload)
  const fingerprint = nullableString(item.fingerprint)
  const dispatchReady = boolean(item.dispatch_ready)
  if (dispatchReady && (!exactPayload || !fingerprint)) throw new Error('home intake contract')
  return {
    preview_id: string(item.preview_id),
    route: literal(item.route, routes),
    recommended_route: literal(item.recommended_route, recommendedRoutes),
    date_interpretation: dateInterpretation(item.date_interpretation),
    emergency_guidance: emergencyGuidance(item.emergency_guidance),
    round_number: number(item.round_number),
    prior_operations: strings(item.prior_operations),
    round_physical_request_count: number(item.round_physical_request_count),
    cumulative_physical_request_count: number(item.cumulative_physical_request_count),
    physical_request_count: number(item.physical_request_count),
    dispatch_ready: dispatchReady,
    local_only: boolean(item.local_only),
    blocked_categories: strings(item.blocked_categories),
    removed_categories: strings(item.removed_categories),
    student_aliases: strings(item.student_aliases),
    exact_payload: exactPayload,
    fingerprint,
    expires_at: nullableString(item.expires_at),
    model_provider: nullableString(item.model_provider),
    model_endpoint: nullableString(item.model_endpoint),
    model_name: nullableString(item.model_name),
    destination_fingerprint: nullableString(item.destination_fingerprint),
    model_enabled: boolean(item.model_enabled),
    max_physical_requests: item.max_physical_requests === null || item.max_physical_requests === undefined ? null : number(item.max_physical_requests),
    estimated_cost: item.estimated_cost === null || item.estimated_cost === undefined ? null : number(item.estimated_cost),
    source_text: nullableString(item.source_text),
    final_due_date: nullableString(item.final_due_date),
    date_semantics: nullableString(item.date_semantics),
  }
}

function planNode(value: unknown): HomeIntakePlanNode {
  const item = record(value)
  return {
    draft_key: string(item.draft_key),
    kind: string(item.kind),
    title: string(item.title),
    details: nullableString(item.details),
    rationale: nullableString(item.rationale),
    status: literal(item.status, ['pending', 'waiting'] as const),
    due_date: nullableString(item.due_date),
  }
}
function planEdge(value: unknown): HomeIntakePlanEdge {
  const item = record(value)
  return { source_draft_key: string(item.source_draft_key), target_draft_key: string(item.target_draft_key), relation: string(item.relation) }
}
function recommendation(value: unknown, kind: 'affair_recommendation' | 'student_support_recommendation'): HomeIntakeRecommendation {
  const item = record(value)
  const returnedKind = literal(item.kind, ['affair_recommendation', 'student_support_recommendation'] as const)
  if (returnedKind !== kind) throw new Error('home intake contract')
  return {
    kind,
    summary: nullableString(item.summary),
    reasons: item.reasons === undefined ? [] : strings(item.reasons),
    assumptions: item.assumptions === undefined ? [] : strings(item.assumptions),
    transaction_type: string(item.transaction_type),
    template_key: string(item.template_key),
    title: string(item.title),
    to_verify: item.to_verify === undefined ? [] : strings(item.to_verify),
    steps: Array.isArray(item.steps) ? item.steps.map((raw) => {
      const step = record(raw)
      return { key: string(step.key), title: string(step.title), details: string(step.details), depends_on: strings(step.depends_on), required: boolean(step.required), waivable: boolean(step.waivable), safety_required: boolean(step.safety_required) }
    }) : [],
    edges: Array.isArray(item.edges) ? item.edges.map((raw) => { const edge = record(raw); return { source_key: string(edge.source_key), target_key: string(edge.target_key), relation: string(edge.relation) } }) : [],
    calendar_items: Array.isArray(item.calendar_items) ? item.calendar_items.map((raw) => { const calendar = record(raw); return { key: string(calendar.key), step_key: string(calendar.step_key), title: string(calendar.title), due_date: nullableString(calendar.due_date), depends_on: strings(calendar.depends_on) } }) : [],
    student_aliases: item.student_aliases === undefined ? [] : strings(item.student_aliases),
    risk_level: string(item.risk_level),
    emergency_prompt: nullableString(item.emergency_prompt),
    model_advice: nullableString(item.model_advice),
  }
}
function decodedResult(kind: HomeIntakeResultKind | null, value: unknown): HomeIntakeDecodedResult {
  if (kind === null) {
    if (value !== null) throw new Error('home intake contract')
    return null
  }
  if (kind === 'follow_up') {
    if (value !== null) throw new Error('home intake contract')
    return { kind, value: null }
  }
  if (kind === 'ordinary_plan') {
    const item = record(value)
    if (!Array.isArray(item.nodes) || !Array.isArray(item.edges)) throw new Error('home intake contract')
    return { kind, value: { nodes: item.nodes.map(planNode), edges: item.edges.map(planEdge), assumptions: strings(item.assumptions) } }
  }
  if (kind === 'plain_text') return { kind, value: { text: string(record(value).text) } }
  return { kind, value: recommendation(value, kind) }
}

function operation(value: unknown): HomeIntakeOperation {
  const item = record(value)
  const kind = item.result_kind === null ? null : literal(item.result_kind, kinds)
  const roundCount = number(item.round_physical_request_count)
  const physicalCount = number(item.physical_request_count)
  if (roundCount !== physicalCount) throw new Error('home intake contract')
  const preservedKind = item.preserved_result_kind === null || item.preserved_result_kind === undefined
    ? null
    : literal(item.preserved_result_kind, ['ordinary_plan', 'affair_recommendation', 'student_support_recommendation'] as const)
  return {
    operation_id: string(item.operation_id),
    route: literal(item.route, routes),
    state: literal(item.state, states),
    result_kind: kind,
    result: decodedResult(kind, item.result),
    follow_up_questions: strings(item.follow_up_questions),
    can_follow_up: boolean(item.can_follow_up),
    assistant_message: nullableString(item.assistant_message),
    validation_issue: nullableString(item.validation_issue),
    error_category: nullableString(item.error_category),
    round_number: number(item.round_number),
    round_physical_request_count: roundCount,
    cumulative_physical_request_count: number(item.cumulative_physical_request_count),
    physical_request_count: physicalCount,
    teacher_confirmation_required: boolean(item.teacher_confirmation_required),
    result_fingerprint: nullableString(item.result_fingerprint),
    local_context: record(item.local_context),
    draft_id: nullableString(item.draft_id),
    draft_version: item.draft_version === null || item.draft_version === undefined ? null : number(item.draft_version),
    draft_saved_at: nullableString(item.draft_saved_at),
    draft_persistence_error: nullableString(item.draft_persistence_error),
    draft_persistence_message: nullableString(item.draft_persistence_message),
    previous_result_preserved: item.previous_result_preserved === undefined ? false : boolean(item.previous_result_preserved),
    preserved_result_kind: preservedKind,
    preserved_result: preservedKind === null ? null : decodedResult(preservedKind, item.preserved_result),
  }
}

function draftSummary(value: unknown): HomeIntakeDraftSummary {
  const item = record(value)
  return {
    draft_id: string(item.draft_id),
    version: number(item.version),
    route: literal(item.route, routes),
    result_kind: literal(item.result_kind, ['ordinary_plan', 'affair_recommendation', 'student_support_recommendation'] as const),
    title: string(item.title),
    updated_at: string(item.updated_at),
  }
}

function draft(value: unknown): HomeIntakeDraft {
  const item = record(value)
  return {
    draft_id: string(item.draft_id),
    version: number(item.version),
    state: literal(item.state, ['open', 'adopting', 'adopted', 'discarded'] as const),
    route: literal(item.route, routes),
    result_kind: literal(item.result_kind, ['ordinary_plan', 'affair_recommendation', 'student_support_recommendation'] as const),
    source_operation_id: string(item.source_operation_id),
    content_fingerprint: string(item.content_fingerprint),
    created_at: string(item.created_at),
    updated_at: string(item.updated_at),
    adopted_affair_id: nullableString(item.adopted_affair_id),
    operation: operation(item.operation),
  }
}

function manualFallback(value: unknown): HomeIntakeManualFallback {
  const item = record(value)
  const rawNode = record(item.node)
  const count = number(item.physical_request_count)
  if (item.manual_fallback !== true || count !== 0) throw new Error('home intake contract')
  return {
    created: boolean(item.created),
    manual_fallback: true,
    source_operation_id: string(item.source_operation_id),
    node: {
      node_id: string(rawNode.node_id), kind: string(rawNode.kind), classification: string(rawNode.classification),
      title: string(rawNode.title), details: nullableString(rawNode.details), status: string(rawNode.status),
      due_date: nullableString(rawNode.due_date), revision: number(rawNode.revision), created_at: string(rawNode.created_at),
      updated_at: string(rawNode.updated_at), projection_type: nullableString(rawNode.projection_type),
    },
    physical_request_count: 0,
  }
}

function headers(token?: string): Record<string, string> {
  return {
    'x-class-teacher-client': CLIENT_HEADER,
    ...(token ? { 'x-class-teacher-session': token } : {}),
  }
}

export const homeIntakeApi = {
  preview(text: string, token?: string, referenceDate?: string) {
    return apiClient.request('/api/class-teacher/home/intake/previews', {
      method: 'POST', headers: headers(token),
      body: { text, due_date: null, reference_date: referenceDate ?? null }, decode: preview,
    })
  },
  dispatch(value: HomeIntakePreview, operationId: string, token?: string) {
    if (!value.fingerprint) throw new Error('home intake fingerprint required')
    return apiClient.request(`/api/class-teacher/home/intake/previews/${value.preview_id}/dispatch`, {
      method: 'POST', headers: headers(token), body: { fingerprint: value.fingerprint, operation_id: operationId },
      decode: operation, timeoutMs: 125_000,
    })
  },
  status(operationId: string, token?: string) {
    return apiClient.request(`/api/class-teacher/home/intake/operations/${operationId}`, {
      headers: token ? { 'x-class-teacher-session': token } : undefined, decode: operation,
    })
  },
  listDrafts(token?: string) {
    return apiClient.request('/api/class-teacher/home/intake/drafts', {
      headers: headers(token), decode: (value) => {
        const item = record(value)
        if (!Array.isArray(item.items)) throw new Error('home intake contract')
        return item.items.map(draftSummary)
      },
    })
  },
  getDraft(draftId: string, token: string) {
    return apiClient.request(`/api/class-teacher/home/intake/drafts/${draftId}`, {
      headers: headers(token), decode: draft,
    })
  },
  discardDraft(draftId: string, expectedVersion: number, token: string) {
    return apiClient.request(`/api/class-teacher/home/intake/drafts/${draftId}/discard`, {
      method: 'POST', headers: headers(token), body: { expected_version: expectedVersion }, decode: record,
    })
  },
  adoptOrdinaryDraft(value: HomeIntakeOperation, token?: string) {
    if (!value.draft_id || !value.draft_version || !value.result_fingerprint) throw new Error('home intake draft required')
    return apiClient.request(`/api/class-teacher/home/intake/drafts/${value.draft_id}/adopt-ordinary`, {
      method: 'POST', headers: headers(token), body: {
        expected_version: value.draft_version,
        source_operation_id: value.operation_id,
        result_fingerprint: value.result_fingerprint,
      }, decode: record,
    })
  },
  followUpPreview(operationId: string, answer: string, token?: string, referenceDate?: string, selectedStepKeys: string[] = [], selectedCalendarKeys: string[] = []) {
    return apiClient.request(`/api/class-teacher/home/intake/operations/${operationId}/follow-up-previews`, {
      method: 'POST', headers: headers(token), body: { answer, reference_date: referenceDate ?? null, selected_step_keys: selectedStepKeys, selected_calendar_keys: selectedCalendarKeys }, decode: preview,
    })
  },
  confirmPlan(value: HomeIntakeOperation, operationId: string) {
    if (value.result_kind !== 'ordinary_plan' || !value.result_fingerprint) throw new Error('home intake plan fingerprint required')
    return apiClient.request('/api/class-teacher/work/plans/confirm', {
      method: 'POST', headers: headers(), body: {
        model_operation_id: value.operation_id, plan_fingerprint: value.result_fingerprint, operation_id: operationId,
      }, decode: record,
    })
  },
  confirmManualFallback(sourceOperationId: string, operationId: string, title?: string | null, token?: string) {
    return apiClient.request('/api/class-teacher/home/intake/manual-fallback/confirm', {
      method: 'POST', headers: headers(token), body: {
        source_operation_id: sourceOperationId, operation_id: operationId, title: title || null, due_date: null,
      }, decode: manualFallback,
    })
  },
  adopt(value: HomeIntakeOperation, operationId: string, subjectIds: string[], token: string) {
    if (!value.result_fingerprint) throw new Error('home intake result fingerprint required')
    return apiClient.request('/api/class-teacher/home/intake/adopt', {
      method: 'POST', headers: headers(token), body: {
        source_operation_id: value.operation_id,
        operation_id: operationId,
        result_fingerprint: value.result_fingerprint,
        subject_ids: subjectIds,
        draft_id: value.draft_id,
        draft_version: value.draft_version,
      }, decode: record,
    })
  },
}
