import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { ApiError } from '../api/errors'
import {
  exportsApi,
  type DownloadedJobFile,
  type TrainingExportRequest,
  type TrainingTaskDetail,
  type TrainingTaskList,
} from '../api/exports'
import type { JobResponse } from '../api/jobs'
import {
  trainingApi,
  type TrainingDiagnosis,
  type TrainingDiagnosisRequest,
  type TrainingExamScopeRequest,
  type TrainingPlanRequest,
  type TrainingPlanResponse,
  type TrainingStageRatios,
  type TrainingStudentScopeRequest,
  type TrainingTaskConfirmRequest,
  type TrainingVariantMode,
} from '../api/training'
import { useJobStore } from './jobs'

export type TrainingRequestState =
  | 'idle'
  | 'loading'
  | 'ready'
  | 'empty'
  | 'error'

export type TrainingConfirmState =
  | 'idle'
  | 'submitting'
  | 'confirmed'
  | 'error'
  | 'conflict'

export interface TrainingStudentScopeInput {
  mode: TrainingStudentScopeRequest['mode']
  studentIds: string[]
  classId: string
}

export interface TrainingExamScopeInput {
  mode: TrainingExamScopeRequest['mode']
  sessionIds: number[]
}

export interface TrainingPlanConfig {
  variantMode: TrainingVariantMode
  questionCount: number
  stageRatios: TrainingStageRatios
  excludeCurrentExamOriginals: boolean
}

export interface TrainingExportChoice extends TrainingExportRequest {
  taskId: number
}

export interface TrainingWorkflowApi {
  diagnose(
    body: TrainingDiagnosisRequest,
    signal?: AbortSignal,
  ): Promise<TrainingDiagnosis>
  preview(
    body: TrainingPlanRequest,
    signal?: AbortSignal,
  ): Promise<TrainingPlanResponse>
  confirm(
    body: TrainingTaskConfirmRequest,
    signal?: AbortSignal,
  ): Promise<TrainingTaskDetail>
}

export interface TrainingHistoryApi {
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
  retryTrainingExport(
    jobId: number,
    signal?: AbortSignal,
  ): Promise<JobResponse>
  downloadJobFile(jobId: number): Promise<DownloadedJobFile>
}

function uniqueText(values: string[]): string[] {
  return [...new Set(values.map((value) => value.trim()).filter(Boolean))].sort()
}

function uniquePositiveIntegers(values: number[]): number[] {
  return [...new Set(values.filter(
    (value) => Number.isSafeInteger(value) && value > 0,
  ))].sort((left, right) => left - right)
}

function safeMessage(action: 'analysis' | 'preview' | 'confirm' | 'history'): string {
  const labels = {
    analysis: '薄弱点诊断暂时无法完成，请保留当前范围后重试。',
    preview: '训练计划暂时无法生成，请保留当前选择后重试。',
    confirm: '训练任务保存结果未能确认，请按提示核对后重试。',
    history: '训练任务记录暂时无法更新，请稍后重试。',
  }
  return labels[action]
}

function hasRecommendation(plan: TrainingPlanResponse): boolean {
  return plan.plan.variants.some((variant) => variant.items.length > 0)
}

export const useTrainingStore = defineStore('training', () => {
  const studentScope = ref<TrainingStudentScopeInput>({
    mode: 'student',
    studentIds: [],
    classId: '',
  })
  const examScope = ref<TrainingExamScopeInput>({
    mode: 'current',
    sessionIds: [],
  })
  const diagnosis = ref<TrainingDiagnosis | null>(null)
  const plan = ref<TrainingPlanResponse | null>(null)
  const confirmationId = ref('')
  const confirmedTask = ref<TrainingTaskDetail | null>(null)
  const tasks = ref<TrainingTaskList['items']>([])
  const selectedTask = ref<TrainingTaskDetail | null>(null)
  const analysisState = ref<TrainingRequestState>('idle')
  const planState = ref<TrainingRequestState>('idle')
  const confirmState = ref<TrainingConfirmState>('idle')
  const historyState = ref<TrainingRequestState>('idle')
  const errorMessage = ref('')
  const actionMessage = ref('')
  const submittingExportKey = ref('')

  let generation = 0
  let diagnosisController: AbortController | null = null
  let planController: AbortController | null = null
  let confirmController: AbortController | null = null
  let historyController: AbortController | null = null
  let detailController: AbortController | null = null
  let diagnosisScopeKey = ''
  let planScopeKey = ''
  let planGeneration = 0
  let lastPlanRequest: TrainingPlanRequest | null = null
  let confirmationCacheKey = ''
  const confirmationIds = new Map<string, string>()

  const hasCurrentDiagnosis = computed(
    () => diagnosis.value !== null && diagnosisScopeKey === scopeKey(),
  )
  const hasCurrentPlan = computed(
    () => plan.value !== null && planScopeKey === scopeKey(),
  )
  const knowledgePoints = computed(() => uniqueText(
    diagnosis.value?.students.flatMap(
      (student) => student.weak_points.map(({ knowledge_point }) => knowledge_point),
    ) ?? [],
  ))

  function normalizedStudentScope(): TrainingStudentScopeRequest {
    const studentIds = uniqueText(studentScope.value.studentIds)
    const result: TrainingStudentScopeRequest = {
      mode: studentScope.value.mode,
      student_ids: studentIds,
    }
    const classId = studentScope.value.classId.trim()
    if (studentScope.value.mode === 'class' && classId) result.class_id = classId
    return result
  }

  function normalizedExamScope(): TrainingExamScopeRequest {
    return {
      mode: examScope.value.mode,
      session_ids: uniquePositiveIntegers(examScope.value.sessionIds),
    }
  }

  function scopeKey(): string {
    return JSON.stringify({
      scope: normalizedStudentScope(),
      exam_scope: normalizedExamScope(),
    })
  }

  function requestBody(): TrainingDiagnosisRequest {
    const scope = normalizedStudentScope()
    const nextExamScope = normalizedExamScope()
    if (scope.student_ids.length === 0) throw new Error('请至少选择一名学生')
    if (scope.mode === 'class' && !scope.class_id) throw new Error('请选择班级')
    if (nextExamScope.session_ids.length === 0) throw new Error('请至少选择一场考试')
    return {
      scope,
      exam_scope: nextExamScope,
    }
  }

  function invalidateScopeResults(): void {
    generation += 1
    planGeneration += 1
    diagnosisController?.abort()
    planController?.abort()
    confirmController?.abort()
    diagnosisController = null
    planController = null
    confirmController = null
    diagnosis.value = null
    plan.value = null
    confirmationId.value = ''
    confirmedTask.value = null
    diagnosisScopeKey = ''
    planScopeKey = ''
    lastPlanRequest = null
    confirmationCacheKey = ''
    analysisState.value = 'idle'
    planState.value = 'idle'
    confirmState.value = 'idle'
    errorMessage.value = ''
    actionMessage.value = ''
  }

  function invalidatePlan(): void {
    planGeneration += 1
    planController?.abort()
    confirmController?.abort()
    planController = null
    confirmController = null
    plan.value = null
    confirmationId.value = ''
    confirmedTask.value = null
    planScopeKey = ''
    lastPlanRequest = null
    confirmationCacheKey = ''
    planState.value = 'idle'
    confirmState.value = 'idle'
    errorMessage.value = ''
    actionMessage.value = ''
  }

  function setStudentScope(next: TrainingStudentScopeInput): void {
    const normalized: TrainingStudentScopeInput = {
      mode: next.mode,
      studentIds: uniqueText(next.studentIds),
      classId: next.classId.trim(),
    }
    if (JSON.stringify(normalized) === JSON.stringify(studentScope.value)) return
    studentScope.value = normalized
    invalidateScopeResults()
  }

  function setExamScope(next: TrainingExamScopeInput): void {
    const normalized: TrainingExamScopeInput = {
      mode: next.mode,
      sessionIds: uniquePositiveIntegers(next.sessionIds),
    }
    if (JSON.stringify(normalized) === JSON.stringify(examScope.value)) return
    examScope.value = normalized
    invalidateScopeResults()
  }

  async function analyze(
    api: TrainingWorkflowApi = trainingApi,
  ): Promise<TrainingDiagnosis | null> {
    const body = requestBody()
    diagnosisController?.abort()
    planController?.abort()
    confirmController?.abort()
    const controller = new AbortController()
    diagnosisController = controller
    const requestGeneration = generation
    const requestScopeKey = scopeKey()
    diagnosis.value = null
    plan.value = null
    confirmationId.value = ''
    confirmedTask.value = null
    diagnosisScopeKey = ''
    planScopeKey = ''
    lastPlanRequest = null
    analysisState.value = 'loading'
    planState.value = 'idle'
    confirmState.value = 'idle'
    errorMessage.value = ''
    actionMessage.value = ''
    try {
      const next = await api.diagnose(body, controller.signal)
      if (
        controller.signal.aborted
        || requestGeneration !== generation
        || requestScopeKey !== scopeKey()
      ) return null
      diagnosis.value = next
      diagnosisScopeKey = requestScopeKey
      analysisState.value = next.students.some(
        (student) => student.weak_points.length > 0,
      ) ? 'ready' : 'empty'
      return next
    } catch (error) {
      if (
        controller.signal.aborted
        || requestGeneration !== generation
        || requestScopeKey !== scopeKey()
      ) return null
      analysisState.value = 'error'
      errorMessage.value = safeMessage('analysis')
      throw error
    } finally {
      if (diagnosisController === controller) diagnosisController = null
    }
  }

  async function previewPlan(
    config: TrainingPlanConfig,
    api: TrainingWorkflowApi = trainingApi,
  ): Promise<TrainingPlanResponse | null> {
    if (!hasCurrentDiagnosis.value) throw new Error('请先分析当前选择范围')
    const base = requestBody()
    const body: TrainingPlanRequest = {
      ...base,
      variant_mode: config.variantMode,
      question_count: config.questionCount,
      stage_ratios: { ...config.stageRatios },
      exclude_current_exam_originals: config.excludeCurrentExamOriginals,
    }
    planController?.abort()
    confirmController?.abort()
    const controller = new AbortController()
    planController = controller
    const requestGeneration = generation
    const requestPlanGeneration = planGeneration
    const requestScopeKey = scopeKey()
    plan.value = null
    confirmationId.value = ''
    confirmedTask.value = null
    planScopeKey = ''
    lastPlanRequest = null
    planState.value = 'loading'
    confirmState.value = 'idle'
    errorMessage.value = ''
    actionMessage.value = ''
    try {
      const next = await api.preview(body, controller.signal)
      if (
        controller.signal.aborted
        || requestGeneration !== generation
        || requestPlanGeneration !== planGeneration
        || requestScopeKey !== scopeKey()
      ) return null
      plan.value = next
      planScopeKey = requestScopeKey
      lastPlanRequest = body
      confirmationCacheKey = JSON.stringify({
        request: body,
        plan_revision: next.plan_revision,
      })
      const cachedConfirmationId = confirmationIds.get(confirmationCacheKey)
      confirmationId.value = cachedConfirmationId ?? crypto.randomUUID()
      confirmationIds.set(confirmationCacheKey, confirmationId.value)
      planState.value = hasRecommendation(next) ? 'ready' : 'empty'
      return next
    } catch (error) {
      if (
        controller.signal.aborted
        || requestGeneration !== generation
        || requestPlanGeneration !== planGeneration
        || requestScopeKey !== scopeKey()
      ) return null
      planState.value = 'error'
      errorMessage.value = safeMessage('preview')
      throw error
    } finally {
      if (planController === controller) planController = null
    }
  }

  async function confirmPlan(
    api: TrainingWorkflowApi = trainingApi,
  ): Promise<TrainingTaskDetail> {
    if (confirmState.value === 'conflict') throw new Error('请重新生成训练计划')
    if (!hasCurrentPlan.value || !plan.value || !lastPlanRequest) {
      throw new Error('请先生成当前训练计划')
    }
    if (!hasRecommendation(plan.value)) throw new Error('当前计划没有可确认的题目')
    if (!confirmationId.value) confirmationId.value = crypto.randomUUID()
    if (confirmState.value === 'submitting') throw new Error('训练任务正在保存')
    const body: TrainingTaskConfirmRequest = {
      ...lastPlanRequest,
      confirmation_id: confirmationId.value,
      expected_plan_revision: plan.value.plan_revision,
    }
    confirmController?.abort()
    const controller = new AbortController()
    confirmController = controller
    const requestGeneration = generation
    const requestPlanGeneration = planGeneration
    const requestScopeKey = scopeKey()
    confirmState.value = 'submitting'
    errorMessage.value = ''
    actionMessage.value = ''
    try {
      const next = await api.confirm(body, controller.signal)
      if (
        controller.signal.aborted
        || requestGeneration !== generation
        || requestPlanGeneration !== planGeneration
        || requestScopeKey !== scopeKey()
      ) throw new Error('训练任务现场已经变化')
      confirmedTask.value = next
      selectedTask.value = next
      tasks.value = [
        next,
        ...tasks.value.filter(({ id }) => id !== next.id),
      ]
      confirmState.value = 'confirmed'
      actionMessage.value = `训练任务已保存：${next.task_code}`
      return next
    } catch (error) {
      if (
        controller.signal.aborted
        || requestGeneration !== generation
        || requestPlanGeneration !== planGeneration
        || requestScopeKey !== scopeKey()
      ) {
        throw error
      }
      if (error instanceof ApiError && error.kind === 'conflict') {
        confirmState.value = 'conflict'
        errorMessage.value = '训练候选题已经变化，请重新分析并生成计划后再确认。'
      } else {
        confirmState.value = 'error'
        errorMessage.value = safeMessage('confirm')
      }
      throw error
    } finally {
      if (confirmController === controller) confirmController = null
    }
  }

  async function loadTasks(
    api: TrainingHistoryApi = exportsApi,
  ): Promise<void> {
    historyController?.abort()
    const controller = new AbortController()
    historyController = controller
    historyState.value = 'loading'
    errorMessage.value = ''
    try {
      const page = await api.listTrainingTasks(1, 20, controller.signal)
      if (controller.signal.aborted) return
      tasks.value = page.items
      historyState.value = page.items.length > 0 ? 'ready' : 'empty'
    } catch (error) {
      if (controller.signal.aborted) return
      historyState.value = 'error'
      errorMessage.value = safeMessage('history')
      throw error
    } finally {
      if (historyController === controller) historyController = null
    }
  }

  async function selectTask(
    taskId: number,
    api: TrainingHistoryApi = exportsApi,
  ): Promise<TrainingTaskDetail | null> {
    if (!Number.isSafeInteger(taskId) || taskId <= 0) {
      throw new Error('Invalid training task id')
    }
    detailController?.abort()
    const controller = new AbortController()
    detailController = controller
    try {
      const detail = await api.getTrainingTask(taskId, controller.signal)
      if (controller.signal.aborted) return null
      selectedTask.value = detail
      return detail
    } finally {
      if (detailController === controller) detailController = null
    }
  }

  async function submitExport(
    choice: TrainingExportChoice,
    api: TrainingHistoryApi = exportsApi,
  ): Promise<JobResponse> {
    const { taskId, ...body } = choice
    const key = `export:${taskId}`
    if (submittingExportKey.value === key) {
      throw new Error('训练材料正在生成')
    }
    submittingExportKey.value = key
    try {
      const job = await api.submitTrainingExport(taskId, body)
      useJobStore().track(job)
      actionMessage.value = '训练材料已加入生成队列。'
      return job
    } finally {
      if (submittingExportKey.value === key) submittingExportKey.value = ''
    }
  }

  async function retryExport(
    jobId: number,
    api: TrainingHistoryApi = exportsApi,
  ): Promise<JobResponse> {
    const key = `retry:${jobId}`
    if (submittingExportKey.value === key) {
      throw new Error('训练材料正在重新生成')
    }
    submittingExportKey.value = key
    try {
      const job = await api.retryTrainingExport(jobId)
      useJobStore().track(job)
      actionMessage.value = '训练材料已重新加入生成队列。'
      return job
    } finally {
      if (submittingExportKey.value === key) submittingExportKey.value = ''
    }
  }

  async function download(
    jobId: number,
    api: TrainingHistoryApi = exportsApi,
  ): Promise<DownloadedJobFile> {
    try {
      return await api.downloadJobFile(jobId)
    } catch (error) {
      errorMessage.value = '训练材料文件已过期或暂时无法下载，请重新生成训练材料。'
      throw error
    }
  }

  function reset(): void {
    invalidateScopeResults()
    historyController?.abort()
    detailController?.abort()
    historyController = null
    detailController = null
    studentScope.value = { mode: 'student', studentIds: [], classId: '' }
    examScope.value = { mode: 'current', sessionIds: [] }
    tasks.value = []
    selectedTask.value = null
    historyState.value = 'idle'
    submittingExportKey.value = ''
    confirmationIds.clear()
  }

  return {
    studentScope,
    examScope,
    diagnosis,
    plan,
    confirmationId,
    confirmedTask,
    tasks,
    selectedTask,
    analysisState,
    planState,
    confirmState,
    historyState,
    errorMessage,
    actionMessage,
    submittingExportKey,
    hasCurrentDiagnosis,
    hasCurrentPlan,
    knowledgePoints,
    setStudentScope,
    setExamScope,
    invalidatePlan,
    analyze,
    previewPlan,
    confirmPlan,
    loadTasks,
    selectTask,
    submitExport,
    retryExport,
    download,
    reset,
  }
})
