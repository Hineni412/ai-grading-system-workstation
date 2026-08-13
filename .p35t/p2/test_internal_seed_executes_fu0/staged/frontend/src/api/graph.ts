import { apiClient } from './client'
import { isRecord } from './validation'

export interface GraphScope {
  mode: 'student' | 'selected' | 'class'
  student_ids: string[]
  class_id: string | null
}

export interface GraphExamScope {
  mode: 'current' | 'manual' | 'cross_exam'
  session_ids: number[]
  sessions: Array<{ session_id: number; session_name: string }>
}

export type GraphStudentScopeInput =
  | { mode: 'class'; class_id: string; student_ids?: string[] }
  | { mode: 'student' | 'selected'; student_ids: string[] }

export type GraphExamScopeInput =
  | { mode: 'current'; session_ids: [number] }
  | { mode: 'manual'; session_ids: number[] }
  | { mode: 'cross_exam'; session_ids?: never }

export interface GraphQueryInput {
  scope: GraphStudentScopeInput
  exam_scope: GraphExamScopeInput
}

export interface GraphCoverage {
  covered_items: number
  total_items: number
  missing_items: Record<string, string>
}

export interface GraphSourceQuestionReference {
  session_id: number
  session_name: string
  question_id: string
  bank_question_id: number
  score_awarded: number
  full_score: number
  score_rate: number | null
}

export interface GraphRow {
  student_id: number
  student_code: string
  student_name: string
  knowledge_key: string
  knowledge_label: string
  weighted_score_rate: number
  deduction_count: number
  item_count: number
  sample_reasons: string
  source_question_refs: GraphSourceQuestionReference[]
  tag_context: Record<string, string[]>
  error_counts: Record<string, Record<string, number>>
}

export interface GraphNode {
  knowledge_key: string
  knowledge_label: string
  student_count: number
  item_count: number
  deduction_count: number
  average_mastery: number
  tag_context: Record<string, string[]>
  error_counts: Record<string, Record<string, number>>
}

export interface GraphEdge {
  source_key: string
  target_key: string
  relation_type: 'prerequisite' | 'parent' | 'related'
  weight: number
}

export interface GraphEvidenceItem {
  student_id: number
  student_code: string
  student_name: string
  class_id: string
  knowledge_key: string
  knowledge_label: string
  session_id: number
  session_name: string
  question_id: string
  bank_question_id: number
  score_awarded: number
  full_score: number
  score_rate: number | null
  tag_context: Record<string, string[]>
  actionable_reasons: string[]
  error_counts: Record<string, Record<string, number>>
}

export interface GraphRowsResponse {
  scope: GraphScope
  exam_scope: GraphExamScope
  rows: GraphRow[]
  nodes: GraphNode[]
  edges: GraphEdge[]
  coverage: GraphCoverage
  warnings: string[]
  diagnosis_identity: 'question_tag'
}

export interface GraphEvidenceResponse {
  scope: GraphScope
  exam_scope: GraphExamScope
  knowledge_key: string
  knowledge_label: string
  items: GraphEvidenceItem[]
  total: number
  page: number
  page_size: number
  total_pages: number
  coverage: GraphCoverage
  warnings: string[]
  diagnosis_identity: 'question_tag'
}

interface GraphEvidenceExpectation {
  query: GraphQueryInput
  knowledgeKey: string
  page?: number
}

function isInteger(value: unknown, positive = false): value is number {
  return Number.isSafeInteger(value) && Number(value) >= (positive ? 1 : 0)
}

function isNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function isRate(value: unknown): value is number {
  return isNumber(value) && value >= 0 && value <= 1
}

function isPercentage(value: unknown): value is number {
  return isNumber(value) && value >= 0 && value <= 100
}

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(value)
  return actual.length === keys.length && keys.every(
    (key) => Object.prototype.hasOwnProperty.call(value, key),
  )
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string')
}

function isStringMap(value: unknown): value is Record<string, string> {
  return isRecord(value) && Object.values(value).every((item) => typeof item === 'string')
}

function isStringArrayMap(value: unknown): value is Record<string, string[]> {
  return isRecord(value) && Object.values(value).every(isStringArray)
}

function isCountMap(value: unknown): value is Record<string, Record<string, number>> {
  return (
    isRecord(value) &&
    Object.values(value).every(
      (group) => isRecord(group) && Object.values(group).every((count) => isInteger(count)),
    )
  )
}

function isScope(value: unknown): value is GraphScope {
  return (
    isRecord(value) &&
    hasExactKeys(value, ['mode', 'student_ids', 'class_id']) &&
    (value.mode === 'student' || value.mode === 'selected' || value.mode === 'class') &&
    isStringArray(value.student_ids) &&
    (value.class_id === null || typeof value.class_id === 'string')
  )
}

function isExamScope(value: unknown): value is GraphExamScope {
  return (
    isRecord(value) &&
    hasExactKeys(value, ['mode', 'session_ids', 'sessions']) &&
    (value.mode === 'current' || value.mode === 'manual' || value.mode === 'cross_exam') &&
    Array.isArray(value.session_ids) &&
    value.session_ids.every((id) => isInteger(id, true)) &&
    Array.isArray(value.sessions) &&
    value.sessions.every(
      (session) =>
        isRecord(session) &&
        hasExactKeys(session, ['session_id', 'session_name']) &&
        isInteger(session.session_id, true) &&
        typeof session.session_name === 'string',
    )
  )
}

function isCoverage(value: unknown): value is GraphCoverage {
  return (
    isRecord(value) &&
    hasExactKeys(value, ['covered_items', 'total_items', 'missing_items']) &&
    isInteger(value.covered_items) &&
    isInteger(value.total_items) &&
    value.covered_items <= value.total_items &&
    isStringMap(value.missing_items)
  )
}

function isSourceReference(value: unknown): value is GraphSourceQuestionReference {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      'session_id', 'session_name', 'question_id', 'bank_question_id',
      'score_awarded', 'full_score', 'score_rate',
    ]) &&
    isInteger(value.session_id, true) &&
    typeof value.session_name === 'string' &&
    typeof value.question_id === 'string' &&
    isInteger(value.bank_question_id, true) &&
    isNumber(value.score_awarded) &&
    isNumber(value.full_score) &&
    (value.score_rate === null || isRate(value.score_rate))
  )
}

function isGraphRow(value: unknown): value is GraphRow {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      'student_id', 'student_code', 'student_name', 'knowledge_key',
      'knowledge_label', 'weighted_score_rate', 'deduction_count', 'item_count',
      'sample_reasons', 'source_question_refs', 'tag_context', 'error_counts',
    ]) &&
    isInteger(value.student_id, true) &&
    typeof value.student_code === 'string' &&
    typeof value.student_name === 'string' &&
    typeof value.knowledge_key === 'string' &&
    value.knowledge_key.startsWith('knowledge_point:') &&
    typeof value.knowledge_label === 'string' &&
    isPercentage(value.weighted_score_rate) &&
    isInteger(value.deduction_count) &&
    isInteger(value.item_count) &&
    typeof value.sample_reasons === 'string' &&
    Array.isArray(value.source_question_refs) &&
    value.source_question_refs.every(isSourceReference) &&
    isStringArrayMap(value.tag_context) &&
    isCountMap(value.error_counts)
  )
}

function isGraphNode(value: unknown): value is GraphNode {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      'knowledge_key', 'knowledge_label', 'student_count', 'item_count',
      'deduction_count', 'average_mastery', 'tag_context', 'error_counts',
    ]) &&
    typeof value.knowledge_key === 'string' &&
    value.knowledge_key.startsWith('knowledge_point:') &&
    typeof value.knowledge_label === 'string' &&
    isInteger(value.student_count) &&
    isInteger(value.item_count) &&
    isInteger(value.deduction_count) &&
    isRate(value.average_mastery) &&
    isStringArrayMap(value.tag_context) &&
    isCountMap(value.error_counts)
  )
}

function isGraphEdge(value: unknown): value is GraphEdge {
  return (
    isRecord(value) &&
    hasExactKeys(value, ['source_key', 'target_key', 'relation_type', 'weight']) &&
    typeof value.source_key === 'string' &&
    typeof value.target_key === 'string' &&
    (value.relation_type === 'prerequisite' ||
      value.relation_type === 'parent' ||
      value.relation_type === 'related') &&
    isRate(value.weight)
  )
}

function isGraphEvidenceItem(value: unknown): value is GraphEvidenceItem {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      'student_id', 'student_code', 'student_name', 'class_id', 'knowledge_key',
      'knowledge_label', 'session_id', 'session_name', 'question_id',
      'bank_question_id', 'score_awarded', 'full_score', 'score_rate',
      'tag_context', 'actionable_reasons', 'error_counts',
    ]) &&
    isInteger(value.student_id, true) &&
    typeof value.student_code === 'string' &&
    typeof value.student_name === 'string' &&
    typeof value.class_id === 'string' &&
    typeof value.knowledge_key === 'string' &&
    value.knowledge_key.startsWith('knowledge_point:') &&
    typeof value.knowledge_label === 'string' &&
    isInteger(value.session_id, true) &&
    typeof value.session_name === 'string' &&
    typeof value.question_id === 'string' &&
    isInteger(value.bank_question_id, true) &&
    isNumber(value.score_awarded) &&
    isNumber(value.full_score) &&
    (value.score_rate === null || isRate(value.score_rate)) &&
    isStringArrayMap(value.tag_context) &&
    isStringArray(value.actionable_reasons) &&
    isCountMap(value.error_counts)
  )
}

function hasSharedContext(value: Record<string, unknown>): boolean {
  return (
    isScope(value.scope) &&
    isExamScope(value.exam_scope) &&
    isCoverage(value.coverage) &&
    isStringArray(value.warnings) &&
    value.diagnosis_identity === 'question_tag'
  )
}

export function decodeGraphRowsResponse(
  value: unknown,
  expected?: GraphQueryInput,
): GraphRowsResponse {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      'scope', 'exam_scope', 'rows', 'nodes', 'edges', 'coverage', 'warnings',
      'diagnosis_identity',
    ]) ||
    !hasSharedContext(value) ||
    !Array.isArray(value.rows) ||
    !value.rows.every(isGraphRow) ||
    !Array.isArray(value.nodes) ||
    !value.nodes.every(isGraphNode) ||
    !Array.isArray(value.edges) ||
    !value.edges.every(isGraphEdge) ||
    value.edges.length !== 0
  ) {
    throw new Error('Invalid graph rows')
  }
  const response = value as unknown as GraphRowsResponse
  if (
    !hasAlignedExamScope(response.exam_scope) ||
    response.rows.some((row) => (
      !response.scope.student_ids.includes(String(row.student_id)) ||
      row.source_question_refs.some((reference) => (
        !response.exam_scope.session_ids.includes(reference.session_id)
      ))
    )) ||
    (expected && !matchesGraphQuery(response, expected))
  ) {
    throw new Error('Invalid graph rows')
  }
  return response
}

function hasAlignedExamScope(scope: GraphExamScope): boolean {
  return (
    scope.sessions.length === scope.session_ids.length &&
    scope.sessions.every((session, index) => session.session_id === scope.session_ids[index])
  )
}

function isOrderedSubset<T>(actual: T[], requested: T[]): boolean {
  let position = 0
  for (const value of actual) {
    position = requested.indexOf(value, position)
    if (position < 0) return false
    position += 1
  }
  return true
}

function matchesGraphQuery(response: GraphRowsResponse | GraphEvidenceResponse, query: GraphQueryInput): boolean {
  if (response.scope.mode !== query.scope.mode || response.exam_scope.mode !== query.exam_scope.mode) {
    return false
  }
  if (query.scope.mode === 'class') {
    if (response.scope.class_id !== query.scope.class_id) return false
    if (query.scope.student_ids && !isOrderedSubset(response.scope.student_ids, query.scope.student_ids)) {
      return false
    }
  } else if (!isOrderedSubset(response.scope.student_ids, query.scope.student_ids)) {
    return false
  }
  if (query.exam_scope.mode === 'current') {
    return response.exam_scope.session_ids.length === 1 &&
      response.exam_scope.session_ids[0] === query.exam_scope.session_ids[0]
  }
  if (query.exam_scope.mode === 'manual') {
    return isOrderedSubset(response.exam_scope.session_ids, query.exam_scope.session_ids)
  }
  return true
}

export function hasMatchingGraphEvidenceScope(
  response: GraphEvidenceResponse,
  expected?: GraphEvidenceExpectation,
): boolean {
  if (!hasAlignedExamScope(response.exam_scope)) return false
  if (expected && (
    !matchesGraphQuery(response, expected.query) ||
    response.knowledge_key !== expected.knowledgeKey ||
    (expected.page !== undefined && response.page !== expected.page)
  )) return false
  return response.items.every((item) => (
    item.knowledge_key === response.knowledge_key &&
    response.scope.student_ids.includes(String(item.student_id)) &&
    response.exam_scope.session_ids.includes(item.session_id) &&
    (response.scope.mode !== 'class' || item.class_id === response.scope.class_id)
  ))
}

export function decodeGraphEvidenceResponse(
  value: unknown,
  expected?: GraphEvidenceExpectation,
): GraphEvidenceResponse {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      'scope', 'exam_scope', 'knowledge_key', 'knowledge_label', 'items', 'total',
      'page', 'page_size', 'total_pages', 'coverage', 'warnings',
      'diagnosis_identity',
    ]) ||
    !hasSharedContext(value) ||
    typeof value.knowledge_key !== 'string' ||
    !value.knowledge_key.startsWith('knowledge_point:') ||
    typeof value.knowledge_label !== 'string' ||
    !Array.isArray(value.items) ||
    !value.items.every(isGraphEvidenceItem) ||
    !isInteger(value.total) ||
    !isInteger(value.page, true) ||
    !isInteger(value.page_size, true) ||
    !isInteger(value.total_pages, true) ||
    value.total_pages !== Math.max(1, Math.ceil(value.total / value.page_size)) ||
    value.items.length !==
      Math.max(0, Math.min(value.page_size, value.total - (value.page - 1) * value.page_size))
  ) {
    throw new Error('Invalid graph evidence')
  }
  const response = value as unknown as GraphEvidenceResponse
  if (!hasMatchingGraphEvidenceScope(response, expected)) {
    throw new Error('Invalid graph evidence')
  }
  return response
}

function requireSessionId(sessionId: number): number {
  if (!isInteger(sessionId, true)) throw new Error('Invalid session id')
  return sessionId
}

function requireClassName(className: string): string {
  const value = className.trim()
  if (!value) throw new Error('Invalid class name')
  return value
}

function graphScope(sessionId: number, className: string) {
  return normalizeGraphQuery({
    scope: { mode: 'class' as const, class_id: requireClassName(className) },
    exam_scope: { mode: 'current' as const, session_ids: [requireSessionId(sessionId)] },
  })
}

function normalizeStudentIds(values: string[]): string[] {
  const result: string[] = []
  for (const rawValue of values) {
    const value = String(rawValue).trim()
    if (!value) throw new Error('Invalid student id')
    if (!result.includes(value)) result.push(value)
  }
  return result
}

function normalizeSessionIds(values: number[]): number[] {
  const result: number[] = []
  for (const rawValue of values) {
    const value = requireSessionId(rawValue)
    if (!result.includes(value)) result.push(value)
  }
  return result
}

export function normalizeGraphQuery(query: GraphQueryInput): GraphQueryInput {
  let scope: GraphStudentScopeInput
  if (query.scope.mode === 'class') {
    const studentIds = query.scope.student_ids
      ? normalizeStudentIds(query.scope.student_ids)
      : undefined
    scope = {
      mode: 'class',
      class_id: requireClassName(query.scope.class_id),
      ...(studentIds && studentIds.length > 0 ? { student_ids: studentIds } : {}),
    }
  } else {
    const studentIds = normalizeStudentIds(query.scope.student_ids)
    if (studentIds.length === 0) throw new Error('Invalid student scope')
    scope = { mode: query.scope.mode, student_ids: studentIds }
  }

  let examScope: GraphExamScopeInput
  if (query.exam_scope.mode === 'cross_exam') {
    examScope = { mode: 'cross_exam' }
  } else {
    const sessionIds = normalizeSessionIds(query.exam_scope.session_ids)
    if (query.exam_scope.mode === 'current') {
      if (sessionIds.length !== 1) throw new Error('Invalid current exam scope')
      examScope = { mode: 'current', session_ids: [sessionIds[0]!] }
    } else {
      if (sessionIds.length === 0) throw new Error('Invalid manual exam scope')
      examScope = { mode: 'manual', session_ids: sessionIds }
    }
  }
  return { scope, exam_scope: examScope }
}

export function fetchScopedGraphRows(
  query: GraphQueryInput,
  signal?: AbortSignal,
): Promise<GraphRowsResponse> {
  const normalized = normalizeGraphQuery(query)
  return apiClient.request('/api/graph/rows', {
    method: 'POST',
    body: normalized,
    decode: (value) => decodeGraphRowsResponse(value, normalized),
    signal,
  })
}

export function fetchScopedGraphEvidence(
  query: GraphQueryInput,
  knowledgeKey: string,
  signal?: AbortSignal,
  page = 1,
): Promise<GraphEvidenceResponse> {
  const normalized = normalizeGraphQuery(query)
  if (!knowledgeKey.startsWith('knowledge_point:') || knowledgeKey.length <= 'knowledge_point:'.length) {
    throw new Error('Invalid knowledge key')
  }
  const normalizedPage = Number.isSafeInteger(page) && page > 0 ? page : 1
  return apiClient.request('/api/graph/evidence', {
    method: 'POST',
    body: {
      ...normalized,
      knowledge_key: knowledgeKey,
      page: normalizedPage,
      page_size: 20,
    },
    decode: (value) => decodeGraphEvidenceResponse(value, {
      query: normalized,
      knowledgeKey,
      page: normalizedPage,
    }),
    signal,
  })
}

export function fetchGraphRows(
  sessionId: number,
  className: string,
  signal?: AbortSignal,
): Promise<GraphRowsResponse> {
  return fetchScopedGraphRows(graphScope(sessionId, className), signal)
}

export function fetchGraphEvidence(
  sessionId: number,
  className: string,
  knowledgeKey: string,
  signal?: AbortSignal,
  page = 1,
): Promise<GraphEvidenceResponse> {
  return fetchScopedGraphEvidence(
    graphScope(sessionId, className),
    knowledgeKey,
    signal,
    page,
  )
}
