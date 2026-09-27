import { describe, expect, it } from 'vitest';

import { parseGraphRouteScope } from '../features/knowledge-graph/route';

const sessions = [
  { id: 7, name: '考试七' },
  { id: 8, name: '考试八' },
]
const students = [
  { id: 12, class_name: '七年级一班' },
  { id: 15, class_name: '七年级一班' },
  { id: 21, class_name: '七年级二班' },
]

describe('knowledge graph controlled route scope', () => {

  it('rejects unavailable classes and student ids without guessing replacements', () => {
    expect(parseGraphRouteScope({
      exam: 'current', sessions: '7', scope: 'class', class: '不存在班级',
    }, sessions, students).query).toBeNull()
    expect(parseGraphRouteScope({
      exam: 'current', sessions: '7', scope: 'student', students: '999',
    }, sessions, students).query).toBeNull()
  })
})
