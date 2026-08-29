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
}
