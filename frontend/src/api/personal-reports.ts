import { apiClient } from './client'
import { decodeJobResponse } from './jobs'
import { assertNoPathLikeKeys, isNullableString, isRecord } from './validation'

export type PersonalReportStatus = 'current' | 'stale' | 'missing' | 'unavailable'
export interface PersonalReportState {
  student_id: number
  status: PersonalReportStatus
  generated_at: string | null
  reason: string | null
}
export interface PersonalReportExam {
  session_id: number
  session_name: string
  graded_at: string
  score: number | null
  max_score: number
  status: PersonalReportStatus
  generated_at: string | null
  reason: string | null
}
export const reportStatusText: Record<PersonalReportStatus, string> = {
  current: '已生成', stale: '需重新生成', missing: '未生成', unavailable: '不可生成',
}
function positive(value: unknown): value is number { return Number.isSafeInteger(value) && Number(value) > 0 }
function status(value: unknown): value is PersonalReportStatus { return ['current', 'stale', 'missing', 'unavailable'].includes(String(value)) }
function decodeState(value: unknown): PersonalReportState {
  if (!isRecord(value) || !positive(value.student_id) || !status(value.status)
    || !isNullableString(value.generated_at) || !isNullableString(value.reason)) throw new Error('Invalid personal report state')
  return { student_id: value.student_id, status: value.status, generated_at: value.generated_at, reason: value.reason }
}
function ids(values: number[], limit: number): number[] {
  if (!values.length || values.length > limit || values.some(v => !positive(v))) throw new Error('Invalid report scope')
  return [...new Set(values)].sort((a, b) => a - b)
}
export const personalReportsApi = {
  async states(sessionId: number, signal?: AbortSignal): Promise<PersonalReportState[]> {
    if (!positive(sessionId)) throw new Error('Invalid session')
    return apiClient.request(`/api/sessions/${sessionId}/personal-reports`, { signal, timeoutMs: 60_000, decode(value) {
      assertNoPathLikeKeys(value)
      if (!isRecord(value) || value.session_id !== sessionId || !Array.isArray(value.students)) throw new Error('Invalid report states')
      return value.students.map(decodeState)
    } })
  },
  async exams(studentId: number, volume?: string | null, signal?: AbortSignal): Promise<PersonalReportExam[]> {
    if (!positive(studentId)) throw new Error('Invalid student')
    const query = volume ? `?curriculum_volume_id=${encodeURIComponent(volume)}` : ''
    return apiClient.request(`/api/students/${studentId}/personal-reports${query}`, { signal, timeoutMs: 60_000, decode(value) {
      assertNoPathLikeKeys(value)
      if (!isRecord(value) || value.student_id !== studentId || !Array.isArray(value.sessions)) throw new Error('Invalid report exams')
      return value.sessions.map(item => {
        if (!isRecord(item) || !positive(item.session_id) || typeof item.session_name !== 'string'
          || typeof item.graded_at !== 'string' || !status(item.status) || !isNullableString(item.reason)
          || !isNullableString(item.generated_at) || (item.score !== null && (typeof item.score !== 'number' || !Number.isFinite(item.score)))
          || typeof item.max_score !== 'number' || !Number.isFinite(item.max_score)) throw new Error('Invalid report exam')
        return { session_id: item.session_id, session_name: item.session_name, graded_at: item.graded_at,
          score: item.score as number | null, max_score: item.max_score, status: item.status,
          generated_at: item.generated_at, reason: item.reason }
      })
    } })
  },
  bundle(sessionIds: number[], studentIds: number[], scopeLabel: string) {
    return apiClient.request('/api/personal-reports/bundles', { method: 'POST',
      body: { session_ids: ids(sessionIds, 20), student_ids: ids(studentIds, 500), scope_label: scopeLabel }, decode: decodeJobResponse })
  },
  htmlUrl(sessionId: number, studentId: number, dataOnly: boolean, reviewLinks: boolean, version = '') {
    if (!positive(sessionId) || !positive(studentId)) throw new Error('Invalid report scope')
    return `/api/sessions/${sessionId}/personal-reports/${studentId}/html?narrative=${dataOnly ? 'none' : 'auto'}&review_links=${reviewLinks ? 1 : 0}&v=${encodeURIComponent(version)}`
  },
}

export function matchesReportStudent(student: {student_name: string; student_code: string | null; class_name: string | null; pinyin_initials?: string | null; pinyin_full?: string | null}, query: string): boolean {
  const text = query.trim().toLocaleLowerCase()
  return !text || [student.student_name, student.student_code, student.class_name, student.pinyin_initials,
    student.pinyin_full].some(value => value?.toLocaleLowerCase().includes(text))
}
