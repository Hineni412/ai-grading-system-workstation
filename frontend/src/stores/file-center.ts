import { ref } from 'vue'
import { defineStore } from 'pinia'

import {
  exportsApi,
  type DownloadedJobFile,
  type JobSummaryList,
  type ReportContext,
  type ReportType,
  type ScoreExcelOptions,
  type TrainingExportRequest,
  type TrainingTaskDetail,
  type TrainingTaskList,
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
  listTrainingTasks(
    page?: number,
    pageSize?: number,
    signal?: AbortSignal,
  ): Promise<TrainingTaskList>
  getTrainingTask(
    taskId: number,
    signal?: AbortSignal,
  ): Promise<TrainingTaskDetail>
  submitTrainingExport(
    taskId: number,
    body: TrainingExportRequest,
    signal?: AbortSignal,
  ): Promise<JobResponse>
  retryTrainingExport(jobId: number, signal?: AbortSignal): Promise<JobResponse>
  listTrainingExportJobs(
    page?: number,
    pageSize?: number,
    signal?: AbortSignal,
  ): Promise<JobSummaryList>
  getJob(id: number, signal?: AbortSignal): Promise<JobResponse>
  downloadJobFile(id: number): Promise<DownloadedJobFile>
}

function safeErrorMessage(): string {
  return '文件登记簿暂时无法更新，请稍后重试。'
}

export const useFileCenterStore = defineStore('file-center', () => {
  const sessionId = ref<number | null>(null)
  const reportContext = ref<ReportContext | null>(null)
  const trainingTasks = ref<TrainingTaskList['items']>([])
  const trainingJobs = ref<JobResponse[]>([])
  const selectedTrainingTask = ref<TrainingTaskDetail | null>(null)
  const trainingDetailState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
  const state = ref<FileCenterState>('idle')
  const errorMessage = ref('')
  const updatedAt = ref<string | null>(null)
  const submittingKey = ref<string | null>(null)

  let generation = 0
  let controller: AbortController | null = null
  let detailController: AbortController | null = null

  async function load(
    nextSessionId: number,
    api: FileCenterApi = exportsApi,
  ): Promise<void> {
    if (!Number.isSafeInteger(nextSessionId) || nextSessionId <= 0) {
      throw new Error('Invalid session id')
    }
    controller?.abort()
    detailController?.abort()
    detailController = null
    const nextController = new AbortController()
    controller = nextController
    const requestGeneration = ++generation
    const isSameSession = sessionId.value === nextSessionId
    if (!isSameSession) {
      sessionId.value = nextSessionId
      reportContext.value = null
      trainingTasks.value = []
      trainingJobs.value = []
      selectedTrainingTask.value = null
      trainingDetailState.value = 'idle'
      updatedAt.value = null
    }
    state.value = 'loading'
    errorMessage.value = ''

    try {
      const [nextReports, nextTasks, nextJobPage] = await Promise.all([
        api.getReportContext(nextSessionId, 1, 100, nextController.signal),
        api.listTrainingTasks(1, 100, nextController.signal),
        api.listTrainingExportJobs(1, 100, nextController.signal),
      ])
      const nextJobs = await Promise.all(
        nextJobPage.items.map((job) => api.getJob(job.id, nextController.signal)),
      )
      if (
        requestGeneration !== generation
        || nextController.signal.aborted
        || sessionId.value !== nextSessionId
      ) return
      reportContext.value = nextReports
      trainingTasks.value = nextTasks.items
      trainingJobs.value = nextJobs
      const jobStore = useJobStore()
      for (const job of [...nextReports.jobs, ...nextJobs]) {
        if (!TERMINAL_JOB_STATUSES.has(job.status)) jobStore.track(job)
      }
      const hasAnything = nextReports.jobs.length > 0
        || nextTasks.items.length > 0
        || nextJobs.length > 0
      state.value = hasAnything ? 'ready' : 'empty'
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

  async function submitTrainingBundle(
    taskId: number,
    api: FileCenterApi = exportsApi,
  ): Promise<JobResponse> {
    const key = `training:${taskId}`
    if (submittingKey.value === key) throw new Error('Training export is already in progress')
    submittingKey.value = key
    try {
      const job = await api.submitTrainingExport(taskId, { format: 'docx' })
      useJobStore().track(job)
      return job
    } finally {
      if (submittingKey.value === key) submittingKey.value = null
    }
  }

  async function loadTrainingTask(
    taskId: number,
    api: FileCenterApi = exportsApi,
  ): Promise<TrainingTaskDetail | null> {
    const requestGeneration = generation
    const requestSessionId = sessionId.value
    detailController?.abort()
    const nextController = new AbortController()
    detailController = nextController
    trainingDetailState.value = 'loading'
    try {
      const detail = await api.getTrainingTask(taskId, nextController.signal)
      if (
        nextController.signal.aborted
        || generation !== requestGeneration
        || sessionId.value !== requestSessionId
      ) return null
      selectedTrainingTask.value = detail
      trainingDetailState.value = 'ready'
      return detail
    } catch (error) {
      if (
        nextController.signal.aborted
        || generation !== requestGeneration
        || sessionId.value !== requestSessionId
      ) return null
      trainingDetailState.value = 'error'
      throw error
    } finally {
      if (detailController === nextController) detailController = null
    }
  }

  async function submitTraining(
    taskId: number,
    body: TrainingExportRequest,
    api: FileCenterApi = exportsApi,
  ): Promise<JobResponse> {
    const key = `training:${taskId}`
    if (submittingKey.value === key) throw new Error('Training export is already in progress')
    submittingKey.value = key
    try {
      const job = await api.submitTrainingExport(taskId, body)
      useJobStore().track(job)
      return job
    } finally {
      if (submittingKey.value === key) submittingKey.value = null
    }
  }

  async function retryTrainingExport(
    jobId: number,
    api: FileCenterApi = exportsApi,
  ): Promise<JobResponse> {
    const key = `retry:${jobId}`
    if (submittingKey.value === key) throw new Error('Retry is already in progress')
    submittingKey.value = key
    try {
      const job = await api.retryTrainingExport(jobId)
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

  async function cancelTrackedJob(
    jobId: number,
    canceller: (id: number) => Promise<void> = (id) => useJobStore().cancel(id),
  ): Promise<void> {
    await canceller(jobId)
  }

  function reset(): void {
    controller?.abort()
    controller = null
    detailController?.abort()
    detailController = null
    generation += 1
    sessionId.value = null
    reportContext.value = null
    trainingTasks.value = []
    trainingJobs.value = []
    selectedTrainingTask.value = null
    trainingDetailState.value = 'idle'
    state.value = 'idle'
    errorMessage.value = ''
    updatedAt.value = null
    submittingKey.value = null
  }

  return {
    sessionId,
    reportContext,
    trainingTasks,
    trainingJobs,
    selectedTrainingTask,
    trainingDetailState,
    state,
    errorMessage,
    updatedAt,
    submittingKey,
    load,
    submitReport,
    submitTrainingBundle,
    loadTrainingTask,
    submitTraining,
    retryTrainingExport,
    download,
    cancelTrackedJob,
    reset,
  }
})
