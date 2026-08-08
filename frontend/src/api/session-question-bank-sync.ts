import { apiClient } from './client'
import { decodeJobResponse, type JobResponse } from './jobs'

export interface SessionQuestionBankSyncRequest {
  config_revision: string
  client_request_token: string
  curriculum_volume_id?: string
}

export interface SessionQuestionBankAnalysisStatus {
  question_count: number
  tagged_count: number
  evidence_count: number
  criteria_count: number
  complete_count: number
  pending_taxonomy_count: number
  incomplete_question_ids: number[]
  incomplete_source_refs: string[]
}

function isStatus(value: unknown): value is SessionQuestionBankAnalysisStatus {
  if (typeof value !== 'object' || value === null) return false
  const item = value as Record<string, unknown>
  const counts = [
    item.question_count,
    item.tagged_count,
    item.evidence_count,
    item.criteria_count,
    item.complete_count,
    item.pending_taxonomy_count,
  ]
  return Object.keys(item).length === 8
    && counts.every((count) => Number.isSafeInteger(count) && Number(count) >= 0)
    && Array.isArray(item.incomplete_question_ids)
    && item.incomplete_question_ids.every((id) => Number.isSafeInteger(id) && id > 0)
    && Array.isArray(item.incomplete_source_refs)
    && item.incomplete_source_refs.every((ref) => typeof ref === 'string')
}

function sessionPath(sessionId: number): string {
  if (!Number.isSafeInteger(sessionId) || sessionId <= 0) {
    throw new Error('Invalid session id')
  }
  return `/api/sessions/${sessionId}/question-bank-sync`
}

export async function submitSessionQuestionBankSync(
  sessionId: number,
  request: SessionQuestionBankSyncRequest,
): Promise<JobResponse> {
  return apiClient.request(sessionPath(sessionId), {
    method: 'POST',
    body: request,
    decode: decodeJobResponse,
  })
}

export async function retrySessionQuestionBankSync(
  sessionId: number,
  jobId: number,
  request: SessionQuestionBankSyncRequest,
): Promise<JobResponse> {
  if (!Number.isSafeInteger(jobId) || jobId <= 0) {
    throw new Error('Invalid job id')
  }
  return apiClient.request(`${sessionPath(sessionId)}/${jobId}/retry`, {
    method: 'POST',
    body: request,
    decode: decodeJobResponse,
  })
}

export async function getSessionQuestionBankAnalysisStatus(
  sessionId: number,
): Promise<SessionQuestionBankAnalysisStatus> {
  return apiClient.request(
    `/api/sessions/${sessionId}/question-bank-status`,
    {
      decode(value) {
        if (!isStatus(value)) throw new Error('Invalid question-bank status')
        return value
      },
    },
  )
}
