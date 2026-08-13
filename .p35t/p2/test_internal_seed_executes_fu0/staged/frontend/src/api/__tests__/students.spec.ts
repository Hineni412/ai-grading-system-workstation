import { afterEach, describe, expect, it, vi } from 'vitest'

const student = {
  id: 12,
  student_code: 'S012',
  name: '匿名学生甲',
  class_name: '七年级一班',
  created_at: '2026-07-15T08:00:00Z',
}

afterEach(() => vi.restoreAllMocks())

describe('student read client', () => {
  it('accepts only the public student-list fields', async () => {
    const { decodeStudentList } = await import('../students')
    expect(decodeStudentList({ items: [student], total: 1 })).toEqual([student])
    expect(() => decodeStudentList({
      items: [{ ...student, private_path: 'internal' }],
      total: 1,
    })).toThrow('Invalid student list')
  })

  it('uses a fixed safe error when the request fails', async () => {
    const { fetchStudents, StudentReadError } = await import('../students')
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('internal database path'))
    await expect(fetchStudents()).rejects.toBeInstanceOf(StudentReadError)
    await expect(fetchStudents()).rejects.toThrow('无法读取学生列表')
  })
})
