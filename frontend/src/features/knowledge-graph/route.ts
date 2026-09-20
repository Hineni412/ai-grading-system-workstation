import type { GraphQueryInput } from '../../api/graph'

type RouteQuery = Record<string, unknown>

interface SessionOption {
  id: number
}

interface StudentOption {
  id: number
  class_name: string | null
}

export interface ParsedGraphRouteScope {
  query: GraphQueryInput | null
  canonical: Record<string, string>
  notice: string
}

const CONTROLLED_KEYS = new Set([
  'exam', 'sessions', 'scope', 'class', 'students', 'session',
  'min', 'max', 'include', 'exclude', 'history', 'volume',
])

function singleText(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

function parseIntegerList(value: unknown): number[] | null {
  const text = singleText(value)
  if (text === null) return null
  const values = text.split(',')
  if (values.some((item) => !/^\d+$/.test(item.trim()))) return null
  const result = [...new Set(values.map((item) => Number(item.trim())))]
  return result.every((item) => Number.isSafeInteger(item) && item > 0) ? result : null
}

function parseStudentList(value: unknown): string[] | null {
  const values = parseIntegerList(value)
  return values?.map(String) ?? null
}

export function serializeGraphRouteScope(query: GraphQueryInput): Record<string, string> {
  const result: Record<string, string> = {
    exam: query.exam_scope.mode,
    scope: 'snapshot',
  }
  if (query.exam_scope.mode === 'semester') {
    result.volume = query.exam_scope.curriculum_volume_id
  } else if (query.exam_scope.mode !== 'cross_exam') {
    result.sessions = query.exam_scope.session_ids.join(',')
  }
  return result
}

export function parseGraphRouteScope(
  routeQuery: RouteQuery,
  sessions: SessionOption[],
  students: StudentOption[],
): ParsedGraphRouteScope {
  const keys = Object.keys(routeQuery)
  const hasUnknownKeys = keys.some((key) => !CONTROLLED_KEYS.has(key))
  const hasLegacy = 'session' in routeQuery
  const usesLegacyFormat = hasLegacy && !('exam' in routeQuery) && !('scope' in routeQuery)
  const hasControlledScope = keys.some((key) => CONTROLLED_KEYS.has(key))
  if (!hasControlledScope) {
    return {
      query: null,
      canonical: {},
      notice: hasUnknownKeys ? '已忽略无效地址参数，请重新选择查看范围' : '',
    }
  }

  if (singleText(routeQuery.scope) === 'snapshot') {
    const exam = singleText(routeQuery.exam)
    const sessionsValue = singleText(routeQuery.sessions)
    return {
      query: null,
      canonical: {
        ...(exam ? { exam } : {}),
        scope: 'snapshot',
        ...(sessionsValue ? { sessions: sessionsValue } : {}),
      },
      notice: hasUnknownKeys ? '已忽略无效地址参数' : '',
    }
  }

  let query: GraphQueryInput | null = null
  if (usesLegacyFormat) {
    const sessionId = parseIntegerList(routeQuery.session)
    const classId = singleText(routeQuery.class)
    if (sessionId?.length === 1 && classId !== null) {
      query = {
        scope: { mode: 'class', class_id: classId },
        exam_scope: { mode: 'current', session_ids: [sessionId[0]!] },
      }
    }
  } else {
    const examMode = singleText(routeQuery.exam)
    const scopeMode = singleText(routeQuery.scope)
    if (examMode === 'current') {
      const sessionIds = parseIntegerList(routeQuery.sessions)
      if (sessionIds?.length === 1) {
        query = {
          scope: { mode: 'all' },
          exam_scope: { mode: 'current', session_ids: [sessionIds[0]!] },
        }
      }
    } else if (examMode === 'manual') {
      const sessionIds = parseIntegerList(routeQuery.sessions)
      if (sessionIds?.length) {
        query = {
          scope: { mode: 'all' },
          exam_scope: { mode: 'manual', session_ids: sessionIds },
        }
      }
    } else if (examMode === 'cross_exam') {
      query = {
        scope: { mode: 'all' },
        exam_scope: { mode: 'cross_exam' },
      }
    }

    if (query && scopeMode === 'all') {
      query = { ...query, scope: { mode: 'all' } }
    } else if (query && scopeMode === 'class') {
      const classId = singleText(routeQuery.class)
      query = classId ? { ...query, scope: { mode: 'class', class_id: classId } } : null
    } else if (query && (scopeMode === 'student' || scopeMode === 'selected')) {
      const studentIds = parseStudentList(routeQuery.students)
      query = studentIds?.length
        ? { ...query, scope: { mode: scopeMode, student_ids: studentIds } }
        : null
    } else {
      query = null
    }
    if (query) {
      const minimum = singleText(routeQuery.min)
      const maximum = singleText(routeQuery.max)
      const minValue = minimum === null ? undefined : Number(minimum)
      const maxValue = maximum === null ? undefined : Number(maximum)
      const include = parseStudentList(routeQuery.include) ?? []
      const exclude = parseStudentList(routeQuery.exclude) ?? []
      const history = singleText(routeQuery.history)
      if ((minValue !== undefined && (!Number.isFinite(minValue) || minValue < 0 || minValue > 1))
        || (maxValue !== undefined && (!Number.isFinite(maxValue) || maxValue < 0 || maxValue > 1))) {
        query = null
      } else {
        query = {
          ...query,
          scope: {
            ...query.scope,
            ...(minValue !== undefined ? { score_rate_min: minValue } : {}),
            ...(maxValue !== undefined ? { score_rate_max: maxValue } : {}),
            ...(include.length ? { include_student_ids: include } : {}),
            ...(exclude.length ? { exclude_student_ids: exclude } : {}),
            ...(history !== null ? { use_historical_fallback: history !== '0' } : {}),
          },
        }
      }
    }
  }

  const validSessions = new Set(sessions.map((session) => session.id))
  const validStudents = new Set(students.map((student) => String(student.id)))
  const validClasses = new Set(students.flatMap((student) => (
    student.class_name ? [student.class_name] : []
  )))
  const requestedSessions = query?.exam_scope.mode === 'cross_exam' || query?.exam_scope.mode === 'semester'
    ? []
    : query?.exam_scope.session_ids ?? []
  const valid = query !== null &&
    requestedSessions.every((id) => validSessions.has(id)) &&
    (query.scope.mode === 'all'
      ? true
      : query.scope.mode === 'class'
        ? !query.scope.class_id || validClasses.has(query.scope.class_id)
        : (query.scope.student_ids ?? []).every((id) => validStudents.has(id)))
    && (query.scope.include_student_ids ?? []).every((id) => validStudents.has(id))
    && (query.scope.exclude_student_ids ?? []).every((id) => validStudents.has(id))

  if (!valid || query === null) {
    return {
      query: null,
      canonical: {},
      notice: '地址中的考试、班级或学生范围已不可用，请重新选择查看范围',
    }
  }

  const canonical = serializeGraphRouteScope(query)
  const hasIgnoredControlledKeys = usesLegacyFormat
    ? keys.some((key) => CONTROLLED_KEYS.has(key) && key !== 'session' && key !== 'class')
    : keys.some((key) => (
        CONTROLLED_KEYS.has(key) && !Object.prototype.hasOwnProperty.call(canonical, key)
      ))
  return {
    query,
    canonical,
    notice: hasUnknownKeys || hasIgnoredControlledKeys ? '已忽略无效地址参数' : '',
  }
}
