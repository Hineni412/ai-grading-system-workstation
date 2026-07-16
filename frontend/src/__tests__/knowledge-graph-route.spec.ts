import { describe, expect, it } from 'vitest'

import type { GraphQueryInput } from '../api/graph'
import {
  parseGraphRouteScope,
  serializeGraphRouteScope,
} from '../features/knowledge-graph/route'

const sessions = [
  { id: 7, name: '考试七' },
  { id: 8, name: '考试八' },
]
const students = [
  { id: 12, class_name: '七年级一班' },
  { id: 15, class_name: '七年级一班' },
  { id: 21, class_name: '七年级二班' },
]

const scopes: GraphQueryInput['scope'][] = [
  { mode: 'class', class_id: '七年级一班' },
  { mode: 'student', student_ids: ['12'] },
  { mode: 'selected', student_ids: ['12', '15'] },
]
const examScopes: GraphQueryInput['exam_scope'][] = [
  { mode: 'current', session_ids: [7] },
  { mode: 'manual', session_ids: [7, 8] },
  { mode: 'cross_exam' },
]

describe('knowledge graph controlled route scope', () => {
  it.each(scopes.flatMap((scope) => examScopes.map((examScope) => ({ scope, examScope }))))(
    'round-trips $scope.mode with $examScope.mode',
    ({ scope, examScope }) => {
      const query: GraphQueryInput = { scope, exam_scope: examScope }
      const serialized = serializeGraphRouteScope(query)
      expect(parseGraphRouteScope(serialized, sessions, students)).toEqual({
        query,
        canonical: serialized,
        notice: '',
      })
    },
  )

  it('accepts the workbench legacy context and canonicalizes it', () => {
    expect(parseGraphRouteScope(
      { session: '7', class: '七年级一班' },
      sessions,
      students,
    )).toEqual({
      query: {
        scope: { mode: 'class', class_id: '七年级一班' },
        exam_scope: { mode: 'current', session_ids: [7] },
      },
      canonical: {
        exam: 'current', sessions: '7', scope: 'class', class: '七年级一班',
      },
      notice: '',
    })
  })

  it('drops unknown parameters while keeping a valid scope and showing a notice', () => {
    const parsed = parseGraphRouteScope({
      exam: 'manual', sessions: '7,8', scope: 'selected', students: '12,15', debug: '1',
    }, sessions, students)
    expect(parsed.query).toEqual({
      scope: { mode: 'selected', student_ids: ['12', '15'] },
      exam_scope: { mode: 'manual', session_ids: [7, 8] },
    })
    expect(parsed.canonical).not.toHaveProperty('debug')
    expect(parsed.notice).toContain('已忽略无效地址参数')
  })

  it('rejects unavailable classes and student ids without guessing replacements', () => {
    expect(parseGraphRouteScope({
      exam: 'current', sessions: '7', scope: 'class', class: '不存在班级',
    }, sessions, students).query).toBeNull()
    expect(parseGraphRouteScope({
      exam: 'current', sessions: '7', scope: 'student', students: '999',
    }, sessions, students).query).toBeNull()
  })
})
