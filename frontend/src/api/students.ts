import { apiClient } from './client'
import { isNullableString, isRecord } from './validation'

export interface StudentSummary {
  id: number
  student_code: string
  name: string
  class_name: string | null
  created_at: string | null
}

interface StudentListResponse {
  items: StudentSummary[]
  total: number
}

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(value)
  return actual.length === keys.length && keys.every(
    (key) => Object.prototype.hasOwnProperty.call(value, key),
  )
}

function isStudentSummary(value: unknown): value is StudentSummary {
  return (
    isRecord(value) &&
    hasExactKeys(value, ['id', 'student_code', 'name', 'class_name', 'created_at']) &&
    Number.isSafeInteger(value.id) &&
    Number(value.id) > 0 &&
    typeof value.student_code === 'string' &&
    typeof value.name === 'string' &&
    isNullableString(value.class_name) &&
    isNullableString(value.created_at)
  )
}

export function decodeStudentList(value: unknown): StudentSummary[] {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ['items', 'total']) ||
    !Array.isArray(value.items) ||
    !value.items.every(isStudentSummary) ||
    !Number.isSafeInteger(value.total) ||
    Number(value.total) < 0 ||
    value.total !== value.items.length
  ) {
    throw new Error('Invalid student list')
  }
  return (value as unknown as StudentListResponse).items
}

export class StudentReadError extends Error {
  constructor() {
    super('无法读取学生列表')
    this.name = 'StudentReadError'
  }
}

export async function fetchStudents(signal?: AbortSignal): Promise<StudentSummary[]> {
  try {
    return await apiClient.request('/api/students', {
      decode: decodeStudentList,
      signal,
    })
  } catch {
    throw new StudentReadError()
  }
}
