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

function normalizeSessionIds(values: number[]): number[] {
  const result: number[] = []
  for (const rawValue of values) {
    const value = requirePositiveInteger(rawValue, 'session id')
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
      class_id: requireText(query.scope.class_id, 'class name'),
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
