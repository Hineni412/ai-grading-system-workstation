import { apiClient } from './client'
import { decodeJobResponse, type JobResponse } from './jobs'

export interface SessionQuestionBankSyncRequest {
  config_revision: string
  client_request_token: string
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
