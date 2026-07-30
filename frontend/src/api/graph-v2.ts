import { apiClient } from './client'
import {
  normalizeGraphQuery,
  type GraphCoverage,
  type GraphExamScope,
  type GraphQueryInput,
  type GraphScope,
} from './graph'
import { isRecord } from './validation'

export type GraphV2RelationType = 'parent' | 'prerequisite' | 'related'
export type GraphV2MasteryStatus = 'available' | 'missing' | 'unavailable'

export interface GraphV2Mastery {
  status: GraphV2MasteryStatus
  value: number | null
  evidence_count: number
  reason: string | null
}

export interface GraphV2EvidenceSummary {
  student_count: number
  item_count: number
  deduction_count: number
  tag_context: Record<string, string[]>
  error_counts: Record<string, Record<string, number>>
}

export interface GraphV2Node {
  stable_key: string
  display_name: string
  identity_revision: number
  mastery_v1: GraphV2Mastery
  mastery_v2: GraphV2Mastery
  evidence: GraphV2EvidenceSummary
  missing_reasons: string[]
}

export interface GraphV2Edge {
  relation_id: string
  source_key: string
  target_key: string
  relation_type: GraphV2RelationType
  rationale: string
  revision: number
}

export type GraphV2MissingItem =
  | { kind: 'ungoverned_knowledge_label'; label: string; count: number }
  | { kind: 'requested_identity_not_found'; stable_key: string; count: number }

export interface GraphV2Response {
  response_schema_version: 'knowledge-graph-v2'
  response_version: string
  scope: GraphScope
  exam_scope: GraphExamScope
  coverage: GraphCoverage
  nodes: GraphV2Node[]
  edges: GraphV2Edge[]
  missing: GraphV2MissingItem[]
  warnings: string[]
  counts: {
    node_count: number
    edge_count: number
    evidence_row_count: number
    missing_count: number
  }
}

export interface GraphV2EvidenceItem {
  student_id: number
  student_code: string
  student_name: string
  class_id: string
  knowledge_key: string
  stable_key: string
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

export interface GraphV2EvidenceResponse {
  response_schema_version: 'knowledge-graph-evidence-v2'
  response_version: string
  scope: GraphScope
  exam_scope: GraphExamScope
  coverage: GraphCoverage
  stable_key: string
  display_name: string
  items: GraphV2EvidenceItem[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

export interface RelationReviewQueueItem {
  relation_id: string
  source_key: string
  source_name: string
  target_key: string
  target_name: string
  relation_type: GraphV2RelationType
  source_kind: string
  source_reference: string | null
  rationale: string
  model_name: string | null
  model_version: string | null
  prompt_version: string | null
  confidence: number | null
  conflict_codes: string[]
  revision: number
  updated_at: string
}

export interface RelationReviewQueueResponse {
  status: 'suggested'
  items: RelationReviewQueueItem[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(value)
  return actual.length === keys.length && keys.every(
    (key) => Object.prototype.hasOwnProperty.call(value, key),
  )
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
  return isRecord(value) && Object.values(value).every(
    (group) => isRecord(group) && Object.values(group).every((count) => isInteger(count)),
  )
}

function isStableKey(value: unknown): value is string {
  return typeof value === 'string' && /^(?:kp_[a-z0-9_]+|ki_[0-9a-f]{32})$/.test(value)
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
    value.sessions.every((session) => (
      isRecord(session) &&
      hasExactKeys(session, ['session_id', 'session_name']) &&
      isInteger(session.session_id, true) &&
      typeof session.session_name === 'string'
    ))
  )
}

function hasAlignedExamScope(scope: GraphExamScope): boolean {
  return (
    scope.sessions.length === scope.session_ids.length &&
    scope.sessions.every((session, index) => session.session_id === scope.session_ids[index])
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

function isMastery(value: unknown): value is GraphV2Mastery {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ['status', 'value', 'evidence_count', 'reason']) ||
    (value.status !== 'available' && value.status !== 'missing' && value.status !== 'unavailable') ||
    !isInteger(value.evidence_count) ||
    (value.reason !== null && typeof value.reason !== 'string')
  ) return false
  if (value.status === 'available') {
    return isRate(value.value) && value.evidence_count > 0 && value.reason === null
  }
  return value.value === null
}

function isEvidenceSummary(value: unknown): value is GraphV2EvidenceSummary {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      'student_count', 'item_count', 'deduction_count', 'tag_context', 'error_counts',
    ]) &&
    isInteger(value.student_count) &&
    isInteger(value.item_count) &&
    isInteger(value.deduction_count) &&
    isStringArrayMap(value.tag_context) &&
    isCountMap(value.error_counts)
  )
}

function isNode(value: unknown): value is GraphV2Node {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      'stable_key', 'display_name', 'identity_revision', 'mastery_v1', 'mastery_v2',
      'evidence', 'missing_reasons',
    ]) &&
    isStableKey(value.stable_key) &&
    typeof value.display_name === 'string' &&
    value.display_name.trim().length > 0 &&
    isInteger(value.identity_revision, true) &&
    isMastery(value.mastery_v1) &&
    isMastery(value.mastery_v2) &&
    isEvidenceSummary(value.evidence) &&
    isStringArray(value.missing_reasons)
  )
}

function isRelationType(value: unknown): value is GraphV2RelationType {
  return value === 'parent' || value === 'prerequisite' || value === 'related'
}

function isEdge(value: unknown): value is GraphV2Edge {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      'relation_id', 'source_key', 'target_key', 'relation_type', 'rationale', 'revision',
    ]) &&
    typeof value.relation_id === 'string' &&
    value.relation_id.length > 0 &&
    isStableKey(value.source_key) &&
    isStableKey(value.target_key) &&
    value.source_key !== value.target_key &&
    isRelationType(value.relation_type) &&
    typeof value.rationale === 'string' &&
    isInteger(value.revision, true)
  )
}

function isMissingItem(value: unknown): value is GraphV2MissingItem {
  if (!isRecord(value) || !isInteger(value.count, true)) return false
  if (value.kind === 'ungoverned_knowledge_label') {
    return hasExactKeys(value, ['kind', 'label', 'count']) && typeof value.label === 'string'
  }
  return (
    value.kind === 'requested_identity_not_found' &&
    hasExactKeys(value, ['kind', 'stable_key', 'count']) &&
    isStableKey(value.stable_key)
  )
}

function isCounts(value: unknown): value is GraphV2Response['counts'] {
  return (
    isRecord(value) &&
    hasExactKeys(value, ['node_count', 'edge_count', 'evidence_row_count', 'missing_count']) &&
    isInteger(value.node_count) &&
    isInteger(value.edge_count) &&
    isInteger(value.evidence_row_count) &&
    isInteger(value.missing_count)
  )
}

function isSha256(value: unknown): value is string {
  return typeof value === 'string' && /^[0-9a-f]{64}$/.test(value)
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

function matchesQuery(
  scope: GraphScope,
  examScope: GraphExamScope,
  query: GraphQueryInput,
): boolean {
  if (scope.mode !== query.scope.mode || examScope.mode !== query.exam_scope.mode) return false
  if (query.scope.mode === 'class') {
    if (scope.class_id !== query.scope.class_id) return false
    if (query.scope.student_ids && !isOrderedSubset(scope.student_ids, query.scope.student_ids)) {
      return false
    }
  } else if (!isOrderedSubset(scope.student_ids, query.scope.student_ids)) {
    return false
  }
  if (query.exam_scope.mode === 'cross_exam') return true
  return isOrderedSubset(examScope.session_ids, query.exam_scope.session_ids)
}

export function decodeGraphV2Response(
  value: unknown,
  expected?: GraphQueryInput,
): GraphV2Response {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      'response_schema_version', 'response_version', 'scope', 'exam_scope', 'coverage',
      'nodes', 'edges', 'missing', 'warnings', 'counts',
    ]) ||
    value.response_schema_version !== 'knowledge-graph-v2' ||
    !isSha256(value.response_version) ||
    !isScope(value.scope) ||
    !isExamScope(value.exam_scope) ||
    !isCoverage(value.coverage) ||
    !Array.isArray(value.nodes) ||
    !value.nodes.every(isNode) ||
    !Array.isArray(value.edges) ||
    !value.edges.every(isEdge) ||
    !Array.isArray(value.missing) ||
    !value.missing.every(isMissingItem) ||
    !isStringArray(value.warnings) ||
    !isCounts(value.counts)
  ) throw new Error('Invalid graph v2 response')
  const response = value as unknown as GraphV2Response
  const nodeKeys = new Set(response.nodes.map((node) => node.stable_key))
  if (
    nodeKeys.size !== response.nodes.length ||
    !hasAlignedExamScope(response.exam_scope) ||
    response.edges.some((edge) => !nodeKeys.has(edge.source_key) || !nodeKeys.has(edge.target_key)) ||
    response.counts.node_count !== response.nodes.length ||
    response.counts.edge_count !== response.edges.length ||
    response.counts.missing_count !== response.missing.reduce(
      (total, item) => total + item.count,
      0,
    ) ||
    (expected && !matchesQuery(response.scope, response.exam_scope, expected))
  ) throw new Error('Invalid graph v2 response')
  return response
}

function isEvidenceItem(value: unknown): value is GraphV2EvidenceItem {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      'student_id', 'student_code', 'student_name', 'class_id', 'knowledge_key',
      'knowledge_label', 'session_id', 'session_name', 'question_id', 'bank_question_id',
      'score_awarded', 'full_score', 'score_rate', 'tag_context', 'actionable_reasons',
      'error_counts', 'stable_key',
    ]) &&
    isInteger(value.student_id, true) &&
    typeof value.student_code === 'string' &&
    typeof value.student_name === 'string' &&
    typeof value.class_id === 'string' &&
    typeof value.knowledge_key === 'string' &&
    value.knowledge_key.startsWith('knowledge_point:') &&
    isStableKey(value.stable_key) &&
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

export function decodeGraphV2EvidenceResponse(
  value: unknown,
  expected: { query: GraphQueryInput; stableKey: string; page: number },
): GraphV2EvidenceResponse {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      'response_schema_version', 'response_version', 'scope', 'exam_scope', 'coverage',
      'stable_key', 'display_name', 'items', 'total', 'page', 'page_size', 'total_pages',
    ]) ||
    value.response_schema_version !== 'knowledge-graph-evidence-v2' ||
    !isSha256(value.response_version) ||
    !isScope(value.scope) ||
    !isExamScope(value.exam_scope) ||
    !isCoverage(value.coverage) ||
    !isStableKey(value.stable_key) ||
    typeof value.display_name !== 'string' ||
    !Array.isArray(value.items) ||
    !value.items.every(isEvidenceItem) ||
    !isInteger(value.total) ||
    !isInteger(value.page, true) ||
    !isInteger(value.page_size, true) ||
    !isInteger(value.total_pages, true)
  ) throw new Error('Invalid graph v2 evidence')
  const response = value as unknown as GraphV2EvidenceResponse
  if (
    response.stable_key !== expected.stableKey ||
    response.page !== expected.page ||
    response.page > response.total_pages ||
    !hasAlignedExamScope(response.exam_scope) ||
    response.total_pages !== Math.max(1, Math.ceil(response.total / response.page_size)) ||
    response.items.length !== Math.max(
      0,
      Math.min(
        response.page_size,
        response.total - (response.page - 1) * response.page_size,
      ),
    ) ||
    response.items.some((item) => (
      item.stable_key !== response.stable_key ||
      !response.scope.student_ids.includes(String(item.student_id)) ||
      !response.exam_scope.session_ids.includes(item.session_id) ||
      (response.scope.mode === 'class' && item.class_id !== response.scope.class_id)
    )) ||
    !matchesQuery(response.scope, response.exam_scope, expected.query)
  ) throw new Error('Invalid graph v2 evidence')
  return response
}

function isQueueItem(value: unknown): value is RelationReviewQueueItem {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      'relation_id', 'source_key', 'source_name', 'target_key', 'target_name',
      'relation_type', 'source_kind', 'source_reference', 'rationale', 'model_name',
      'model_version', 'prompt_version', 'confidence', 'conflict_codes', 'revision',
      'updated_at',
    ]) &&
    typeof value.relation_id === 'string' &&
    isStableKey(value.source_key) &&
    typeof value.source_name === 'string' &&
    isStableKey(value.target_key) &&
    typeof value.target_name === 'string' &&
    isRelationType(value.relation_type) &&
    typeof value.source_kind === 'string' &&
    (value.source_reference === null || typeof value.source_reference === 'string') &&
    typeof value.rationale === 'string' &&
    (value.model_name === null || typeof value.model_name === 'string') &&
    (value.model_version === null || typeof value.model_version === 'string') &&
    (value.prompt_version === null || typeof value.prompt_version === 'string') &&
    (value.confidence === null || isRate(value.confidence)) &&
    isStringArray(value.conflict_codes) &&
    isInteger(value.revision, true) &&
    typeof value.updated_at === 'string'
  )
}

export function decodeRelationReviewQueue(value: unknown): RelationReviewQueueResponse {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ['status', 'items', 'total', 'page', 'page_size', 'total_pages']) ||
    value.status !== 'suggested' ||
    !Array.isArray(value.items) ||
    !value.items.every(isQueueItem) ||
    !isInteger(value.total) ||
    !isInteger(value.page, true) ||
    !isInteger(value.page_size, true) ||
    !isInteger(value.total_pages, true)
  ) throw new Error('Invalid relation review queue')
  const response = value as unknown as RelationReviewQueueResponse
  if (
    response.total_pages !== Math.max(1, Math.ceil(response.total / response.page_size)) ||
    response.page > response.total_pages ||
    response.items.length !== Math.max(
      0,
      Math.min(
        response.page_size,
        response.total - (response.page - 1) * response.page_size,
      ),
    )
  ) {
    throw new Error('Invalid relation review queue')
  }
  return response
}

function normalizeStableKeys(values: string[]): string[] {
  const result: string[] = []
  for (const raw of values) {
    const value = raw.trim().toLocaleLowerCase('en-US')
    if (!isStableKey(value)) throw new Error('Invalid stable key')
    if (!result.includes(value)) result.push(value)
  }
  return result
}

export function fetchGraphV2(
  query: GraphQueryInput,
  signal?: AbortSignal,
  knowledgeKeys: string[] = [],
  prerequisiteDepth = 1,
): Promise<GraphV2Response> {
  const normalized = normalizeGraphQuery(query)
  const keys = normalizeStableKeys(knowledgeKeys)
  if (!Number.isSafeInteger(prerequisiteDepth) || prerequisiteDepth < 0 || prerequisiteDepth > 5) {
    throw new Error('Invalid prerequisite depth')
  }
  return apiClient.request('/api/graph/v2/query', {
    method: 'POST',
    body: {
      ...normalized,
      knowledge_keys: keys,
      prerequisite_depth: prerequisiteDepth,
    },
    decode: (value) => decodeGraphV2Response(value, normalized),
    signal,
  })
}

export function fetchGraphV2Evidence(
  query: GraphQueryInput,
  stableKey: string,
  signal?: AbortSignal,
  page = 1,
): Promise<GraphV2EvidenceResponse> {
  const normalized = normalizeGraphQuery(query)
  const [key] = normalizeStableKeys([stableKey])
  const normalizedPage = Number.isSafeInteger(page) && page > 0 ? page : 1
  return apiClient.request('/api/graph/v2/evidence', {
    method: 'POST',
    body: {
      ...normalized,
      stable_key: key,
      page: normalizedPage,
      page_size: 20,
    },
    decode: (value) => decodeGraphV2EvidenceResponse(value, {
      query: normalized,
      stableKey: key!,
      page: normalizedPage,
    }),
    signal,
  })
}

export function fetchRelationReviewQueue(
  signal?: AbortSignal,
): Promise<RelationReviewQueueResponse> {
  return apiClient.request('/api/graph/relations/review-queue?status=suggested&page=1&page_size=20', {
    decode: decodeRelationReviewQueue,
    signal,
  })
}
