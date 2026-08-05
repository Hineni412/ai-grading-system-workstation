export interface GraphScope {
  mode: 'all' | 'student' | 'selected' | 'class'
  student_ids: string[]
  class_id: string | null
  class_ids?: string[]
  score_rate_min?: number | null
  score_rate_max?: number | null
  include_student_ids?: string[]
  exclude_student_ids?: string[]
  use_historical_fallback?: boolean
  matched_student_count?: number
  scope_revision?: string
  student_score_profiles?: Record<string, Record<string, unknown>>
}

export interface GraphExamScope {
  mode: 'current' | 'manual' | 'cross_exam'
  session_ids: number[]
  sessions: Array<{ session_id: number; session_name: string }>
}

export interface GraphStudentScopeInput {
  mode: 'all' | 'class' | 'student' | 'selected'
  class_id?: string
  class_ids?: string[]
  student_ids?: string[]
  score_rate_min?: number | null
  score_rate_max?: number | null
  include_student_ids?: string[]
  exclude_student_ids?: string[]
  use_historical_fallback?: boolean
}

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

function requirePositiveInteger(value: number, label: string): number {
  if (!Number.isSafeInteger(value) || value < 1) throw new Error(`Invalid ${label}`)
  return value
}

function requireText(value: string, label: string): string {
  const normalized = value.trim()
  if (!normalized) throw new Error(`Invalid ${label}`)
  return normalized
}

function normalizeStudentIds(values: string[]): string[] {
  const result: string[] = []
  for (const rawValue of values) {
    const value = requireText(String(rawValue), 'student id')
    if (!result.includes(value)) result.push(value)
  }
  return result
}

function normalizeOptionalRate(value: number | null | undefined, label: string): number | undefined {
  if (value === null || value === undefined) return undefined
  if (!Number.isFinite(value) || value < 0 || value > 1) throw new Error(`Invalid ${label}`)
  return Math.round(value * 10_000) / 10_000
}

function normalizeSessionIds(values: number[]): number[] {
  const result: number[] = []
  for (const rawValue of values) {
    const value = requirePositiveInteger(rawValue, 'session id')
    if (!result.includes(value)) result.push(value)
  }
  return result
}

export function normalizeGraphQuery(query: GraphQueryInput): GraphQueryInput {
  const minimum = normalizeOptionalRate(query.scope.score_rate_min, 'minimum score rate')
  const maximum = normalizeOptionalRate(query.scope.score_rate_max, 'maximum score rate')
  if (minimum !== undefined && maximum !== undefined && minimum > maximum) {
    throw new Error('Invalid score rate range')
  }
  const common = {
    ...(minimum !== undefined ? { score_rate_min: minimum } : {}),
    ...(maximum !== undefined ? { score_rate_max: maximum } : {}),
    ...(normalizeStudentIds(query.scope.include_student_ids ?? []).length
      ? { include_student_ids: normalizeStudentIds(query.scope.include_student_ids ?? []) }
      : {}),
    ...(normalizeStudentIds(query.scope.exclude_student_ids ?? []).length
      ? { exclude_student_ids: normalizeStudentIds(query.scope.exclude_student_ids ?? []) }
      : {}),
    ...(query.scope.use_historical_fallback === false
      ? { use_historical_fallback: false }
      : {}),
  }
  let scope: GraphStudentScopeInput
  if (query.scope.mode === 'all') {
    scope = { mode: 'all', ...common }
  } else if (query.scope.mode === 'class') {
    const classIds = normalizeStudentIds([
      ...(query.scope.class_ids ?? []),
      ...(query.scope.class_id ? [query.scope.class_id] : []),
    ])
    if (!classIds.length) throw new Error('Invalid class scope')
    scope = {
      mode: 'class',
      class_ids: classIds,
      ...(classIds.length === 1 ? { class_id: classIds[0] } : {}),
      ...common,
    }
  } else {
    const studentIds = normalizeStudentIds(query.scope.student_ids ?? [])
    if (studentIds.length === 0) throw new Error('Invalid student scope')
    scope = { mode: query.scope.mode, student_ids: studentIds, ...common }
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

export function graphQueryForClass(
  sessionId: number,
  className: string,
): GraphQueryInput {
  return normalizeGraphQuery({
    scope: { mode: 'class', class_id: requireText(className, 'class name') },
    exam_scope: {
      mode: 'current',
      session_ids: [requirePositiveInteger(sessionId, 'session id')],
    },
  })
}
