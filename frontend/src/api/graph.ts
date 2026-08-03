import { apiClient } from './client'
import {
  normalizeGraphQuery,
  type GraphCoverage,
  type GraphExamScope,
  type GraphQueryInput,
  type GraphScope,
} from './graph-query'
import { isRecord } from './validation'

export {
  graphQueryForClass,
  normalizeGraphQuery,
} from './graph-query'
export type {
  GraphCoverage,
  GraphExamScope,
  GraphExamScopeInput,
  GraphQueryInput,
  GraphScope,
  GraphStudentScopeInput,
} from './graph-query'

export type GraphRelationType = 'parent' | 'prerequisite' | 'related'
export type GraphMasteryStatus = 'available' | 'missing' | 'unavailable'
export type GraphRelationBasis =
  | 'mathematical_logic'
  | 'curriculum_structure'
  | 'multi_textbook_sequence'
  | 'teacher_judgment'
  | 'empirical_evidence'
export type GraphRelationStrength = 'required' | 'recommended' | 'contextual'

export interface GraphMastery {
  status: GraphMasteryStatus
  value: number | null
  evidence_count: number
  parameter_version: string | null
  reason: string | null
}

export interface GraphEvidenceSummary {
  student_count: number
  item_count: number
  deduction_count: number
  tag_context: Record<string, string[]>
  error_counts: Record<string, Record<string, number>>
}

export interface GraphNode {
  stable_key: string
  display_name: string
  definition: string
  include_scope: string
  exclude_scope: string
  curriculum_anchors: string[]
  observable_evidence: string
  rationale: string
  evidence_source_ids: string[]
  mastery: GraphMastery
  evidence: GraphEvidenceSummary
  missing_reasons: string[]
}

export interface GraphEdge {
  relation_key: string
  source_key: string
  target_key: string
  relation_type: GraphRelationType
  rationale: string
  basis_kind: GraphRelationBasis
  strength: GraphRelationStrength
  evidence_source_ids: string[]
  source_locator: string
}

export interface CurrentGraphStandard {
  release_id: string
  content_hash: string
  taxonomy_revision: number
}

export interface GraphResponse {
  response_schema_version: 'knowledge-graph-current'
  response_version: string
  scope: GraphScope
  exam_scope: GraphExamScope
  coverage: GraphCoverage
  current_standard: CurrentGraphStandard
  nodes: GraphNode[]
  edges: GraphEdge[]
  missing: Array<Record<string, unknown>>
  warnings: string[]
  counts: Record<string, number>
}

export interface GraphEvidenceItem {
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

export interface GraphEvidenceResponse {
  response_schema_version: 'knowledge-graph-evidence-current'
  response_version: string
  scope: GraphScope
  exam_scope: GraphExamScope
  coverage: GraphCoverage
  current_standard: CurrentGraphStandard
  stable_key: string
  display_name: string
  items: GraphEvidenceItem[]
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
  relation_type: GraphRelationType
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

export interface RelationBatchReviewResponse {
  status: 'applied' | 'partial' | 'failed'
  applied_count: number
  failed_count: number
  results: Array<Record<string, unknown>>
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

function isSha256(value: unknown): value is string {
  return typeof value === 'string' && /^[0-9a-f]{64}$/.test(value)
}

function isScope(value: unknown): value is GraphScope {
  return isRecord(value)
    && hasExactKeys(value, ['mode', 'student_ids', 'class_id'])
    && (value.mode === 'student' || value.mode === 'selected' || value.mode === 'class')
    && isStringArray(value.student_ids)
    && (value.class_id === null || typeof value.class_id === 'string')
}

function isExamScope(value: unknown): value is GraphExamScope {
  return isRecord(value)
    && hasExactKeys(value, ['mode', 'session_ids', 'sessions'])
    && (value.mode === 'current' || value.mode === 'manual' || value.mode === 'cross_exam')
    && Array.isArray(value.session_ids)
    && value.session_ids.every((id) => isInteger(id, true))
    && Array.isArray(value.sessions)
    && value.sessions.every((session) => isRecord(session)
      && hasExactKeys(session, ['session_id', 'session_name'])
      && isInteger(session.session_id, true)
      && typeof session.session_name === 'string')
}

function hasAlignedExamScope(scope: GraphExamScope): boolean {
  return scope.sessions.length === scope.session_ids.length
    && scope.sessions.every((session, index) => session.session_id === scope.session_ids[index])
}

function isCoverage(value: unknown): value is GraphCoverage {
  return isRecord(value)
    && hasExactKeys(value, ['covered_items', 'total_items', 'missing_items'])
    && isInteger(value.covered_items)
    && isInteger(value.total_items)
    && value.covered_items <= value.total_items
    && isStringMap(value.missing_items)
}

function isMastery(value: unknown): value is GraphMastery {
  if (!isRecord(value)
    || !hasExactKeys(value, ['status', 'value', 'evidence_count', 'parameter_version', 'reason'])
    || !['available', 'missing', 'unavailable'].includes(String(value.status))
    || !isInteger(value.evidence_count)
    || (value.parameter_version !== null && !isSha256(value.parameter_version))
    || (value.reason !== null && typeof value.reason !== 'string')) return false
  if (value.status === 'available') {
    return isRate(value.value) && value.evidence_count > 0 && isSha256(value.parameter_version)
  }
  return value.value === null
}

function isEvidenceSummary(value: unknown): value is GraphEvidenceSummary {
  return isRecord(value)
    && hasExactKeys(value, ['student_count', 'item_count', 'deduction_count', 'tag_context', 'error_counts'])
    && isInteger(value.student_count)
    && isInteger(value.item_count)
    && isInteger(value.deduction_count)
    && isStringArrayMap(value.tag_context)
    && isCountMap(value.error_counts)
}

function isNode(value: unknown): value is GraphNode {
  return isRecord(value)
    && hasExactKeys(value, [
      'stable_key', 'display_name', 'definition', 'include_scope', 'exclude_scope',
      'curriculum_anchors', 'observable_evidence', 'rationale', 'evidence_source_ids',
      'mastery', 'evidence', 'missing_reasons',
    ])
    && isStableKey(value.stable_key)
    && typeof value.display_name === 'string' && value.display_name.trim().length > 0
    && typeof value.definition === 'string'
    && typeof value.include_scope === 'string'
    && typeof value.exclude_scope === 'string'
    && isStringArray(value.curriculum_anchors)
    && typeof value.observable_evidence === 'string'
    && typeof value.rationale === 'string'
    && isStringArray(value.evidence_source_ids)
    && isMastery(value.mastery)
    && isEvidenceSummary(value.evidence)
    && isStringArray(value.missing_reasons)
}

function isRelationType(value: unknown): value is GraphRelationType {
  return value === 'parent' || value === 'prerequisite' || value === 'related'
}

function isEdge(value: unknown): value is GraphEdge {
  return isRecord(value)
    && hasExactKeys(value, [
      'relation_key', 'source_key', 'target_key', 'relation_type', 'rationale',
      'basis_kind', 'strength', 'evidence_source_ids', 'source_locator',
    ])
    && isSha256(value.relation_key)
    && isStableKey(value.source_key)
    && isStableKey(value.target_key)
    && value.source_key !== value.target_key
    && isRelationType(value.relation_type)
    && typeof value.rationale === 'string'
    && ['mathematical_logic', 'curriculum_structure', 'multi_textbook_sequence', 'teacher_judgment', 'empirical_evidence'].includes(String(value.basis_kind))
    && ['required', 'recommended', 'contextual'].includes(String(value.strength))
    && isStringArray(value.evidence_source_ids)
    && typeof value.source_locator === 'string'
}

function isCurrentStandard(value: unknown): value is CurrentGraphStandard {
  return isRecord(value)
    && hasExactKeys(value, ['release_id', 'content_hash', 'taxonomy_revision'])
    && typeof value.release_id === 'string' && value.release_id.trim().length > 0
    && isSha256(value.content_hash)
    && isInteger(value.taxonomy_revision, true)
}

function isCounts(value: unknown): value is Record<string, number> {
  return isRecord(value) && Object.values(value).every((count) => isInteger(count))
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

function matchesQuery(scope: GraphScope, examScope: GraphExamScope, query: GraphQueryInput): boolean {
  if (scope.mode !== query.scope.mode || examScope.mode !== query.exam_scope.mode) return false
  if (query.scope.mode === 'class') {
    if (scope.class_id !== query.scope.class_id) return false
    if (query.scope.student_ids && !isOrderedSubset(scope.student_ids, query.scope.student_ids)) return false
  } else if (!isOrderedSubset(scope.student_ids, query.scope.student_ids)) return false
  return query.exam_scope.mode === 'cross_exam'
    || isOrderedSubset(examScope.session_ids, query.exam_scope.session_ids)
}

export function decodeGraphResponse(value: unknown, expected?: GraphQueryInput): GraphResponse {
  if (!isRecord(value)
    || !hasExactKeys(value, [
      'response_schema_version', 'response_version', 'scope', 'exam_scope', 'coverage',
      'current_standard', 'nodes', 'edges', 'missing', 'warnings', 'counts',
    ])
    || value.response_schema_version !== 'knowledge-graph-current'
    || !isSha256(value.response_version)
    || !isScope(value.scope)
    || !isExamScope(value.exam_scope)
    || !isCoverage(value.coverage)
    || !isCurrentStandard(value.current_standard)
    || !Array.isArray(value.nodes) || !value.nodes.every(isNode)
    || !Array.isArray(value.edges) || !value.edges.every(isEdge)
    || !Array.isArray(value.missing) || !value.missing.every(isRecord)
    || !isStringArray(value.warnings)
    || !isCounts(value.counts)) throw new Error('Invalid knowledge graph response')
  const response = value as unknown as GraphResponse
  const nodeKeys = new Set(response.nodes.map((node) => node.stable_key))
  if (nodeKeys.size !== response.nodes.length
    || !hasAlignedExamScope(response.exam_scope)
    || response.edges.some((edge) => !nodeKeys.has(edge.source_key) || !nodeKeys.has(edge.target_key))
    || response.counts.node_count !== response.nodes.length
    || response.counts.edge_count !== response.edges.length
    || (expected && !matchesQuery(response.scope, response.exam_scope, expected))) {
    throw new Error('Invalid knowledge graph response')
  }
  return response
}

function isEvidenceItem(value: unknown): value is GraphEvidenceItem {
  return isRecord(value)
    && isInteger(value.student_id, true)
    && typeof value.student_code === 'string'
    && typeof value.student_name === 'string'
    && typeof value.class_id === 'string'
    && isStableKey(value.stable_key)
    && typeof value.knowledge_key === 'string'
    && typeof value.knowledge_label === 'string'
    && isInteger(value.session_id, true)
    && typeof value.session_name === 'string'
    && typeof value.question_id === 'string'
    && isInteger(value.bank_question_id, true)
    && isNumber(value.score_awarded)
    && isNumber(value.full_score)
    && (value.score_rate === null || isRate(value.score_rate))
    && isStringArrayMap(value.tag_context)
    && isStringArray(value.actionable_reasons)
    && isCountMap(value.error_counts)
}

export function decodeGraphEvidenceResponse(
  value: unknown,
  expected: { query: GraphQueryInput; stableKey: string; page: number },
): GraphEvidenceResponse {
  if (!isRecord(value)
    || !hasExactKeys(value, [
      'response_schema_version', 'response_version', 'scope', 'exam_scope', 'coverage',
      'current_standard', 'stable_key', 'display_name', 'items', 'total', 'page',
      'page_size', 'total_pages',
    ])
    || value.response_schema_version !== 'knowledge-graph-evidence-current'
    || !isSha256(value.response_version)
    || !isScope(value.scope)
    || !isExamScope(value.exam_scope)
    || !isCoverage(value.coverage)
    || !isCurrentStandard(value.current_standard)
    || !isStableKey(value.stable_key)
    || typeof value.display_name !== 'string'
    || !Array.isArray(value.items) || !value.items.every(isEvidenceItem)
    || !isInteger(value.total)
    || !isInteger(value.page, true)
    || !isInteger(value.page_size, true)
    || !isInteger(value.total_pages, true)) throw new Error('Invalid knowledge graph evidence')
  const response = value as unknown as GraphEvidenceResponse
  if (response.stable_key !== expected.stableKey
    || response.page !== expected.page
    || response.page > response.total_pages
    || !hasAlignedExamScope(response.exam_scope)
    || response.total_pages !== Math.max(1, Math.ceil(response.total / response.page_size))
    || response.items.some((item) => item.stable_key !== response.stable_key)
    || !matchesQuery(response.scope, response.exam_scope, expected.query)) {
    throw new Error('Invalid knowledge graph evidence')
  }
  return response
}

function isQueueItem(value: unknown): value is RelationReviewQueueItem {
  return isRecord(value)
    && typeof value.relation_id === 'string'
    && isStableKey(value.source_key)
    && typeof value.source_name === 'string'
    && isStableKey(value.target_key)
    && typeof value.target_name === 'string'
    && isRelationType(value.relation_type)
    && typeof value.source_kind === 'string'
    && (value.source_reference === null || typeof value.source_reference === 'string')
    && typeof value.rationale === 'string'
    && (value.model_name === null || typeof value.model_name === 'string')
    && (value.model_version === null || typeof value.model_version === 'string')
    && (value.prompt_version === null || typeof value.prompt_version === 'string')
    && (value.confidence === null || isRate(value.confidence))
    && isStringArray(value.conflict_codes)
    && isInteger(value.revision, true)
    && typeof value.updated_at === 'string'
}

export function decodeRelationReviewQueue(value: unknown): RelationReviewQueueResponse {
  if (!isRecord(value)
    || value.status !== 'suggested'
    || !Array.isArray(value.items) || !value.items.every(isQueueItem)
    || !isInteger(value.total)
    || !isInteger(value.page, true)
    || !isInteger(value.page_size, true)
    || !isInteger(value.total_pages, true)) throw new Error('Invalid relation review queue')
  return value as unknown as RelationReviewQueueResponse
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

export function fetchGraph(
  query: GraphQueryInput,
  signal?: AbortSignal,
  knowledgeKeys: string[] = [],
  prerequisiteDepth = 1,
): Promise<GraphResponse> {
  const normalized = normalizeGraphQuery(query)
  const keys = normalizeStableKeys(knowledgeKeys)
  if (!Number.isSafeInteger(prerequisiteDepth) || prerequisiteDepth < 0 || prerequisiteDepth > 5) {
    throw new Error('Invalid prerequisite depth')
  }
  return apiClient.request('/api/graph/query', {
    method: 'POST',
    body: { ...normalized, knowledge_keys: keys, prerequisite_depth: prerequisiteDepth },
    decode: (value) => decodeGraphResponse(value, normalized),
    signal,
  })
}

export function fetchGraphEvidence(
  query: GraphQueryInput,
  stableKey: string,
  signal?: AbortSignal,
  page = 1,
): Promise<GraphEvidenceResponse> {
  const normalized = normalizeGraphQuery(query)
  const [key] = normalizeStableKeys([stableKey])
  const normalizedPage = Number.isSafeInteger(page) && page > 0 ? page : 1
  return apiClient.request('/api/graph/evidence', {
    method: 'POST',
    body: { ...normalized, stable_key: key, page: normalizedPage, page_size: 20 },
    decode: (value) => decodeGraphEvidenceResponse(value, {
      query: normalized,
      stableKey: key!,
      page: normalizedPage,
    }),
    signal,
  })
}

export function fetchRelationReviewQueue(signal?: AbortSignal): Promise<RelationReviewQueueResponse> {
  return apiClient.request('/api/graph/relations/review-queue?status=suggested&page=1&page_size=20', {
    decode: decodeRelationReviewQueue,
    signal,
  })
}

function decodeRelationBatchReview(value: unknown): RelationBatchReviewResponse {
  if (!isRecord(value)
    || !['applied', 'partial', 'failed'].includes(String(value.status))
    || !isInteger(value.applied_count)
    || !isInteger(value.failed_count)
    || !Array.isArray(value.results)
    || !value.results.every(isRecord)) throw new Error('Invalid relation batch review response')
  return value as unknown as RelationBatchReviewResponse
}

export function reviewRelationExceptions(input: {
  items: RelationReviewQueueItem[]
  action: 'confirm' | 'reject'
}): Promise<RelationBatchReviewResponse> {
  return apiClient.request('/api/graph/relations/review-batch', {
    method: 'POST',
    body: {
      teacher_ref: 'teacher:local-workbench',
      commands: input.items.map((item) => ({
        relation_id: item.relation_id,
        expected_revision: item.revision,
        action: input.action,
        reason: input.action === 'confirm'
          ? '教师在异常队列中确认 AI 建议'
          : '教师在异常队列中拒绝 AI 建议',
      })),
    },
    decode: decodeRelationBatchReview,
  })
}
