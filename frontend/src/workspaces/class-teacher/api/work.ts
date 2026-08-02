import { apiClient } from '../../../api/client'

const CLIENT_HEADER = 'class-teacher-browser-v1'

export type WorkKind =
  | 'goal'
  | 'task'
  | 'waiting'
  | 'decision'
  | 'collection'
  | 'communication'
  | 'sop'
  | 'restricted_projection'

export type WorkStatus =
  | 'draft'
  | 'pending'
  | 'in_progress'
  | 'waiting'
  | 'completed'
  | 'cancelled'

export interface WorkNode {
  node_id: string
  kind: WorkKind
  classification: 'ordinary' | 'restricted_projection'
  title: string
  details: string | null
  status: WorkStatus
  due_date: string | null
  revision: number
  created_at: string
  updated_at: string
  projection_type?: 'sensitive_affair' | 'attention_followup' | 'student_support' | null
}

export interface WorkPlanNode {
  draft_key: string
  kind: WorkKind
  title: string
  details: string | null
  status: 'pending' | 'waiting'
  due_date: string | null
}

export interface WorkPlanPreview {
  preview_id: string
  source_text: string
  final_due_date: string | null
  date_semantics: 'date-only'
  exact_payload: Record<string, unknown>
  fingerprint: string
  expires_at: string
  model_provider: string | null
  model_endpoint: string | null
  model_name: string
  destination_fingerprint: string
  model_enabled: boolean
  max_physical_requests: 1
  physical_request_count: number
}

export interface WorkPlanEdge {
  source_draft_key: string
  target_draft_key: string
  relation: 'contains' | 'depends_on' | 'next'
}

export interface WorkPlan {
  nodes: WorkPlanNode[]
  edges: WorkPlanEdge[]
  assumptions: string[]
}

export type WorkPlanState =
  | 'succeeded'
  | 'needs_information'
  | 'unavailable'
  | 'invalid_result'
  | 'result_unknown'
  | 'in_progress'
  | 'destination_changed'

export interface WorkPlanResult {
  preview_id: string
  operation_id: string
  state: WorkPlanState
  physical_request_count: number
  error_category: string | null
  questions: string[]
  assumptions: string[]
  plan: WorkPlan | null
  plan_fingerprint: string | null
  teacher_confirmation_required: boolean
  local_context: Record<string, unknown>
}

export interface WorkSnapshot {
  as_of: string
  start_date: string | null
  end_date: string | null
  nodes: WorkNode[]
  edges: Array<{
    source_node_id: string
    target_node_id: string
    relation: 'contains' | 'depends_on' | 'next' | 'review_of'
  }>
  today: WorkNode[]
  overdue: WorkNode[]
  waiting: WorkNode[]
  review_due?: WorkNode[]
  summary?: { today: number; overdue: number; waiting: number; review_due: number }
  view?: 'today' | 'week' | 'timeline' | 'all'
  cursor?: string | null
  source_version?: string
}

export type WorkView = NonNullable<WorkSnapshot['view']>

export interface WorkNodeDetail {
  node: WorkNode
  upstream: WorkNode[]
  downstream: WorkNode[]
  progress_events: Array<Record<string, unknown>>
  collection_summary: { expected_count: number; received_count: number; needs_review_count: number } | null
  pending_ai_branches?: Array<{
    operation_id: string
    assumptions: string[]
    nodes: Array<{ title: string; due_date: string | null }>
  }>
  allowed_commands: string[]
  projection_id: string | null
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

function node(value: unknown): WorkNode {
  const item = record(value)
  return {
    node_id: string(item.node_id),
    kind: string(item.kind) as WorkKind,
    classification: string(item.classification) as WorkNode['classification'],
    title: string(item.title),
    details: nullableString(item.details),
    status: string(item.status) as WorkStatus,
    due_date: nullableString(item.due_date),
    revision: number(item.revision),
    created_at: string(item.created_at),
    updated_at: string(item.updated_at),
    projection_type: item.projection_type === null || item.projection_type === undefined
      ? null
      : string(item.projection_type) as WorkNode['projection_type'],
  }
}

function planNode(value: unknown): WorkPlanNode {
  const item = record(value)
  return {
    draft_key: string(item.draft_key),
    kind: string(item.kind) as WorkKind,
    title: string(item.title),
    details: nullableString(item.details),
    status: string(item.status) as WorkPlanNode['status'],
    due_date: nullableString(item.due_date),
  }
}

function planPreview(value: unknown): WorkPlanPreview {
  const item = record(value)
  return {
    preview_id: string(item.preview_id),
    source_text: string(item.source_text),
    final_due_date: nullableString(item.final_due_date),
    date_semantics: string(item.date_semantics) as 'date-only',
    exact_payload: record(item.exact_payload),
    fingerprint: string(item.fingerprint),
    expires_at: string(item.expires_at),
    model_provider: nullableString(item.model_provider),
    model_endpoint: nullableString(item.model_endpoint),
    model_name: string(item.model_name),
    destination_fingerprint: string(item.destination_fingerprint),
    model_enabled: boolean(item.model_enabled),
    max_physical_requests: number(item.max_physical_requests) as 1,
    physical_request_count: number(item.physical_request_count),
  }
}

function snapshot(value: unknown): WorkSnapshot {
  const item = record(value)
  if (
    !Array.isArray(item.nodes)
    || !Array.isArray(item.edges)
    || !Array.isArray(item.today)
    || !Array.isArray(item.overdue)
    || !Array.isArray(item.waiting)
  ) throw new Error('contract')
  return {
    as_of: string(item.as_of),
    start_date: nullableString(item.start_date),
    end_date: nullableString(item.end_date),
    nodes: item.nodes.map(node),
    edges: item.edges.map((value) => {
      const edge = record(value)
      return {
        source_node_id: string(edge.source_node_id),
        target_node_id: string(edge.target_node_id),
        relation: string(edge.relation) as WorkSnapshot['edges'][number]['relation'],
      }
    }),
    today: item.today.map(node),
    overdue: item.overdue.map(node),
    waiting: item.waiting.map(node),
    review_due: Array.isArray(item.review_due) ? item.review_due.map(node) : [],
    summary: record(item.summary) as unknown as WorkSnapshot['summary'],
    view: string(item.view) as WorkSnapshot['view'],
    cursor: nullableString(item.cursor),
    source_version: string(item.source_version),
  }
}

function nodeDetail(value: unknown): WorkNodeDetail {
  const item = record(value)
  if (!Array.isArray(item.upstream) || !Array.isArray(item.downstream)
    || !Array.isArray(item.progress_events) || !Array.isArray(item.allowed_commands)) {
    throw new Error('contract')
  }
  return {
    node: node(item.node),
    upstream: item.upstream.map(node),
    downstream: item.downstream.map(node),
    progress_events: item.progress_events.map(record),
    collection_summary: item.collection_summary === null
      ? null
      : record(item.collection_summary) as unknown as WorkNodeDetail['collection_summary'],
    pending_ai_branches: (Array.isArray(item.pending_ai_branches) ? item.pending_ai_branches : []).map((value) => {
      const branch = record(value)
      if (!Array.isArray(branch.assumptions) || !Array.isArray(branch.nodes)) throw new Error('contract')
      return {
        operation_id: string(branch.operation_id),
        assumptions: branch.assumptions.map(string),
        nodes: branch.nodes.map((raw) => {
          const candidate = record(raw)
          return { title: string(candidate.title), due_date: nullableString(candidate.due_date) }
        }),
      }
    }),
    allowed_commands: item.allowed_commands.map(string),
    projection_id: nullableString(item.projection_id),
  }
}

function planResult(value: unknown): WorkPlanResult {
  const item = record(value)
  if (
    !Array.isArray(item.questions)
    || !Array.isArray(item.assumptions)
    || !item.questions.every((entry) => typeof entry === 'string')
    || !item.assumptions.every((entry) => typeof entry === 'string')
  ) throw new Error('contract')
  let decodedPlan: WorkPlan | null = null
  if (item.plan !== null) {
    const rawPlan = record(item.plan)
    if (!Array.isArray(rawPlan.nodes) || !Array.isArray(rawPlan.edges)) {
      throw new Error('contract')
    }
    if (!Array.isArray(rawPlan.assumptions)
      || !rawPlan.assumptions.every((entry) => typeof entry === 'string')) {
      throw new Error('contract')
    }
    decodedPlan = {
      nodes: rawPlan.nodes.map(planNode),
      edges: rawPlan.edges.map((value) => {
        const edge = record(value)
        return {
          source_draft_key: string(edge.source_draft_key),
          target_draft_key: string(edge.target_draft_key),
          relation: string(edge.relation) as WorkPlanEdge['relation'],
        }
      }),
      assumptions: rawPlan.assumptions as string[],
    }
  }
  return {
    preview_id: string(item.preview_id),
    operation_id: string(item.operation_id),
    state: string(item.state) as WorkPlanState,
    physical_request_count: number(item.physical_request_count),
    error_category: nullableString(item.error_category),
    questions: item.questions as string[],
    assumptions: item.assumptions as string[],
    plan: decodedPlan,
    plan_fingerprint: nullableString(item.plan_fingerprint),
    teacher_confirmation_required: boolean(item.teacher_confirmation_required),
    local_context: record(item.local_context),
  }
}

function mutationHeaders(): Record<string, string> {
  return { 'x-class-teacher-client': CLIENT_HEADER }
}

export const workApi = {
  read(view: WorkView, anchor?: string, cursor?: string) {
    const params = new URLSearchParams({ view })
    if (anchor) params.set('anchor', anchor)
    if (cursor) params.set('cursor', cursor)
    return apiClient.request(`/api/class-teacher/work?${params.toString()}`, { decode: snapshot })
  },
  detail(nodeId: string) {
    return apiClient.request(`/api/class-teacher/work/nodes/${nodeId}`, { decode: nodeDetail })
  },
  command(
    value: WorkNode,
    command: string,
    fields: Record<string, unknown> = {},
  ) {
    return apiClient.request(`/api/class-teacher/work/nodes/${value.node_id}/commands`, {
      method: 'POST',
      headers: mutationHeaders(),
      body: {
        command,
        expected_revision: value.revision,
        operation_id: globalThis.crypto.randomUUID(),
        ...fields,
      },
      decode: (payload) => record(payload),
    })
  },
  query(startDate?: string, endDate?: string) {
    const params = new URLSearchParams()
    if (startDate) params.set('start_date', startDate)
    if (endDate) params.set('end_date', endDate)
    const query = params.size ? `?${params.toString()}` : ''
    return apiClient.request(`/api/class-teacher/work${query}`, { decode: snapshot })
  },
  previewPlan(text: string, dueDate: string | null) {
    return apiClient.request('/api/class-teacher/work/plans/previews', {
      method: 'POST',
      headers: mutationHeaders(),
      body: { text, due_date: dueDate },
      decode: planPreview,
    })
  },
  invokePlan(preview: WorkPlanPreview, operationId: string) {
    return apiClient.request(
      `/api/class-teacher/work/plans/previews/${preview.preview_id}/confirm`,
      {
        method: 'POST',
        headers: mutationHeaders(),
        body: { fingerprint: preview.fingerprint, operation_id: operationId },
        decode: planResult,
        timeoutMs: 125_000,
      },
    )
  },
  planStatus(operationId: string) {
    return apiClient.request(
      `/api/class-teacher/work/plans/operations/${operationId}`,
      { decode: planResult },
    )
  },
  confirmPlan(result: WorkPlanResult, operationId: string) {
    if (!result.plan_fingerprint) throw new Error('plan fingerprint required')
    return apiClient.request('/api/class-teacher/work/plans/confirm', {
      method: 'POST',
      headers: mutationHeaders(),
      body: {
        model_operation_id: result.operation_id,
        plan_fingerprint: result.plan_fingerprint,
        operation_id: operationId,
      },
      decode: (value) => {
        const item = record(value)
        if (!Array.isArray(item.nodes) || !Array.isArray(item.edges)) throw new Error('contract')
        return {
          goal_id: nullableString(item.goal_id),
          parent_node_id: nullableString(item.parent_node_id),
          nodes: item.nodes.map(node),
        }
      },
    })
  },
  update(
    nodeValue: WorkNode,
    status: Exclude<WorkStatus, 'draft'>,
    operationId: string,
  ) {
    return apiClient.request(`/api/class-teacher/work/nodes/${nodeValue.node_id}`, {
      method: 'PATCH',
      headers: mutationHeaders(),
      body: {
        revision: nodeValue.revision,
        status,
        due_date: nodeValue.due_date,
        operation_id: operationId,
      },
      decode: node,
    })
  },
  previewProgressPlan(nodeValue: WorkNode, textValue: string, dueDate: string | null) {
    return apiClient.request(
      `/api/class-teacher/work/nodes/${nodeValue.node_id}/plans/previews`,
      {
        method: 'POST',
        headers: mutationHeaders(),
        body: {
          revision: nodeValue.revision,
          text: textValue,
          due_date: dueDate,
        },
        decode: planPreview,
      },
    )
  },
}
