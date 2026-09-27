import { ref } from 'vue'
import { defineStore } from 'pinia'

import {
  exportsApi,
  type DownloadedJobFile,
  type ReportContext,
  type ReportFileDeleteResult,
  type ReportType,
  type ScoreExcelOptions,
} from '../api/exports'
import { TERMINAL_JOB_STATUSES, type JobResponse } from '../api/jobs'
import { useJobStore } from './jobs'

export type FileCenterState =
  | 'idle'
  | 'loading'
  | 'ready'
  | 'empty'
  | 'error'
  | 'stale-error'

export interface FileCenterApi {
  getReportContext(
    sessionId: number,
    page?: number,
    pageSize?: number,
    signal?: AbortSignal,
  ): Promise<ReportContext>
  submitReport(
    sessionId: number,
    reportType: ReportType,
    forceRegenerate?: boolean,
    excelOptions?: ScoreExcelOptions,
    signal?: AbortSignal,
  ): Promise<JobResponse>
  downloadJobFile(id: number): Promise<DownloadedJobFile>
  deleteReportFile(
    sessionId: number,
    jobId: number,
    signal?: AbortSignal,
  ): Promise<ReportFileDeleteResult>
}

function safeErrorMessage(): string {
  return '文件登记簿暂时无法更新，请稍后重试。'
}

export const useFileCenterStore = defineStore('file-center', () => {
  const sessionId = ref<number | null>(null)
  const reportContext = ref<ReportContext | null>(null)
  const state = ref<FileCenterState>('idle')
  const errorMessage = ref('')
  const updatedAt = ref<string | null>(null)
  const submittingKey = ref<string | null>(null)

  let generation = 0
  let controller: AbortController | null = null

  async function load(
    nextSessionId: number,
    api: FileCenterApi = exportsApi,
  ): Promise<void> {
    if (!Number.isSafeInteger(nextSessionId) || nextSessionId <= 0) {
      throw new Error('Invalid session id')
    }
    controller?.abort()
    const nextController = new AbortController()
    controller = nextController
    const requestGeneration = ++generation
    const isSameSession = sessionId.value === nextSessionId
    if (!isSameSession) {
      sessionId.value = nextSessionId
      reportContext.value = null
      updatedAt.value = null
    }
    state.value = 'loading'
    errorMessage.value = ''

    try {
      const nextReports = await api.getReportContext(
        nextSessionId,
        1,
        100,
        nextController.signal,
      )
      if (
        requestGeneration !== generation
        || nextController.signal.aborted
        || sessionId.value !== nextSessionId
      ) return
      reportContext.value = nextReports
      const jobStore = useJobStore()
      for (const job of nextReports.jobs) {
        if (!TERMINAL_JOB_STATUSES.has(job.status)) jobStore.track(job)
      }
      state.value = nextReports.jobs.length > 0 ? 'ready' : 'empty'
      updatedAt.value = new Date().toISOString()
    } catch {
      if (
        requestGeneration !== generation
        || nextController.signal.aborted
        || sessionId.value !== nextSessionId
      ) return
      state.value = updatedAt.value === null ? 'error' : 'stale-error'
      errorMessage.value = safeErrorMessage()
    } finally {
      if (controller === nextController) controller = null
    }
  }

  async function submitReport(
    nextSessionId: number,
    reportType: ReportType,
    forceRegenerate: boolean,
    excelOptions: ScoreExcelOptions | undefined = undefined,
    api: FileCenterApi = exportsApi,
  ): Promise<JobResponse> {
    const key = `report:${reportType}`
    if (submittingKey.value === key) throw new Error('Report submission is already in progress')
    submittingKey.value = key
    try {
      const job = excelOptions === undefined
        ? await api.submitReport(
          nextSessionId,
          reportType,
          forceRegenerate,
        )
        : await api.submitReport(
          nextSessionId,
          reportType,
          forceRegenerate,
          excelOptions,
        )
      useJobStore().track(job)
      return job
    } finally {
      if (submittingKey.value === key) submittingKey.value = null
    }
  }

  async function download(
    jobId: number,
    api: FileCenterApi = exportsApi,
  ): Promise<DownloadedJobFile> {
    return api.downloadJobFile(jobId)
  }

  async function deleteReport(
    jobId: number,
    api: FileCenterApi = exportsApi,
  ): Promise<ReportFileDeleteResult> {
    const currentSessionId = sessionId.value
    if (currentSessionId === null) throw new Error('No session selected')
    const result = await api.deleteReportFile(currentSessionId, jobId)
    await load(currentSessionId)
    return result
  }

  async function cancelTrackedJob(
    jobId: number,
    canceller: (id: number) => Promise<void> = (id) => useJobStore().cancel(id),
  ): Promise<void> {
    await canceller(jobId)
  }

  function reset(): void {
    controller?.abort()
    controller = null
    generation += 1
    sessionId.value = null
    reportContext.value = null
    state.value = 'idle'
    errorMessage.value = ''
    updatedAt.value = null
    submittingKey.value = null
  }

  return {
    sessionId,
    reportContext,
    state,
    errorMessage,
    updatedAt,
    submittingKey,
    load,
    submitReport,
    download,
    deleteReport,
    cancelTrackedJob,
    reset,
  }
})
