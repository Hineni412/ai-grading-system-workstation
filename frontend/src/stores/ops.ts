import { computed, onScopeDispose, ref } from 'vue'
import { defineStore } from 'pinia'

import {
  ApiError,
  isAmbiguousWriteError,
} from '../api/errors'
import {
  opsApi,
  type OpsApi,
  type OpsBackupItem,
  type OpsImportUpload,
  type OpsOperationState,
  type OpsPreflight,
  type OpsPreflightRequest,
  type OpsSelfCheck,
} from '../api/ops'
import { TERMINAL_JOB_STATUSES, type JobResponse } from '../api/jobs'
import { useJobStore } from './jobs'

export interface OpsUiError {
  message: string
  impact: string
  retryable: boolean
  requestId: string
}

const OFFLINE_JOB_TYPES = new Set([
  'ops_restore_prepare',
  'ops_migration_prepare',
  'ops_transfer_import_prepare',
])

const DIRECTORY_LABELS: Record<string, string> = {
  data: '数据目录',
  databases: '数据库目录',
  backups: '备份目录',
  logs: '日志目录',
  reports: '报告目录',
  outputs: '输出目录',
}

const DATABASE_LABELS: Record<string, string> = {
  grading: '阅卷数据库',
  question_bank: '题库数据库',
}

const TOOL_LABELS: Record<string, string> = {
  microsoft_word: 'Microsoft Word',
  libreoffice: 'LibreOffice',
  pdflatex: 'PDFLaTeX',
}

function defaultUiError(impact: string): OpsUiError {
  return {
    message: '操作暂时无法完成',
    impact,
    retryable: true,
    requestId: '',
  }
}

function safeReadError(error: unknown, area: 'self-check' | 'backups'): OpsUiError {
  if (!(error instanceof ApiError)) {
    return defaultUiError(area === 'self-check'
      ? '保留上一次系统状态'
      : '保留上一次备份清单')
  }
  return {
    message: area === 'self-check'
      ? '系统状态暂时无法更新'
      : '备份清单暂时无法更新',
    impact: area === 'self-check'
      ? '保留上一次系统状态'
      : '保留上一次备份清单',
    retryable: error.retryable,
    requestId: error.requestId,
  }
}

function safeActionError(error: unknown, defaultImpact: string): OpsUiError {
  if (!(error instanceof ApiError)) return defaultUiError(defaultImpact)
  const messages: Record<string, { message: string; impact: string }> = {
    ops_confirmation_expired: {
      message: '预检已过期，请重新预检',
      impact: '没有启动新的运维任务',
    },
    ops_confirmation_used: {
      message: '这次确认已经使用，请先刷新状态',
      impact: '不会自动重复提交',
    },
    ops_preflight_stale: {
      message: '数据状态已经变化，请重新预检',
      impact: '没有按旧预检继续操作',
    },
    ops_operation_busy: {
      message: '已有运维操作正在进行',
      impact: '当前操作没有排队或重复启动',
    },
    ops_resource_not_found: {
      message: '所选资源已不存在，请刷新后重选',
      impact: '没有启动新的运维任务',
    },
    ops_request_invalid: {
      message: '当前运维请求未通过安全检查',
      impact: '没有启动新的运维任务',
    },
  }
  const mapped = messages[error.code]
  return {
    message: mapped?.message ?? '运维操作暂时无法完成',
    impact: mapped?.impact ?? defaultImpact,
    retryable: error.retryable,
    requestId: error.requestId,
  }
}

function operationIdFromJob(job: JobResponse | undefined): string {
  if (!job) return ''
  for (const source of [job.result, job.payload]) {
    const value = source.operation_id
    if (typeof value === 'string' && value.trim()) return value.trim()
  }
  return ''
}

function newestOpsJob(jobs: Record<number, JobResponse>): JobResponse | null {
  return Object.values(jobs)
    .filter((job) => job.job_type.startsWith('ops_'))
    .sort((left, right) => {
      const time = Date.parse(right.updated_at) - Date.parse(left.updated_at)
      return Number.isFinite(time) && time !== 0 ? time : right.id - left.id
    })[0] ?? null
}

function checkText(status: string): string {
  if (status === 'ok') return '正常'
  if (status === 'warning') return '需注意'
  return '不可用'
}

export const useOpsStore = defineStore('ops', () => {
  const jobStore = useJobStore()
  const selfCheck = ref<OpsSelfCheck | null>(null)
  const backups = ref<OpsBackupItem[]>([])
  const selfCheckLoading = ref(false)
  const backupsLoading = ref(false)
  const selfCheckError = ref<OpsUiError | null>(null)
  const backupsError = ref<OpsUiError | null>(null)

  const importUpload = ref<OpsImportUpload | null>(null)
  const uploadLoading = ref(false)
  const preflight = ref<OpsPreflight | null>(null)
  const preflightRequest = ref<OpsPreflightRequest | null>(null)
  const preflightLoading = ref(false)
  const submitting = ref(false)
  const resultUnknown = ref(false)
  const actionError = ref<OpsUiError | null>(null)
  const activeJobId = ref<number | null>(null)
  const operationState = ref<OpsOperationState | null>(null)
  const operationLoading = ref(false)

  let selfCheckGeneration = 0
  let backupGeneration = 0
  let preflightGeneration = 0
  let uploadGeneration = 0
  let operationGeneration = 0
  let selfCheckController: AbortController | null = null
  let backupController: AbortController | null = null
  let preflightController: AbortController | null = null
  let uploadController: AbortController | null = null
  let submitController: AbortController | null = null
  let operationController: AbortController | null = null
  let submitPromise: Promise<void> | null = null

  const activeJob = computed(() => {
    const id = activeJobId.value
    return id === null ? null : jobStore.jobs[id] ?? null
  })

  const operationApplied = computed(() => operationState.value?.status === 'applied')

  const hasBlockingOperation = computed(() => {
    if (
      uploadLoading.value
      || preflightLoading.value
      || submitting.value
      || operationLoading.value
    ) return true
    const job = activeJob.value
    if (job && !TERMINAL_JOB_STATUSES.has(job.status)) return true
    return operationState.value !== null
      && ['prepared', 'restart_required', 'applying'].includes(operationState.value.status)
  })

  const diagnosticText = computed(() => {
    const snapshot = selfCheck.value
    if (!snapshot) return ''
    const lines = [
      'AI 阅卷系统脱敏诊断',
      `系统版本：${snapshot.version}`,
      `整体状态：${checkText(snapshot.status)}`,
      `API 配置：${snapshot.api_configured ? '已配置' : '未配置'}`,
      '',
      '数据目录：',
      ...snapshot.directories.map((item) =>
        `- ${DIRECTORY_LABELS[item.key] ?? item.key}：${checkText(item.status)}；`
        + `存在 ${item.exists ? '是' : '否'}；可写 ${item.writable ? '是' : '否'}`,
      ),
      '',
      '数据库：',
      ...snapshot.databases.map((item) =>
        `- ${DATABASE_LABELS[item.key] ?? item.key}：完整性 ${item.integrity}；`
        + `版本 ${item.migration_version}；待迁移 ${item.pending_migrations}`,
      ),
      '',
      '外部工具：',
      ...snapshot.tools.map((item) =>
        `- ${TOOL_LABELS[item.key] ?? item.key}：${item.available ? '可用' : '不可用'}`,
      ),
      '',
      `警告代码：${snapshot.warnings.length ? snapshot.warnings.join('，') : '无'}`,
      '说明：以上内容来自公开脱敏状态，不包含本机路径、API Key 或业务正文。',
    ]
    return lines.join('\n')
  })

  async function refreshSelfCheck(api: OpsApi = opsApi): Promise<void> {
    const generation = ++selfCheckGeneration
    selfCheckController?.abort()
    const controller = new AbortController()
    selfCheckController = controller
    selfCheckLoading.value = true
    try {
      const next = await api.getSelfCheck(controller.signal)
      if (generation !== selfCheckGeneration) return
      selfCheck.value = next
      selfCheckError.value = null
    } catch (error) {
      if (generation !== selfCheckGeneration) return
      selfCheckError.value = safeReadError(error, 'self-check')
    } finally {
      if (generation === selfCheckGeneration) selfCheckLoading.value = false
      if (selfCheckController === controller) selfCheckController = null
    }
  }

  async function refreshBackups(api: OpsApi = opsApi): Promise<void> {
    const generation = ++backupGeneration
    backupController?.abort()
    const controller = new AbortController()
    backupController = controller
    backupsLoading.value = true
    try {
      const next = await api.getBackups(50, controller.signal)
      if (generation !== backupGeneration) return
      backups.value = next.items
      backupsError.value = null
    } catch (error) {
      if (generation !== backupGeneration) return
      backupsError.value = safeReadError(error, 'backups')
    } finally {
      if (generation === backupGeneration) backupsLoading.value = false
      if (backupController === controller) backupController = null
    }
  }

  async function initialize(api: OpsApi = opsApi): Promise<void> {
    await Promise.all([
      refreshSelfCheck(api),
      refreshBackups(api),
    ])
    await recoverTrackedOperation(api)
  }

  function clearPreparedFlow(): void {
    preflightGeneration += 1
    preflightController?.abort()
    preflightController = null
    submitController?.abort()
    submitController = null
    preflight.value = null
    preflightRequest.value = null
    preflightLoading.value = false
    submitting.value = false
    submitPromise = null
  }

  function clearFinishedDisplay(): void {
    const job = activeJob.value
    if (job && TERMINAL_JOB_STATUSES.has(job.status)) activeJobId.value = null
    const state = operationState.value
    if (state && !['prepared', 'restart_required', 'applying'].includes(state.status)) {
      operationState.value = null
    }
  }

  async function startPreflight(
    request: OpsPreflightRequest,
    api: OpsApi = opsApi,
  ): Promise<void> {
    if (hasBlockingOperation.value) {
      actionError.value = {
        message: '已有运维操作正在进行',
        impact: '请先等待当前操作完成或在允许阶段撤销',
        retryable: false,
        requestId: '',
      }
      return
    }
    clearFinishedDisplay()
    const generation = ++preflightGeneration
    preflightController?.abort()
    const controller = new AbortController()
    preflightController = controller
    preflightLoading.value = true
    preflight.value = null
    preflightRequest.value = request
    actionError.value = null
    resultUnknown.value = false
    try {
      const next = await api.preflight(request, controller.signal)
      if (generation !== preflightGeneration) return
      preflight.value = next
    } catch (error) {
      if (generation !== preflightGeneration) return
      preflightRequest.value = null
      actionError.value = safeActionError(error, '没有启动新的运维任务')
    } finally {
      if (generation === preflightGeneration) preflightLoading.value = false
      if (preflightController === controller) preflightController = null
    }
  }

  async function submitConfirmed(api: OpsApi = opsApi): Promise<void> {
    if (submitPromise) return submitPromise
    const token = preflight.value?.confirmation_token
    if (!token) return
    const controller = new AbortController()
    submitController = controller
    submitting.value = true
    actionError.value = null
    resultUnknown.value = false
    const request = (async () => {
      try {
        const job = await api.submit(token, controller.signal)
        jobStore.track(job)
        activeJobId.value = job.id
        operationState.value = null
        actionError.value = null
      } catch (error) {
        if (isAmbiguousWriteError(error)) {
          resultUnknown.value = true
          actionError.value = {
            message: '提交结果未知，请先刷新状态，不要重复操作',
            impact: '系统不会自动再次提交该危险操作',
            retryable: false,
            requestId: error.requestId,
          }
        } else {
          actionError.value = safeActionError(error, '没有确认新的运维结果')
        }
      } finally {
        preflight.value = null
        preflightRequest.value = null
        submitting.value = false
        if (submitController === controller) submitController = null
        submitPromise = null
      }
    })()
    submitPromise = request
    return request
  }

  async function stageImport(file: File, api: OpsApi = opsApi): Promise<void> {
    if (hasBlockingOperation.value) {
      actionError.value = {
        message: '已有运维操作正在进行',
        impact: '当前文件没有上传',
        retryable: false,
        requestId: '',
      }
      return
    }
    clearFinishedDisplay()
    const generation = ++uploadGeneration
    uploadController?.abort()
    const controller = new AbortController()
    uploadController = controller
    uploadLoading.value = true
    importUpload.value = null
    clearPreparedFlow()
    actionError.value = null
    resultUnknown.value = false
    try {
      const next = await api.stageImport(file, controller.signal)
      if (generation !== uploadGeneration) return
      importUpload.value = next
    } catch (error) {
      if (generation !== uploadGeneration) return
      actionError.value = safeActionError(error, '文件没有进入待导入状态')
    } finally {
      if (generation === uploadGeneration) uploadLoading.value = false
      if (uploadController === controller) uploadController = null
    }
  }

  async function loadOperation(
    operationId: string,
    api: OpsApi = opsApi,
  ): Promise<void> {
    const generation = ++operationGeneration
    operationController?.abort()
    const controller = new AbortController()
    operationController = controller
    operationLoading.value = true
    try {
      const next = await api.getOperation(operationId, controller.signal)
      if (generation !== operationGeneration) return
      operationState.value = next
      actionError.value = null
    } catch (error) {
      if (generation !== operationGeneration) return
      actionError.value = safeActionError(error, '保留当前任务状态，尚未确认最终应用结果')
    } finally {
      if (generation === operationGeneration) operationLoading.value = false
      if (operationController === controller) operationController = null
    }
  }

  async function refreshOperation(api: OpsApi = opsApi): Promise<void> {
    const operationId = operationState.value?.operation_id
      || operationIdFromJob(activeJob.value ?? undefined)
    if (!operationId) return
    await loadOperation(operationId, api)
  }

  async function recoverTrackedOperation(api: OpsApi = opsApi): Promise<void> {
    const job = newestOpsJob(jobStore.jobs)
    if (!job) return
    activeJobId.value = job.id
    if (
      job.status === 'succeeded'
      && OFFLINE_JOB_TYPES.has(job.job_type)
    ) {
      const operationId = operationIdFromJob(job)
      if (operationId) await loadOperation(operationId, api)
    }
  }

  async function cancelCurrent(api: OpsApi = opsApi): Promise<void> {
    const state = operationState.value
    if (state && ['prepared', 'restart_required'].includes(state.status)) {
      operationLoading.value = true
      const controller = new AbortController()
      operationController?.abort()
      operationController = controller
      try {
        operationState.value = await api.cancelOperation(
          state.operation_id,
          controller.signal,
        )
        actionError.value = null
      } catch (error) {
        actionError.value = safeActionError(error, '待重启操作没有被撤销')
      } finally {
        operationLoading.value = false
        if (operationController === controller) operationController = null
      }
      return
    }
    const job = activeJob.value
    if (job && !TERMINAL_JOB_STATUSES.has(job.status)) {
      await jobStore.cancel(job.id)
    }
  }

  async function downloadActive(api: OpsApi = opsApi) {
    const job = activeJob.value
    if (!job || job.status !== 'succeeded') throw new Error('Ops download is not ready')
    return api.downloadJob(job.id)
  }

  function resetFlow(): void {
    if (hasBlockingOperation.value) return
    clearPreparedFlow()
    importUpload.value = null
    activeJobId.value = null
    operationState.value = null
    actionError.value = null
    resultUnknown.value = false
  }

  function dispose(): void {
    selfCheckController?.abort()
    backupController?.abort()
    preflightController?.abort()
    uploadController?.abort()
    submitController?.abort()
    operationController?.abort()
  }

  onScopeDispose(dispose)

  return {
    selfCheck,
    backups,
    selfCheckLoading,
    backupsLoading,
    selfCheckError,
    backupsError,
    importUpload,
    uploadLoading,
    preflight,
    preflightRequest,
    preflightLoading,
    submitting,
    resultUnknown,
    actionError,
    activeJobId,
    activeJob,
    operationState,
    operationLoading,
    operationApplied,
    hasBlockingOperation,
    diagnosticText,
    initialize,
    refreshSelfCheck,
    refreshBackups,
    startPreflight,
    submitConfirmed,
    stageImport,
    recoverTrackedOperation,
    refreshOperation,
    cancelCurrent,
    downloadActive,
    resetFlow,
  }
})
