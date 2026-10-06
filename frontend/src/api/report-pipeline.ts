import { apiClient } from './client'
import { decodeJobResponse, type JobResponse } from './jobs'
import {
  assertNoPathLikeKeys,
  isNonnegativeInteger,
  isNullableString,
  isRecord,
} from './validation'

export interface ReportPipelineCausesStatus {
  pending_questions: number
  total_questions: number
  call_count: number
  estimated_tokens: number
}

export interface ReportPipelineClassesStatus {
  pending: number
  total: number
}

export interface ReportPipelinePersonalStatus {
  pending: number
  total: number
  call_count: number
  cache_hits: number
  estimated_tokens: number
}

export interface ReportPipelineStatus {
  auto_generate: boolean
  configured: boolean
  service_name: string | null
  model_name: string | null
  active_job_id: number | null
  review_pending: number
  causes: ReportPipelineCausesStatus
  class_reports: ReportPipelineClassesStatus
  personal_reports: ReportPipelinePersonalStatus
  complete: boolean
}

function decodeActiveJobId(value: unknown): number | null {
  if (typeof value === 'number' && Number.isSafeInteger(value) && value > 0) return value
  if (typeof value === 'string' && /^\d+$/.test(value)) {
    const parsed = Number(value)
    if (Number.isSafeInteger(parsed) && parsed > 0) return parsed
  }
  return null
}

function decodeStatus(value: unknown): ReportPipelineStatus {
  assertNoPathLikeKeys(value)
  if (
    !isRecord(value)
    || typeof value.auto_generate !== 'boolean'
    || typeof value.configured !== 'boolean'
    || !isNullableString(value.service_name)
    || !isNullableString(value.model_name)
    || !isNonnegativeInteger(value.review_pending)
    || !isRecord(value.causes)
    || !isNonnegativeInteger(value.causes.pending_questions)
    || !isNonnegativeInteger(value.causes.total_questions)
    || !isNonnegativeInteger(value.causes.call_count)
    || !isNonnegativeInteger(value.causes.estimated_tokens)
    || !isRecord(value.class_reports)
    || !isNonnegativeInteger(value.class_reports.pending)
    || !isNonnegativeInteger(value.class_reports.total)
    || !isRecord(value.personal_reports)
    || !isNonnegativeInteger(value.personal_reports.pending)
    || !isNonnegativeInteger(value.personal_reports.total)
    || !isNonnegativeInteger(value.personal_reports.call_count)
    || !isNonnegativeInteger(value.personal_reports.cache_hits)
    || !isNonnegativeInteger(value.personal_reports.estimated_tokens)
    || typeof value.complete !== 'boolean'
  ) {
    throw new Error('Invalid report pipeline status')
  }
  return {
    auto_generate: value.auto_generate,
    configured: value.configured,
    service_name: value.service_name,
    model_name: value.model_name,
    active_job_id: decodeActiveJobId(value.active_job_id),
    review_pending: value.review_pending,
    causes: value.causes as unknown as ReportPipelineCausesStatus,
    class_reports: value.class_reports as unknown as ReportPipelineClassesStatus,
    personal_reports: value.personal_reports as unknown as ReportPipelinePersonalStatus,
    complete: value.complete,
  }
}

function requireSessionId(sessionId: number): number {
  if (!Number.isSafeInteger(sessionId) || sessionId <= 0) {
    throw new Error('Invalid session id')
  }
  return sessionId
}

export const reportPipelineApi = {
  async getStatus(sessionId: number, signal?: AbortSignal): Promise<ReportPipelineStatus> {
    const id = requireSessionId(sessionId)
    return apiClient.request(`/api/sessions/${id}/report-pipeline`, {
      decode: decodeStatus,
      signal,
    })
  },

  async start(sessionId: number, signal?: AbortSignal): Promise<JobResponse> {
    const id = requireSessionId(sessionId)
    return apiClient.request(`/api/sessions/${id}/report-pipeline`, {
      method: 'POST',
      decode: decodeJobResponse,
      signal,
    })
  },
}
