<script setup lang="ts">
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  reactive,
  ref,
} from 'vue'

import {
  aiDiagnosticsApi,
  type AiDiagnosticDetail,
  type AiDiagnosticOutcome,
  type AiDiagnosticSummary,
} from '../api/ai-diagnostics'
import {
  copyModelTaskBindings,
  MODEL_PROFILE_LIMITS,
  modelProfilesApi,
  type ModelExecutionStatus,
  type ModelProfile,
  type ModelProfileUpsertInput,
  type ModelTaskBindings,
  type RequestSpeedMode,
} from '../api/model-profiles'
import { useModelProfilesStore } from '../stores/model-profiles'
import { workspaceAITaskApi } from '../workspaces/shared/ai-tasks/api'
import type { WorkspaceAITask } from '../workspaces/shared/ai-tasks/contracts'
import '../styles/model-profiles.css'

const props = withDefaults(defineProps<{ compact?: boolean }>(), { compact: false })

interface ModelProfileDraft {
  sourceName: string | null
  name: string
  baseUrl: string
  apiKey: string
  hasApiKey: boolean
  ocrModel: string
  gradingModel: string
  configBaseUrl: string
  configApiKey: string
  hasConfigApiKey: boolean
  configModel: string
  teachingPrepModel: string
  classTeacherModel: string
  requestSpeedMode: RequestSpeedMode
  maxConcurrentRequests: number
  requestsPerMinute: number
}

type DiagnosticTab = 'request' | 'attachments' | 'response' | 'parsed' | 'error'

const profilesStore = useModelProfilesStore()
const formElement = ref<HTMLFormElement | null>(null)
const nameInput = ref<HTMLInputElement | null>(null)
const localError = ref('')
const baseline = ref('')
const diagnostics = ref<AiDiagnosticSummary[]>([])
const diagnosticsState = ref<'idle' | 'loading' | 'error'>('idle')
const diagnosticsError = ref('')
const workspaceTaskRecords = ref<WorkspaceAITask[]>([])
const workspaceTaskRecordsState = ref<'idle' | 'loading' | 'error'>('idle')
const workspaceTaskRecordsError = ref('')
const diagnosticOutcome = ref<'' | AiDiagnosticOutcome>('')
const diagnosticKind = ref('')
const workspaceModuleFilter = ref<'' | WorkspaceAITask['module']>('')
const workspaceTaskKindFilter = ref('')
const selectedDiagnosticId = ref('')
const selectedDiagnostic = ref<AiDiagnosticDetail | null>(null)
const diagnosticDetailState = ref<'idle' | 'loading' | 'error'>('idle')
const diagnosticTab = ref<DiagnosticTab>('request')
const executionStatus = ref<ModelExecutionStatus | null>(null)
const executionStatusState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const executionStatusError = ref('')
const taskBindingsDraft = ref<ModelTaskBindings>({
  content_generation: { profile_name: null, model: '' },
  grading: { profile_name: null, model: '' },
  teaching_prep: { profile_name: null, model: '' },
  class_teacher: { profile_name: null, model: '' },
})
const taskRows = [
  { key: 'content_generation', title: '题库与评分标准生成', detail: '题库标注、试题和评分标准使用同一个模型' },
  { key: 'grading', title: '识别姓名与批改试卷', detail: '姓名识别和批改共用同一个模型与站点' },
  { key: 'teaching_prep', title: '备课工作台', detail: '备课对话、资料整理与生成' },
  { key: 'class_teacher', title: '班主任工作台', detail: '班主任对话与草稿整理' },
] as const
let diagnosticsController: AbortController | null = null
let diagnosticDetailController: AbortController | null = null
let executionStatusController: AbortController | null = null
let workspaceTaskRecordsController: AbortController | null = null

const draft = reactive<ModelProfileDraft>({
  sourceName: null,
  name: '',
  baseUrl: '',
  apiKey: '',
  hasApiKey: false,
  ocrModel: '',
  gradingModel: '',
  configBaseUrl: '',
  configApiKey: '',
  hasConfigApiKey: false,
  configModel: '',
  teachingPrepModel: '',
  classTeacherModel: '',
  requestSpeedMode: 'automatic',
  maxConcurrentRequests: 20,
  requestsPerMinute: 1000,
})

function draftSnapshot(): string {
  return JSON.stringify({
    sourceName: draft.sourceName,
    name: draft.name,
    baseUrl: draft.baseUrl,
    apiKey: draft.apiKey,
    ocrModel: draft.ocrModel,
    gradingModel: draft.gradingModel,
    configBaseUrl: draft.configBaseUrl,
    configApiKey: draft.configApiKey,
    configModel: draft.configModel,
    teachingPrepModel: draft.teachingPrepModel,
    classTeacherModel: draft.classTeacherModel,
    requestSpeedMode: draft.requestSpeedMode,
    maxConcurrentRequests: draft.maxConcurrentRequests,
    requestsPerMinute: draft.requestsPerMinute,
  })
}

baseline.value = draftSnapshot()

const isNew = computed(() => draft.sourceName === null)
const isDirty = computed(() => draftSnapshot() !== baseline.value)
defineExpose({ hasUnsavedChanges: isDirty })
const isBusy = computed(() => profilesStore.operationState !== 'idle')
const isCurrent = computed(() => (
  draft.sourceName !== null
  && draft.sourceName === profilesStore.activeProfileName
))
const canActivate = computed(() => (
  draft.sourceName !== null
  && !isCurrent.value
  && !isBusy.value
))
const currentProfileLabel = computed(() => (
  profilesStore.activeProfile?.name ?? '尚未选择'
))
const editStatus = computed(() => {
  if (isNew.value) {
    return isDirty.value ? '新配置尚未保存' : '填写后保存为新配置'
  }
  return isDirty.value ? '有未保存修改' : '已与本机保存内容同步'
})
const requestSpeedSummary = computed(() => {
  if (draft.requestSpeedMode === 'conservative') {
    return '同时处理 1 个请求，每分钟最多启动 60 个请求。'
  }
  if (draft.requestSpeedMode === 'custom') {
    return `同时最多 ${draft.maxConcurrentRequests} 个请求，`
      + `每分钟最多启动 ${draft.requestsPerMinute} 个请求。`
  }
  return '从 6 个同时请求起步，稳定后逐步提高，最多 20 个；每分钟最多启动 1000 个请求。'
})
const executionLimitingMessage = computed(() => {
  if (executionStatus.value?.limiting_reason === 'provider_overload') {
    return '模型服务刚才返回了拥堵或限流，系统已自动降低同时请求数。'
  }
  if (executionStatus.value?.limiting_reason === 'recovering') {
    return '模型服务正在恢复稳定，系统会逐步提高同时请求数。'
  }
  return '当前按照已保存的请求速度方案运行。'
})

function resetExecutionStatus(): void {
  executionStatusController?.abort()
  executionStatusController = null
  executionStatus.value = null
  executionStatusState.value = 'idle'
  executionStatusError.value = ''
}

async function loadExecutionStatus(
  profileName: string | null = draft.sourceName,
): Promise<void> {
  executionStatusController?.abort()
  if (profileName === null) {
    resetExecutionStatus()
    return
  }
  const controller = new AbortController()
  executionStatusController = controller
  executionStatusState.value = 'loading'
  executionStatusError.value = ''
  try {
    const status = await modelProfilesApi.getExecutionStatus(
      profileName,
      controller.signal,
    )
    if (controller.signal.aborted || draft.sourceName !== profileName) return
    executionStatus.value = status
    executionStatusState.value = 'ready'
  } catch (error) {
    if (controller.signal.aborted || draft.sourceName !== profileName) return
    executionStatus.value = null
    executionStatusState.value = 'error'
    executionStatusError.value = error instanceof Error
      ? error.message
      : '当前运行状态没有读取成功。'
  } finally {
    if (executionStatusController === controller) {
      executionStatusController = null
    }
  }
}

function applyProfile(profile: ModelProfile): void {
  Object.assign(draft, {
    sourceName: profile.name,
    name: profile.name,
    baseUrl: profile.base_url,
    apiKey: '',
    hasApiKey: profile.has_api_key,
    ocrModel: profile.ocr_model,
    gradingModel: profile.grading_model,
    configBaseUrl: profile.config_base_url,
    configApiKey: '',
    hasConfigApiKey: profile.has_config_api_key,
    configModel: profile.config_model,
    teachingPrepModel: profile.teaching_prep_model,
    classTeacherModel: profile.class_teacher_model,
    requestSpeedMode: profile.request_speed_mode,
    maxConcurrentRequests: profile.max_concurrent_requests,
    requestsPerMinute: profile.requests_per_minute,
  } satisfies ModelProfileDraft)
  localError.value = ''
  baseline.value = draftSnapshot()
}

function applyBlankProfile(): void {
  Object.assign(draft, {
    sourceName: null,
    name: '',
    baseUrl: '',
    apiKey: '',
    hasApiKey: false,
    ocrModel: '',
    gradingModel: '',
    configBaseUrl: '',
    configApiKey: '',
    hasConfigApiKey: false,
    configModel: '',
    teachingPrepModel: '',
    classTeacherModel: '',
    requestSpeedMode: 'automatic',
    maxConcurrentRequests: 20,
    requestsPerMinute: 1000,
  } satisfies ModelProfileDraft)
  localError.value = ''
  baseline.value = draftSnapshot()
}

function syncFromSelection(): void {
  taskBindingsDraft.value = copyModelTaskBindings(profilesStore.taskBindings)
  if (profilesStore.selectedProfile) {
    applyProfile(profilesStore.selectedProfile)
  } else {
    applyBlankProfile()
  }
}

async function saveTaskBindings(): Promise<void> {
  localError.value = ''
  await profilesStore.saveTaskBindings(taskBindingsDraft.value)
  taskBindingsDraft.value = copyModelTaskBindings(profilesStore.taskBindings)
}

function confirmDiscard(message: string): boolean {
  return !isDirty.value || window.confirm(message)
}

function chooseProfile(profile: ModelProfile): void {
  if (
    profile.name === draft.sourceName
    || !confirmDiscard('当前表单有未保存修改。切换配置会丢弃这些修改，是否继续？')
  ) return
  profilesStore.selectProfile(profile.name)
  applyProfile(profile)
  void loadExecutionStatus(profile.name)
}

async function beginNewProfile(): Promise<void> {
  if (!confirmDiscard('当前表单有未保存修改。新建配置会丢弃这些修改，是否继续？')) {
    return
  }
  profilesStore.selectProfile(null)
  applyBlankProfile()
  resetExecutionStatus()
  await nextTick()
  nameInput.value?.focus()
}

function toUpsertInput(): ModelProfileUpsertInput {
  return {
    name: draft.name,
    base_url: draft.baseUrl,
    api_key: draft.apiKey,
    ocr_model: draft.ocrModel,
    grading_model: draft.gradingModel,
    config_base_url: draft.configBaseUrl,
    config_api_key: draft.configApiKey,
    config_model: draft.configModel,
    teaching_prep_model: draft.teachingPrepModel,
    class_teacher_model: draft.classTeacherModel,
    request_speed_mode: draft.requestSpeedMode,
    max_concurrent_requests: draft.maxConcurrentRequests,
    requests_per_minute: draft.requestsPerMinute,
  }
}

async function saveProfile(): Promise<void> {
  localError.value = ''
  profilesStore.clearMessages()
  if (!formElement.value?.reportValidity()) return
  if (isNew.value && draft.apiKey.trim() === '') {
    localError.value = '新配置需要填写 API 密钥。'
    return
  }
  const saved = await profilesStore.saveProfile(
    draft.sourceName,
    toUpsertInput(),
  )
  if (saved) {
    applyProfile(saved)
    void loadExecutionStatus(saved.name)
  }
}

async function activateProfile(): Promise<void> {
  if (draft.sourceName === null || !canActivate.value) return
  if (
    isDirty.value
    && !window.confirm(
      '当前表单有未保存修改。切换当前配置会使用已经保存的版本，并丢弃这些修改，是否继续？',
    )
  ) return
  if (isDirty.value) {
    const saved = profilesStore.profiles.find(
      ({ name }) => name === draft.sourceName,
    )
    if (saved) applyProfile(saved)
  }
  await profilesStore.activateProfile(draft.sourceName)
  void loadExecutionStatus(draft.sourceName)
}

async function deleteProfile(): Promise<void> {
  if (draft.sourceName === null || isBusy.value) return
  const name = draft.sourceName
  if (!window.confirm(
    `确定永久删除模型配置“${name}”吗？\n\n使用它的工作模型安排会自动改用剩余的当前配置；如果没有其他配置，对应 AI 功能会暂时不可用。`,
  )) return
  const deleted = await profilesStore.deleteProfile(name)
  if (!deleted) return
  syncFromSelection()
  void loadExecutionStatus()
}

async function reloadProfiles(): Promise<void> {
  if (
    !confirmDiscard('重新加载会丢弃当前未保存修改，是否继续？')
  ) return
  const loaded = await profilesStore.load()
  if (loaded) {
    syncFromSelection()
    void loadExecutionStatus()
  }
}

function handleBeforeUnload(event: BeforeUnloadEvent): void {
  if (!isDirty.value) return
  event.preventDefault()
  event.returnValue = ''
}

const diagnosticKinds = [
  { value: '', label: '全部用途' },
  { value: 'recognition', label: '识别' },
  { value: 'grading', label: '批改' },
  { value: 'config_generation', label: '评分标准' },
  { value: 'tagging', label: '题库标注' },
  { value: 'workspace', label: '工作台' },
] as const
const workspaceModules = [
  { value: '', label: '全部工作台' },
  { value: 'teaching_prep', label: '备课工作台' },
  { value: 'class_teacher', label: '班主任工作台' },
] as const
const workspaceTaskKinds = [
  { value: '', module: '', label: '全部功能' },
  { value: 'teaching_prep.semester_mapping', module: 'teaching_prep', label: '学期资料整理' },
  { value: 'teaching_prep.lesson_plan', module: 'teaching_prep', label: '课时备课方案' },
  { value: 'teaching_prep.exercise_suggestions', module: 'teaching_prep', label: '练习建议' },
  { value: 'teaching_prep.slide_change_proposal', module: 'teaching_prep', label: '课件改编方案' },
  { value: 'class_teacher.intake_triage', module: 'class_teacher', label: '事项整理' },
  { value: 'class_teacher.draft_revision', module: 'class_teacher', label: '草稿修订' },
  { value: 'class_teacher.intake', module: 'class_teacher', label: '旧版事项整理' },
] as const
const availableWorkspaceTaskKinds = computed(() => workspaceTaskKinds.filter(
  ({ module }) => !module || !workspaceModuleFilter.value || module === workspaceModuleFilter.value,
))
const workspaceTasksByOperation = computed(() => new Map(
  workspaceTaskRecords.value.map((task) => [task.operation_id, task]),
))
const recentWorkspaceTaskRecords = computed(() => workspaceTaskRecords.value.slice(0, 20))
const visibleDiagnostics = computed(() => diagnostics.value.filter((call) => {
  if (!workspaceModuleFilter.value && !workspaceTaskKindFilter.value) return true
  if (call.request_kind !== 'workspace') return false
  const task = workspaceTasksByOperation.value.get(call.operation_id)
  if (!task) return false
  return (
    (!workspaceModuleFilter.value || task.module === workspaceModuleFilter.value)
    && (!workspaceTaskKindFilter.value || task.task_kind === workspaceTaskKindFilter.value)
  )
}))
const diagnosticTabs: readonly {
  value: DiagnosticTab
  label: string
}[] = [
  { value: 'request', label: '发送内容' },
  { value: 'attachments', label: '附件' },
  { value: 'response', label: '原始返回' },
  { value: 'parsed', label: '解析结果' },
  { value: 'error', label: '错误与重试' },
]

function selectDiagnosticTab(tab: DiagnosticTab): void {
  diagnosticTab.value = tab
}

function diagnosticKindLabel(value: string): string {
  return diagnosticKinds.find((item) => item.value === value)?.label ?? value
}

function workspaceModuleLabel(module: WorkspaceAITask['module']): string {
  return module === 'teaching_prep' ? '备课' : '班主任'
}

function workspaceTaskKindLabel(taskKind: string): string {
  return workspaceTaskKinds.find(({ value }) => value === taskKind)?.label ?? '其他功能'
}

function diagnosticDisplayLabel(call: AiDiagnosticSummary): string {
  if (call.request_kind !== 'workspace') return diagnosticKindLabel(call.request_kind)
  const task = workspaceTasksByOperation.value.get(call.operation_id)
  if (!task) return '工作台 · 功能未知'
  return `${workspaceModuleLabel(task.module)} · ${workspaceTaskKindLabel(task.task_kind)}`
}

function clearHiddenDiagnosticSelection(): void {
  if (
    selectedDiagnosticId.value
    && !visibleDiagnostics.value.some(({ call_id: callId }) => callId === selectedDiagnosticId.value)
  ) {
    selectedDiagnosticId.value = ''
    selectedDiagnostic.value = null
    diagnosticDetailState.value = 'idle'
  }
}

function handleDiagnosticKindChange(): void {
  if (diagnosticKind.value !== 'workspace') {
    workspaceModuleFilter.value = ''
    workspaceTaskKindFilter.value = ''
  }
  void loadDiagnostics()
}

function handleWorkspaceModuleChange(): void {
  const selected = workspaceTaskKinds.find(
    ({ value }) => value === workspaceTaskKindFilter.value,
  )
  if (
    selected?.module
    && workspaceModuleFilter.value
    && selected.module !== workspaceModuleFilter.value
  ) workspaceTaskKindFilter.value = ''
  clearHiddenDiagnosticSelection()
}

function handleWorkspaceTaskKindChange(): void {
  clearHiddenDiagnosticSelection()
}

function diagnosticOutcomeLabel(value: AiDiagnosticOutcome): string {
  if (value === 'success') return '模型已返回'
  if (value === 'failure') return '调用失败'
  return '等待返回'
}

function formatDiagnosticTime(value: string): string {
  if (!value) return '时间未知'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(parsed)
}

function formatDuration(milliseconds: number): string {
  if (milliseconds < 1_000) return `${milliseconds} 毫秒`
  return `${(milliseconds / 1_000).toFixed(milliseconds < 10_000 ? 1 : 0)} 秒`
}

function formatBytes(bytes: number): string {
  if (bytes < 1_024) return `${bytes} B`
  if (bytes < 1_048_576) return `${(bytes / 1_024).toFixed(1)} KB`
  return `${(bytes / 1_048_576).toFixed(1)} MB`
}

function formatDiagnosticJson(value: unknown): string {
  if (value === null || value === undefined) return '暂无内容'
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return '内容无法转换为可读文本'
  }
}

async function loadDiagnosticDetail(callId: string): Promise<void> {
  diagnosticDetailController?.abort()
  const controller = new AbortController()
  diagnosticDetailController = controller
  selectedDiagnosticId.value = callId
  selectedDiagnostic.value = null
  diagnosticDetailState.value = 'loading'
  diagnosticTab.value = 'request'
  try {
    selectedDiagnostic.value = await aiDiagnosticsApi.detail(
      callId,
      controller.signal,
    )
    diagnosticDetailState.value = 'idle'
  } catch (error) {
    if (controller.signal.aborted) return
    diagnosticDetailState.value = 'error'
    diagnosticsError.value = error instanceof Error
      ? error.message
      : 'AI 调用详情没有加载成功。'
  }
}

async function loadDiagnostics(): Promise<void> {
  diagnosticsController?.abort()
  const controller = new AbortController()
  diagnosticsController = controller
  diagnosticsState.value = 'loading'
  diagnosticsError.value = ''
  try {
    const result = await aiDiagnosticsApi.list({
      limit: 100,
      requestKind: diagnosticKind.value,
      outcome: diagnosticOutcome.value,
      signal: controller.signal,
    })
    diagnostics.value = result.items
    diagnosticsState.value = 'idle'
    const selectedStillVisible = visibleDiagnostics.value.some(
      ({ call_id: callId }) => callId === selectedDiagnosticId.value,
    )
    if (!selectedStillVisible) {
      selectedDiagnosticId.value = ''
      selectedDiagnostic.value = null
      diagnosticDetailState.value = 'idle'
    }
  } catch (error) {
    if (controller.signal.aborted) return
    diagnosticsState.value = 'error'
    diagnosticsError.value = error instanceof Error
      ? error.message
      : 'AI 调用记录没有加载成功。'
  }
}

async function loadWorkspaceTaskRecords(): Promise<void> {
  workspaceTaskRecordsController?.abort()
  const controller = new AbortController()
  workspaceTaskRecordsController = controller
  workspaceTaskRecordsState.value = 'loading'
  workspaceTaskRecordsError.value = ''
  try {
    const [teachingPrep, classTeacher] = await Promise.all([
      workspaceAITaskApi.list('teaching_prep', controller.signal),
      workspaceAITaskApi.list('class_teacher', controller.signal),
    ])
    if (controller.signal.aborted) return
    workspaceTaskRecords.value = [...teachingPrep, ...classTeacher]
      .sort((left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at))
    clearHiddenDiagnosticSelection()
    workspaceTaskRecordsState.value = 'idle'
  } catch (error) {
    if (controller.signal.aborted) return
    workspaceTaskRecordsState.value = 'error'
    workspaceTaskRecordsError.value = error instanceof Error
      ? error.message
      : '工作台 AI 任务记录没有加载成功。'
  }
}

function workspaceTaskStatus(task: WorkspaceAITask): string {
  if (task.status === 'proposal_ready') return '已返回可审核结果'
  if (task.status === 'needs_input') return '等待补充信息'
  if (task.status === 'prepared') return '尚未发送'
  if (task.status === 'queued') return '等待发送'
  if (task.status === 'running') return '处理中'
  if (task.status === 'result_unknown') return '已尝试发送，结果无法确认'
  if (task.status.startsWith('failed') || task.status === 'invalid_result') return '失败'
  if (task.status.includes('cancel') || task.status === 'discarded') return '已取消'
  return task.status
}

function workspaceDispatchCopy(task: WorkspaceAITask): string {
  if (task.dispatch_evidence === 'response_persisted') return '已收到并保存响应'
  if (task.dispatch_evidence === 'may_have_started') return '请求可能已发出'
  return '尚无发送证据'
}

function handleDiagnosticsToggle(event: Event): void {
  const disclosure = event.currentTarget as HTMLDetailsElement
  if (disclosure.open && diagnosticsState.value === 'idle' && diagnostics.value.length === 0) {
    void loadDiagnostics()
  }
  if (
    disclosure.open
    && workspaceTaskRecordsState.value === 'idle'
    && workspaceTaskRecords.value.length === 0
  ) void loadWorkspaceTaskRecords()
}

onMounted(async () => {
  window.addEventListener('beforeunload', handleBeforeUnload)
  const loaded = await profilesStore.load()
  if (loaded) {
    syncFromSelection()
    void loadExecutionStatus()
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', handleBeforeUnload)
  diagnosticsController?.abort()
  diagnosticDetailController?.abort()
  executionStatusController?.abort()
  workspaceTaskRecordsController?.abort()
})
</script>

<template>
  <article class="model-profiles-view">
    <header v-if="!props.compact" class="model-profiles-view__header">
      <div>
        <p class="model-profiles-view__eyebrow">LOCAL MODEL ROUTING</p>
        <h1 tabindex="-1">API 站点与工作模型</h1>
        <p>先保存可用的 API 站点，再为四类工作分别指定站点和模型。</p>
      </div>
      <div class="model-profiles-view__current" aria-live="polite">
        <span class="model-profiles-view__beacon" aria-hidden="true" />
        <span>
          <small>兼容旧任务的默认站点</small>
          <strong>{{ currentProfileLabel }}</strong>
        </span>
      </div>
    </header>

    <section v-if="!props.compact" class="model-profiles-notice" aria-label="费用与密钥说明">
      <p>
        <strong>保存配置不会调用模型，不会产生费用。</strong>
        只有之后真正执行识别、批改或标注任务时，才可能产生模型费用。
      </p>
      <p>
        <strong>密钥只保存在本机且页面无法读回。</strong>
        已保存的密钥只显示“已保存”，不会以明文返回页面。
      </p>
    </section>

    <section
      v-if="profilesStore.loadState === 'ready' || profilesStore.loadState === 'empty'"
      class="model-task-routing"
      aria-labelledby="model-task-routing-title"
    >
      <header>
        <div>
          <p class="model-profiles-view__eyebrow">工作模型</p>
          <h2 id="model-task-routing-title">四类工作，各自选择站点和模型</h2>
          <p>一个站点就是一组 API 地址和密钥；不同工作可以使用不同站点。</p>
        </div>
        <button
          type="button"
          class="model-profiles-button model-profiles-button--primary"
          :disabled="isBusy || profilesStore.profiles.length === 0"
          @click="saveTaskBindings"
        >保存工作模型</button>
      </header>
      <p v-if="profilesStore.profiles.length === 0" class="model-task-routing__empty">
        请先在下方新增一个 API 站点，再安排工作模型。
      </p>
      <div class="model-task-routing__grid">
        <label v-for="row in taskRows" :key="row.key" class="model-task-row">
          <span class="model-task-row__title"><strong>{{ row.title }}</strong><small>{{ row.detail }}</small></span>
          <select
            v-model="taskBindingsDraft[row.key].profile_name"
            :aria-label="`${row.title}使用的 API 站点`"
            :disabled="profilesStore.profiles.length === 0 || isBusy"
          >
            <option :value="null" disabled>选择 API 站点</option>
            <option v-for="profile in profilesStore.profiles" :key="profile.name" :value="profile.name">{{ profile.name }}</option>
          </select>
          <input
            v-model="taskBindingsDraft[row.key].model"
            type="text"
            :maxlength="MODEL_PROFILE_LIMITS.model"
            :aria-label="`${row.title}使用的模型`"
            :disabled="profilesStore.profiles.length === 0 || isBusy"
            placeholder="填写模型名称"
          >
        </label>
      </div>
    </section>

    <p
      v-if="profilesStore.errorMessage || localError"
      class="model-profiles-feedback model-profiles-feedback--error"
      role="alert"
    >
      {{ localError || profilesStore.errorMessage }}
    </p>
    <p
      v-else-if="profilesStore.noticeMessage"
      class="model-profiles-feedback model-profiles-feedback--success"
      role="status"
    >
      {{ profilesStore.noticeMessage }}
    </p>

    <section
      v-if="profilesStore.loadState === 'loading'"
      class="model-profiles-state"
      role="status"
    >
      正在读取本机模型配置…
    </section>
    <section
      v-else-if="profilesStore.loadState === 'error'"
      class="model-profiles-state model-profiles-state--error"
    >
      <p>配置列表没有加载成功，页面没有改变任何配置。</p>
      <button
        type="button"
        class="model-profiles-button model-profiles-button--secondary"
        @click="reloadProfiles"
      >
        重新加载
      </button>
    </section>

    <details v-else class="model-profiles-advanced-shell" :open="!props.compact">
      <summary>高级设置：API 站点、密钥与请求速度</summary>
      <div class="model-profiles-workspace">
      <aside class="model-profiles-index" aria-label="API 站点列表">
        <header>
          <div>
            <p>API 站点</p>
            <span>{{ profilesStore.profiles.length }} 个</span>
          </div>
          <button
            type="button"
            class="model-profiles-button model-profiles-button--quiet"
            :disabled="isBusy"
            @click="beginNewProfile"
          >
            新增站点
          </button>
        </header>

        <p
          v-if="profilesStore.profiles.length === 0"
          class="model-profiles-index__empty"
        >
          还没有 API 站点。先在右侧填写第一个站点。
        </p>
        <ul v-else class="model-profiles-index__list">
          <li v-for="profile in profilesStore.profiles" :key="profile.name">
            <button
              type="button"
              class="model-profile-item"
              :class="{
                'model-profile-item--selected': profile.name === draft.sourceName,
              }"
              :aria-current="profile.name === draft.sourceName ? 'true' : undefined"
              :disabled="isBusy"
              @click="chooseProfile(profile)"
            >
              <span class="model-profile-item__heading">
                <strong>{{ profile.name }}</strong>
                <span
                  v-if="profile.name === profilesStore.activeProfileName"
                  class="model-profile-item__active"
                >
                  <i aria-hidden="true" />
                  当前
                </span>
              </span>
              <small>{{ profile.ocr_model }} · {{ profile.grading_model }}</small>
              <span class="model-profile-item__key-state">
                {{ profile.has_api_key ? '主密钥已保存' : '主密钥未保存' }}
              </span>
            </button>
          </li>
        </ul>
      </aside>

      <form
        ref="formElement"
        class="model-profile-editor"
        :aria-busy="isBusy"
        @submit.prevent="saveProfile"
      >
        <header class="model-profile-editor__header">
          <div>
            <p>{{ isNew ? 'NEW API SITE' : 'API SITE' }}</p>
            <h2>{{ isNew ? '新增 API 站点' : draft.sourceName }}</h2>
            <span>{{ editStatus }}</span>
          </div>
          <span
            v-if="isCurrent"
            class="model-profile-editor__active"
          >
            当前正在使用
          </span>
        </header>

        <div class="model-profile-fields">
          <label class="model-profile-field model-profile-field--wide">
            <span>站点名称</span>
            <input
              ref="nameInput"
              v-model="draft.name"
              name="profile-name"
              type="text"
              autocomplete="off"
              :maxlength="MODEL_PROFILE_LIMITS.name"
              placeholder="例如：校内模型站点"
              :disabled="isBusy"
              :readonly="!isNew"
              required
            >
            <small v-if="!isNew">已保存配置的名称固定；需要新名称时请新建一套配置。</small>
            <small>用于区分不同服务商或校内代理。</small>
          </label>

          <label class="model-profile-field model-profile-field--wide">
            <span>API 地址</span>
            <input
              v-model="draft.baseUrl"
              name="base-url"
              type="url"
              inputmode="url"
              autocomplete="off"
              :maxlength="MODEL_PROFILE_LIMITS.url"
              placeholder="https://api.example.com/v1"
              :disabled="isBusy"
              required
            >
            <small>支持 http:// 或 https://，地址中不要写账号或密码。</small>
          </label>

          <label class="model-profile-field model-profile-field--wide">
            <span>API 密钥</span>
            <input
              v-model="draft.apiKey"
              name="api-key"
              type="password"
              autocomplete="new-password"
              :maxlength="MODEL_PROFILE_LIMITS.apiKey"
              :placeholder="draft.hasApiKey ? '已保存；留空保持不变' : '粘贴 API 密钥'"
              :disabled="isBusy"
              :required="isNew"
            >
            <small>
              {{ draft.hasApiKey
                ? '密钥已保存在本机。页面无法读回；留空不会覆盖。'
                : '尚未保存主密钥。新配置保存前必须填写。' }}
            </small>
          </label>

        </div>

        <fieldset class="model-profile-execution">
          <legend>AI 请求速度</legend>
          <p class="model-profile-execution__intro">
            姓名识别、试卷批改、评分配置和题库标注共用这套限制。
            学生可以一次全部进入队列，程序只按这里允许的数量同时请求模型。
          </p>
          <div class="model-profile-execution__modes">
            <label
              class="model-profile-speed-option"
              :class="{ 'model-profile-speed-option--selected': draft.requestSpeedMode === 'automatic' }"
            >
              <input
                v-model="draft.requestSpeedMode"
                type="radio"
                name="request-speed-mode"
                value="automatic"
                :disabled="isBusy"
              >
              <span>
                <strong>自动（推荐）</strong>
                <small>遇到限流自动降低，同时请求稳定后再缓慢恢复。</small>
              </span>
            </label>
            <label
              class="model-profile-speed-option"
              :class="{ 'model-profile-speed-option--selected': draft.requestSpeedMode === 'conservative' }"
            >
              <input
                v-model="draft.requestSpeedMode"
                type="radio"
                name="request-speed-mode"
                value="conservative"
                :disabled="isBusy"
              >
              <span>
                <strong>保守</strong>
                <small>一次只发送一个请求，适合接口不稳定时临时使用。</small>
              </span>
            </label>
            <label
              class="model-profile-speed-option"
              :class="{ 'model-profile-speed-option--selected': draft.requestSpeedMode === 'custom' }"
            >
              <input
                v-model="draft.requestSpeedMode"
                type="radio"
                name="request-speed-mode"
                value="custom"
                :disabled="isBusy"
              >
              <span>
                <strong>自定义</strong>
                <small>设置允许的上限；供应商限流时仍会自动减速。</small>
              </span>
            </label>
          </div>
          <div
            v-if="draft.requestSpeedMode === 'custom'"
            class="model-profile-execution__custom"
          >
            <label class="model-profile-field">
              <span>最多同时请求数</span>
              <input
                v-model.number="draft.maxConcurrentRequests"
                name="max-concurrent-requests"
                type="number"
                min="1"
                :max="MODEL_PROFILE_LIMITS.concurrentRequests"
                step="1"
                :disabled="isBusy"
                required
              >
              <small>这是 worker 的实际含义；填 100 不代表供应商一定接受 100 个并发。</small>
            </label>
            <label class="model-profile-field">
              <span>每分钟请求数（RPM）</span>
              <input
                v-model.number="draft.requestsPerMinute"
                name="requests-per-minute"
                type="number"
                min="1"
                :max="MODEL_PROFILE_LIMITS.requestsPerMinute"
                step="1"
                :disabled="isBusy"
                required
              >
              <small>RPM 只限制启动频率，不会自动增加同时处理数量。</small>
            </label>
          </div>
          <p class="model-profile-execution__summary" aria-live="polite">
            <strong>当前计划：</strong>{{ requestSpeedSummary }}
          </p>
          <section
            v-if="!isNew"
            class="model-profile-runtime"
            aria-label="AI 请求运行状态"
          >
            <header>
              <div>
                <strong>当前运行状态</strong>
                <small>本次程序启动以来，四类 AI 请求共用</small>
              </div>
              <button
                type="button"
                class="model-profiles-button model-profiles-button--quiet"
                :disabled="executionStatusState === 'loading'"
                @click="loadExecutionStatus()"
              >
                {{ executionStatusState === 'loading' ? '正在刷新…' : '刷新状态' }}
              </button>
            </header>
            <p
              v-if="executionStatusState === 'loading' && executionStatus === null"
              class="model-profile-runtime__state"
              role="status"
            >
              正在读取本机运行状态…
            </p>
            <p
              v-else-if="executionStatusState === 'error'"
              class="model-profile-runtime__state model-profile-runtime__state--error"
              role="alert"
            >
              {{ executionStatusError || '当前运行状态没有读取成功。' }}
            </p>
            <template v-else-if="executionStatus !== null">
              <dl class="model-profile-runtime__metrics">
                <div>
                  <dt>正在请求</dt>
                  <dd>{{ executionStatus.active }}</dd>
                </div>
                <div>
                  <dt>排队等待</dt>
                  <dd>{{ executionStatus.queued }}</dd>
                </div>
                <div>
                  <dt>当前同时上限</dt>
                  <dd>
                    {{ executionStatus.effective_max_in_flight }}
                    <small>/ 计划 {{ executionStatus.configured_max_in_flight }}</small>
                  </dd>
                </div>
                <div>
                  <dt>启动后峰值</dt>
                  <dd>{{ executionStatus.peak_active }}</dd>
                </div>
              </dl>
              <p class="model-profile-runtime__reason">
                {{ executionLimitingMessage }}
                本次启动已向模型实际发送
                {{ executionStatus.physical_request_count }} 个请求。
              </p>
            </template>
          </section>
          <p class="model-profile-execution__note">
            保存不会测试接口或产生费用；新设置从下一次任务启动时生效，
            已经运行的任务继续使用启动时的方案。
          </p>
        </fieldset>

        <footer class="model-profile-editor__actions">
          <div>
            <strong>{{ editStatus }}</strong>
            <span>保存和切换都只修改本机设置，不会测试连接。</span>
          </div>
          <div class="model-profile-editor__buttons">
            <button
              v-if="!isNew"
              type="button"
              class="model-profiles-button model-profiles-button--danger"
              :disabled="isBusy"
              @click="deleteProfile"
            >
              {{ profilesStore.operationState === 'deleting'
                ? '正在删除…'
                : '删除配置' }}
            </button>
            <button
              type="button"
              class="model-profiles-button model-profiles-button--secondary"
              :disabled="!canActivate"
              @click="activateProfile"
            >
              {{ profilesStore.operationState === 'activating'
                ? '正在切换…'
                : isCurrent ? '当前配置' : '设为当前配置' }}
            </button>
            <button
              type="submit"
              class="model-profiles-button model-profiles-button--primary"
              :disabled="isBusy || !isDirty"
            >
              {{ profilesStore.operationState === 'saving'
                ? '正在保存…'
                : '保存配置' }}
            </button>
          </div>
        </footer>
      </form>
      </div>
    </details>

    <details class="ai-diagnostics-disclosure" @toggle="handleDiagnosticsToggle">
      <summary>调用记录与排查工具（需要时展开）</summary>
    <section class="ai-diagnostics" aria-labelledby="ai-diagnostics-title">
      <header class="ai-diagnostics__header">
        <div>
          <p class="model-profiles-view__eyebrow">LOCAL AI TRACE</p>
          <h2 id="ai-diagnostics-title">AI 调用记录</h2>
          <p>按用途、工作台、具体功能和结果分类查询每次发送、返回与解析过程。</p>
        </div>
        <div class="ai-diagnostics__filters">
          <label>
            <span>来源</span>
            <select v-model="diagnosticKind" @change="handleDiagnosticKindChange">
              <option
                v-for="kind in diagnosticKinds"
                :key="kind.value"
                :value="kind.value"
              >
                {{ kind.label }}
              </option>
            </select>
          </label>
          <label v-if="diagnosticKind === 'workspace'">
            <span>工作台</span>
            <select v-model="workspaceModuleFilter" @change="handleWorkspaceModuleChange">
              <option
                v-for="module in workspaceModules"
                :key="module.value"
                :value="module.value"
              >
                {{ module.label }}
              </option>
            </select>
          </label>
          <label v-if="diagnosticKind === 'workspace'">
            <span>具体功能</span>
            <select v-model="workspaceTaskKindFilter" @change="handleWorkspaceTaskKindChange">
              <option
                v-for="taskKind in availableWorkspaceTaskKinds"
                :key="taskKind.value"
                :value="taskKind.value"
              >
                {{ taskKind.label }}
              </option>
            </select>
          </label>
          <label>
            <span>结果</span>
            <select v-model="diagnosticOutcome" @change="loadDiagnostics">
              <option value="">全部结果</option>
              <option value="success">模型已返回</option>
              <option value="failure">调用失败</option>
              <option value="pending">等待返回</option>
            </select>
          </label>
          <button
            type="button"
            class="model-profiles-button model-profiles-button--secondary"
            :disabled="diagnosticsState === 'loading'"
            @click="loadDiagnostics"
          >
            {{ diagnosticsState === 'loading' ? '正在刷新…' : '刷新记录' }}
          </button>
        </div>
      </header>

      <p class="ai-diagnostics__privacy">
        <strong>“模型已返回”只表示网络请求和模型响应完成。</strong>
        JSON 结构、题号与本地业务规则是否通过，请以对应生成或批改任务页显示的结果为准。
      </p>

      <p class="ai-diagnostics__privacy">
        <strong>本机敏感记录。</strong>
        日志包含实际文本请求和模型原始响应；只有图片正文不落盘，仅记录用途、
        大小和指纹。记录只保存在本机 <code>logs/</code>，该目录已被 Git 忽略，
        不会纳入代码提交；单个文件约 32 MB 时滚动，保留当前文件和最近 3 个旧文件。
        密钥和本机文件路径不会写入。
      </p>

      <section class="workspace-ai-records" aria-labelledby="workspace-ai-records-title">
        <header>
          <div>
            <strong id="workspace-ai-records-title">工作台 AI 任务记录</strong>
            <span>这里保留安全任务摘要；上方调用记录保存并展示实际发送正文和模型原文。</span>
          </div>
          <button
            type="button"
            class="model-profiles-button model-profiles-button--quiet"
            :disabled="workspaceTaskRecordsState === 'loading'"
            @click="loadWorkspaceTaskRecords"
          >
            {{ workspaceTaskRecordsState === 'loading' ? '正在刷新…' : '刷新工作台记录' }}
          </button>
        </header>
        <p v-if="workspaceTaskRecordsState === 'error'" class="workspace-ai-records__state is-error">
          {{ workspaceTaskRecordsError }}
        </p>
        <p v-else-if="workspaceTaskRecordsState === 'loading' && workspaceTaskRecords.length === 0" class="workspace-ai-records__state">
          正在读取工作台任务…
        </p>
        <p v-else-if="workspaceTaskRecords.length === 0" class="workspace-ai-records__state">
          还没有工作台 AI 任务记录。
        </p>
        <ul v-else>
          <li v-for="task in recentWorkspaceTaskRecords" :key="task.task_id">
            <div>
              <strong>{{ task.safe_title }}</strong>
              <span>{{ task.module === 'teaching_prep' ? '备课工作台' : '班主任工作台' }} · {{ formatDiagnosticTime(task.updated_at) }}</span>
            </div>
            <div class="workspace-ai-records__result">
              <strong>{{ workspaceTaskStatus(task) }}</strong>
              <span>{{ workspaceDispatchCopy(task) }}<template v-if="task.error_code"> · {{ task.error_code }}</template></span>
            </div>
          </li>
        </ul>
      </section>

      <p
        v-if="diagnosticsState === 'error'"
        class="model-profiles-feedback model-profiles-feedback--error"
        role="alert"
      >
        {{ diagnosticsError || 'AI 调用记录没有加载成功。' }}
      </p>

      <div class="ai-diagnostics__workspace">
        <aside class="ai-diagnostics-ledger" aria-label="AI 调用列表">
          <header>
            <strong>最近调用</strong>
            <span>{{ visibleDiagnostics.length }} 条</span>
          </header>
          <p
            v-if="diagnosticsState === 'loading' && diagnostics.length === 0"
            class="ai-diagnostics-ledger__state"
          >
            正在读取本机记录…
          </p>
          <p
            v-else-if="visibleDiagnostics.length === 0"
            class="ai-diagnostics-ledger__state"
          >
            还没有符合当前筛选条件的调用。执行相应功能后再刷新。
          </p>
          <ul v-else>
            <li v-for="call in visibleDiagnostics" :key="call.call_id">
              <button
                type="button"
                class="ai-diagnostic-call"
                :class="{
                  'ai-diagnostic-call--selected':
                    call.call_id === selectedDiagnosticId,
                }"
                :aria-current="
                  call.call_id === selectedDiagnosticId ? 'true' : undefined
                "
                @click="loadDiagnosticDetail(call.call_id)"
              >
                <span class="ai-diagnostic-call__heading">
                  <strong>{{ diagnosticDisplayLabel(call) }}</strong>
                  <span
                    class="ai-diagnostic-status"
                    :class="`ai-diagnostic-status--${call.outcome}`"
                  >
                    {{ diagnosticOutcomeLabel(call.outcome) }}
                  </span>
                </span>
                <span class="ai-diagnostic-call__model">{{ call.model || '模型未知' }}</span>
                <span class="ai-diagnostic-call__meta">
                  <time>{{ formatDiagnosticTime(call.started_at_utc) }}</time>
                  <span>{{ formatDuration(call.elapsed_ms) }}</span>
                  <span v-if="call.image_count">{{ call.image_count }} 张图片</span>
                  <span v-if="call.attempt > 1">第 {{ call.attempt }} 次尝试</span>
                </span>
              </button>
            </li>
          </ul>
        </aside>

        <article class="ai-diagnostic-detail" aria-live="polite">
          <div
            v-if="diagnosticDetailState === 'loading'"
            class="ai-diagnostic-detail__state"
          >
            正在读取这次调用的完整记录…
          </div>
          <div
            v-else-if="diagnosticDetailState === 'error'"
            class="ai-diagnostic-detail__state ai-diagnostic-detail__state--error"
          >
            {{ diagnosticsError || '这次调用的详情没有加载成功。' }}
          </div>
          <div
            v-else-if="selectedDiagnostic === null"
            class="ai-diagnostic-detail__empty"
          >
            <strong>选择一条调用记录</strong>
            <p>可以逐项查看实际发送文本、附件指纹、模型原始返回和解析结果。</p>
          </div>
          <template v-else>
            <header class="ai-diagnostic-detail__header">
              <div>
                <span>
                  {{ diagnosticDisplayLabel(selectedDiagnostic) }}
                  · 第 {{ selectedDiagnostic.attempt }} 次尝试
                </span>
                <h3>{{ selectedDiagnostic.model || '模型未知' }}</h3>
              </div>
              <span
                class="ai-diagnostic-status"
                :class="`ai-diagnostic-status--${selectedDiagnostic.outcome}`"
              >
                {{ diagnosticOutcomeLabel(selectedDiagnostic.outcome) }}
              </span>
            </header>

            <dl class="ai-diagnostic-facts">
              <div>
                <dt>开始时间</dt>
                <dd>{{ formatDiagnosticTime(selectedDiagnostic.started_at_utc) }}</dd>
              </div>
              <div>
                <dt>模型等待</dt>
                <dd>{{ formatDuration(selectedDiagnostic.elapsed_ms) }}</dd>
              </div>
              <div>
                <dt>协议</dt>
                <dd>{{ selectedDiagnostic.protocol }}</dd>
              </div>
              <div>
                <dt>服务地址</dt>
                <dd>{{ selectedDiagnostic.endpoint_host || '未记录' }}</dd>
              </div>
            </dl>

            <div class="ai-diagnostic-tabs" role="tablist" aria-label="调用详情">
              <button
                v-for="tab in diagnosticTabs"
                :key="tab.value"
                type="button"
                role="tab"
                class="ai-diagnostic-tab"
                :class="{ 'ai-diagnostic-tab--active': diagnosticTab === tab.value }"
                :aria-selected="diagnosticTab === tab.value"
                @click="selectDiagnosticTab(tab.value)"
              >
                {{ tab.label }}
                <template v-if="tab.value === 'attachments'">
                  {{ selectedDiagnostic.attachments.length }}
                </template>
              </button>
            </div>

            <section
              v-if="diagnosticTab === 'request'"
              class="ai-diagnostic-panel"
              aria-label="发送内容"
            >
              <p>图片正文已替换为附件编号；其余内容是发送给模型的文本和参数。</p>
              <pre>{{ formatDiagnosticJson(selectedDiagnostic.request) }}</pre>
            </section>

            <section
              v-else-if="diagnosticTab === 'attachments'"
              class="ai-diagnostic-panel"
              aria-label="附件"
            >
              <p v-if="selectedDiagnostic.attachments.length === 0">
                这次请求没有发送图片附件。
              </p>
              <div v-else class="ai-diagnostic-attachments">
                <article
                  v-for="attachment in selectedDiagnostic.attachments"
                  :key="attachment.id"
                >
                  <strong>{{ attachment.id }} · {{ attachment.purpose }}</strong>
                  <span>{{ attachment.mime_type }} · {{ formatBytes(attachment.bytes) }}</span>
                  <code>{{ attachment.sha256 }}</code>
                </article>
              </div>
            </section>

            <section
              v-else-if="diagnosticTab === 'response'"
              class="ai-diagnostic-panel"
              aria-label="原始返回"
            >
              <p>
                {{ selectedDiagnostic.response_chars
                  ? `${selectedDiagnostic.response_chars} 个字符`
                  : '没有收到可显示的文本返回' }}
              </p>
              <pre>{{ selectedDiagnostic.raw_response || '暂无原始返回' }}</pre>
            </section>

            <section
              v-else-if="diagnosticTab === 'parsed'"
              class="ai-diagnostic-panel"
              aria-label="解析结果"
            >
              <p>
                解析状态：{{ selectedDiagnostic.parse_status }}
                <template v-if="selectedDiagnostic.parse_operations.length">
                  · 本地修复：
                  {{ selectedDiagnostic.parse_operations.join('、') }}
                </template>
              </p>
              <p
                v-if="selectedDiagnostic.parse_error"
                class="ai-diagnostic-panel__error"
              >
                {{ selectedDiagnostic.parse_error }}
              </p>
              <pre>{{ formatDiagnosticJson(selectedDiagnostic.parsed_result) }}</pre>
            </section>

            <section
              v-else
              class="ai-diagnostic-panel"
              aria-label="错误与重试"
            >
              <dl class="ai-diagnostic-retry">
                <div>
                  <dt>当前尝试</dt>
                  <dd>
                    {{ selectedDiagnostic.retry_index + 1 }}
                    / {{ selectedDiagnostic.retry_limit + 1 }}
                  </dd>
                </div>
                <div>
                  <dt>是否继续重试</dt>
                  <dd>{{ selectedDiagnostic.will_retry ? '是' : '否' }}</dd>
                </div>
                <div>
                  <dt>重试等待</dt>
                  <dd>{{ formatDuration(selectedDiagnostic.retry_delay_ms) }}</dd>
                </div>
              </dl>
              <div
                v-if="selectedDiagnostic.error"
                class="ai-diagnostic-error-card"
              >
                <strong>
                  {{ selectedDiagnostic.error.category }}
                  · {{ selectedDiagnostic.error.exception_type }}
                </strong>
                <span v-if="selectedDiagnostic.error.http_status_code">
                  HTTP {{ selectedDiagnostic.error.http_status_code }}
                </span>
                <p>{{ selectedDiagnostic.error.message || '没有安全错误正文。' }}</p>
              </div>
              <p v-else>这次调用没有记录到错误。</p>
            </section>
          </template>
        </article>
      </div>
    </section>
    </details>
  </article>
</template>
