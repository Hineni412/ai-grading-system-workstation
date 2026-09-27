<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'

import ConfigStageRail from '../components/config/ConfigStageRail.vue'
import ConfigSourceUpload from '../components/config/ConfigSourceUpload.vue'
import ConfigGenerationPanel from '../components/config/ConfigGenerationPanel.vue'
import ConfigSaveResult from '../components/config/ConfigSaveResult.vue'
import QuestionBlockReview from '../components/config/QuestionBlockReview.vue'
import RubricEditorTable from '../components/config/RubricEditorTable.vue'
import ScoringUnitEditor from '../components/config/ScoringUnitEditor.vue'
import SessionDraftPanel from '../components/config/SessionDraftPanel.vue'
import AppButton from '../components/design-system/AppButton.vue'
import PageHeader from '../components/design-system/PageHeader.vue'
import { ApiError, isAmbiguousWriteError, isAuthoritativeNotFoundError } from '../api/errors'
import {
  abandonConfigGenerationRequest,
  createClientRequestToken,
  fetchConfigEditor,
  fetchConfigGenerationJobByToken,
  saveConfigEditor,
  submitConfigGeneration,
  type ConfigEditorCommand,
  type ConfigEditorResponse,
  type ConfigEditorSaveRequest,
  type ConfigEditorSaveResponse,
  type ConfigGenerationRequest,
} from '../api/config-workspace'
import type { JobResponse } from '../api/jobs'
import { fetchRegionReadiness, type RegionReadiness } from '../api/template-regions'
import { useConfigWorkspaceStore } from '../stores/config-workspace'
import { useJobStore } from '../stores/jobs'
import { useSessionStore } from '../stores/session'
import '../styles/session-config.css'

const props = withDefaults(defineProps<{
  editorSaver?: (sessionId: number, request: ConfigEditorSaveRequest) => Promise<ConfigEditorSaveResponse>
  editorLoader?: (sessionId: number) => Promise<ConfigEditorResponse>
  generationSubmitter?: (sessionId: number, request: ConfigGenerationRequest) => Promise<JobResponse>
  generationLoader?: (sessionId: number, requestToken: string) => Promise<JobResponse>
  requestAbandoner?: (sessionId: number, requestToken: string) => Promise<void>
  templateReadinessLoader?: (sessionId: number) => Promise<RegionReadiness>
}>(), {
  editorSaver: saveConfigEditor,
  editorLoader: fetchConfigEditor,
  generationSubmitter: submitConfigGeneration,
  generationLoader: fetchConfigGenerationJobByToken,
  requestAbandoner: abandonConfigGenerationRequest,
  templateReadinessLoader: fetchRegionReadiness,
})

const sessionStore = useSessionStore()
const configStore = useConfigWorkspaceStore()
const jobStore = useJobStore()
const requestedStage = ref(new URLSearchParams(window.location.search).get('stage'))
type StageId = 'draft' | 'source' | 'generation' | 'editor' | 'template'
const activePanel = ref<'source' | 'editor'>('source')
const sourceStage = ref<'draft' | 'source' | 'generation'>('draft')
const transitionName = ref<'config-forward' | 'config-back'>('config-forward')
const pendingStageFocus = ref<'draft' | 'source' | 'generation' | null>(null)
const selectedScoringQuestion = ref('')
const regenerationSubmitting = ref(false)
const activeRegenerationMode = ref<'batched' | null>(null)
const regenerationMessage = ref('')
const regenerationError = ref('')
const inlineRegenerationJobId = ref<number | null>(null)
const rubricInputValid = ref(true)
const saveFailureReason = ref('')
const scoreReviewRequiredQuestions = ref(new Set<string>())
const scoreReviewSatisfiedQuestions = ref(new Set<string>())
const templatePresent = ref(false)
const templateReady = ref(false)
let templateLoadGeneration = 0

const saving = computed(() => configStore.saveStatus === 'saving')
const saveUnknown = computed(() => configStore.saveStatus === 'unknown')
const submissionPending = computed(() => configStore.hasPendingSubmission)
const configJobActive = computed(() => {
  const current = configStore.jobId === null ? null : jobStore.jobs[configStore.jobId]
  return current?.job_type === 'config_generation'
    && current.payload.session_id === configStore.sessionId
    && !['succeeded', 'failed', 'cancelled'].includes(current.status)
})
const inlineRegenerationJob = computed(() => inlineRegenerationJobId.value === null
  ? null
  : jobStore.jobs[inlineRegenerationJobId.value] ?? null)
const regenerationBusy = computed(() => regenerationSubmitting.value
  || configJobActive.value || submissionPending.value)
const canRegenerateBatched = computed(() => configStore.canGenerate
  && blockingQualityQuestionIds.value.length > 0)
const templateReadinessTrigger = computed(() => {
  const current = configStore.jobId === null ? null : jobStore.jobs[configStore.jobId]
  if (current?.job_type !== 'config_generation'
    || current.payload.session_id !== configStore.sessionId) return ''
  return `${current.id}:${current.status}:${String(current.result.mapping_status ?? '')}`
})
const editorIssues = computed(() => [
  ...(configStore.editor?.issues ?? []),
  ...configStore.serverIssues,
])
const blockingIssues = computed(() => (configStore.editor?.issues ?? [])
  .some((issue) => issue.severity === 'error'))
const blockingQualityQuestionIds = computed(() => {
  const knownQuestionIds = [...scoringQuestions.value]
    .sort((left, right) => right.length - left.length || left.localeCompare(right))
  const result: string[] = []
  for (const issue of editorIssues.value) {
    if (issue.code !== 'quality_blocking') continue
    const message = issue.message.trim()
    for (const questionId of knownQuestionIds) {
      const prefix = `[质量检查-阻断] ${questionId}`
      if (message === prefix || message.startsWith(`${prefix} `)
        || message.startsWith(`${prefix}/`)) {
        if (!result.includes(questionId)) result.push(questionId)
        break
      }
    }
  }
  return result
})
const saveNeeded = computed(() => configStore.hasDirtyEditor)
const pendingScoreReviewQuestions = computed(() => new Set(
  [...scoreReviewRequiredQuestions.value]
    .filter((questionId) => scoringQuestions.value.includes(questionId)
      && !scoreReviewSatisfiedQuestions.value.has(questionId)),
))
const saveBlocked = computed(() => blockingIssues.value || !rubricInputValid.value
  || pendingScoreReviewQuestions.value.size > 0)
const solutionQuestionTypes = new Set(['proof', 'calculation', 'comprehensive'])
const scoringQuestions = computed(() => [...new Set(
  configStore.effectiveEditorRows
    .filter((row) => solutionQuestionTypes.has(row.question_type))
    .map((row) => row.question_id),
)])
const activeScoringQuestion = computed(() => {
  if (scoringQuestions.value.includes(selectedScoringQuestion.value)) return selectedScoringQuestion.value
  return scoringQuestions.value[0] ?? ''
})
const activeScoringRows = computed(() => configStore.effectiveEditorRows.filter(
  (row) => row.question_id === activeScoringQuestion.value,
))
watch(scoringQuestions, (questionIds) => {
  if (!questionIds.includes(selectedScoringQuestion.value)) {
    selectedScoringQuestion.value = questionIds[0] ?? ''
  }
}, { immediate: true })
const templateAction = computed(() => templateReady.value ? '查看已确认版本'
  : templatePresent.value ? '继续标定' : '准备样卷')
const templateSummary = computed(() => templateReady.value
  ? '题框已确认，可查看正式版本。'
  : templatePresent.value ? '样卷已上传，题框标定尚未确认。'
    : '尚未上传样卷，请准备双页 PDF 后开始标定。')
const activeStage = computed<StageId>(() => activePanel.value === 'editor'
  ? 'editor'
  : sourceStage.value)

watch(() => configStore.phase, (phase) => {
  if (requestedStage.value !== null) return
  if (phase === 'editor') {
    if (activePanel.value !== 'editor') transitionName.value = 'config-forward'
    activePanel.value = 'editor'
    return
  }
  sourceStage.value = phase
}, { immediate: true })

watch(
  () => configStore.editor?.configured === true,
  () => {
    const stage = requestedStage.value
    if (stage === null || !['draft', 'source', 'generation', 'editor'].includes(stage)) return
    if (stage === 'editor' && !configStore.editor?.configured) return
    requestedStage.value = null
    void selectStage(stage as Exclude<StageId, 'template'>)
  },
  { immediate: true },
)

async function selectStage(stage: StageId): Promise<void> {
  if (stage === 'template') {
    const sessionId = sessionStore.currentSession?.id
    if (sessionId !== undefined && configStore.editor?.configured) {
      window.location.assign(`/sessions/${sessionId}/regions`)
    }
    return
  }
  if (stage === 'editor') {
    if (!configStore.editor?.configured) return
    transitionName.value = 'config-forward'
    activePanel.value = 'editor'
    await nextTick()
    document.querySelector<HTMLElement>('#rubric-ledger-title')?.focus()
    return
  }
  transitionName.value = 'config-back'
  const panelChanging = activePanel.value !== 'source'
  sourceStage.value = stage
  pendingStageFocus.value = stage
  activePanel.value = 'source'
  if (!panelChanging) {
    await nextTick()
    focusPendingStage()
  }
}

function focusPendingStage(): void {
  const stage = pendingStageFocus.value
  if (stage === null) return
  pendingStageFocus.value = null
  const target = document.querySelector<HTMLElement>(
    stage === 'draft' ? '#session-draft-title'
      : stage === 'source' ? '#config-source-stage'
        : '#config-generation-stage',
  )
  target?.focus({ preventScroll: true })
  target?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
}

watch([
  () => sessionStore.currentSession?.id ?? null,
  () => configStore.editor?.revision ?? '',
  templateReadinessTrigger,
], async ([sessionId]) => {
  const generation = ++templateLoadGeneration
  templatePresent.value = false
  templateReady.value = false
  if (sessionId === null) return
  try {
    const readiness = await props.templateReadinessLoader(sessionId)
    if (generation !== templateLoadGeneration
      || sessionStore.currentSession?.id !== sessionId) return
    templatePresent.value = readiness.template_present
    templateReady.value = readiness.template_ready
  } catch { /* keep the conservative not-started state without exposing details */ }
}, { immediate: true })

function confirmSourceUpload(): boolean {
  if (!configStore.hasDirtyEditor) return true
  return window.confirm('替换试卷会在新文件接收成功后清除尚未保存的评分依据修改。是否继续？')
}

function sameValue(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right)
}

function editorReflectsRequest(
  response: ConfigEditorResponse,
  request: ConfigEditorSaveRequest,
): boolean {
  if (request.edits.length === 0 && request.commands.length === 0) {
    return true
  }
  if (response.revision === request.revision || request.commands.length > 0) return false
  const rows = new Map(response.rows.map((row) => [row.row_id, row]))
  return request.edits.every((edit) => {
    const row = rows.get(edit.row_id)
    if (!row) return false
    return Object.entries(edit).every(([field, value]) => field === 'row_id'
      || sameValue(row[field as keyof typeof row], value))
  })
}

async function saveEditor(): Promise<void> {
  if (configStore.sessionId === null || !saveNeeded.value || saveBlocked.value
    || configJobActive.value || !configStore.beginSave()) return
  const sessionId = configStore.sessionId
  const request = configStore.buildSaveRequest()
  const context = configStore.captureEditorContext()
  saveFailureReason.value = ''
  try {
    const response = await props.editorSaver(sessionId, request)
    if (!configStore.isEditorContextCurrent(context)) return
    confirmSavedScoreReviews(request)
    configStore.replaceWithAuthoritativeEditor(response)
  } catch (error) {
    if (!configStore.isEditorContextCurrent(context)) return
    saveFailureReason.value = error instanceof ApiError && error.status === 422
      ? '服务器未通过评分依据检查，请查看具体问题。'
      : error instanceof ApiError && error.status !== null && error.status >= 500
        ? '服务器保存评分依据时出错，请稍后重试；不是分值已自动恢复。'
        : '保存请求未完成，请检查连接后重试。'
    if (error instanceof ApiError
      && (error.code === 'config_revision_conflict' || error.status === 409)) {
      configStore.markConflict()
    } else if (isAmbiguousWriteError(error)) {
      try {
        const authoritative = await props.editorLoader(sessionId)
        if (!configStore.isEditorContextCurrent(context)) return
        if (editorReflectsRequest(authoritative, request)) {
          confirmSavedScoreReviews(request)
          configStore.replaceWithReconciledEditor(authoritative)
        } else if (authoritative.revision === request.revision) {
          configStore.noteSaveFailed()
        } else {
          configStore.markConflict()
        }
      } catch {
        if (configStore.isEditorContextCurrent(context)) configStore.noteSaveUnknown()
      }
    } else if (configStore.recordServerIssues(error)) {
      configStore.noteSaveFailed()
    } else {
      configStore.noteSaveFailed()
    }
  }
}

async function reloadLatestEditor(): Promise<void> {
  const sessionId = configStore.sessionId
  if (sessionId === null) return
  const context = configStore.captureEditorContext()
  try {
    const response = await props.editorLoader(sessionId)
    if (configStore.isEditorContextCurrent(context)) configStore.setEditor(response)
  } catch {
    if (configStore.isEditorContextCurrent(context)) configStore.noteSaveFailed()
  }
}

function queueCommand(command: ConfigEditorCommand): void {
  configStore.addEditorCommand(command)
  if (command.kind === 'replace_question_structure') {
    scoreReviewSatisfiedQuestions.value = new Set([
      ...scoreReviewSatisfiedQuestions.value,
      command.question_id,
    ])
  }
}

function scoreReviewStorageKey(sessionId: number): string {
  return `config-score-review-required:${sessionId}`
}

function replaceScoreReviewRequirements(next: Set<string>): void {
  scoreReviewRequiredQuestions.value = next
  const sessionId = sessionStore.currentSession?.id
  if (sessionId === undefined) return
  try {
    if (next.size > 0) {
      window.localStorage.setItem(scoreReviewStorageKey(sessionId), JSON.stringify([...next]))
    } else {
      window.localStorage.removeItem(scoreReviewStorageKey(sessionId))
    }
  } catch { /* storage availability must not block editing */ }
}

function loadScoreReviewRequirements(sessionId: number | null): void {
  scoreReviewSatisfiedQuestions.value = new Set()
  if (sessionId === null) {
    scoreReviewRequiredQuestions.value = new Set()
    return
  }
  try {
    const stored = JSON.parse(window.localStorage.getItem(scoreReviewStorageKey(sessionId)) ?? '[]')
    scoreReviewRequiredQuestions.value = new Set(
      Array.isArray(stored) ? stored.filter((value): value is string => typeof value === 'string') : [],
    )
  } catch {
    scoreReviewRequiredQuestions.value = new Set()
  }
}

function confirmSavedScoreReviews(request: ConfigEditorSaveRequest): void {
  const savedQuestionIds = new Set(request.commands
    .filter((command) => command.kind === 'replace_question_structure')
    .map((command) => command.question_id))
  if (savedQuestionIds.size === 0) return
  replaceScoreReviewRequirements(new Set(
    [...scoreReviewRequiredQuestions.value]
      .filter((questionId) => !savedQuestionIds.has(questionId)),
  ))
  scoreReviewSatisfiedQuestions.value = new Set(
    [...scoreReviewSatisfiedQuestions.value]
      .filter((questionId) => !savedQuestionIds.has(questionId)),
  )
}

let inlineRegenerationContext: {
  jobId: number
  generationContext: number
  sessionId: number
  previousScoreReviewRequirements: Set<string> | null
} | null = null
const loadedInlineRegenerationJobs = new Set<number>()

function attachInlineRegeneration(
  next: JobResponse,
  generationContext: number,
  sessionId: number,
  previousScoreReviewRequirements: Set<string> | null = null,
): void {
  jobStore.track(next)
  if (!configStore.attachJob(next.id, generationContext)) return
  inlineRegenerationJobId.value = next.id
  inlineRegenerationContext = {
    jobId: next.id,
    generationContext,
    sessionId,
    previousScoreReviewRequirements,
  }
  regenerationMessage.value = '被拦题目重新分析已经开始，当前正式评分依据继续保留。'
}

async function abandonMissingRegeneration(
  sessionId: number,
  requestToken: string,
): Promise<void> {
  try {
    await props.requestAbandoner(sessionId, requestToken)
    configStore.clearGenerationSubmissionPending()
    activeRegenerationMode.value = null
    regenerationError.value = '服务器确认未收到这次请求，可以再次提交。'
  } catch {
    regenerationError.value = '原请求可能仍在到达服务器，当前继续锁定。请稍后再试。'
  }
}

async function regenerateBlockedEditor(): Promise<void> {
  const targetedQuestionIds = blockingQualityQuestionIds.value
  if (!canRegenerateBatched.value) return
  await regenerateEditorQuestions(targetedQuestionIds, false)
}

async function regenerateSelectedScoringQuestion(questionId: string): Promise<void> {
  await regenerateEditorQuestions([questionId], true)
}

async function regenerateEditorQuestions(
  targetedQuestionIds: string[],
  requireManualScoreReview: boolean,
): Promise<void> {
  if (regenerationBusy.value || saving.value
    || configStore.sessionId === null) return
  if (targetedQuestionIds.length === 0) return
  if (configStore.hasDirtyEditor && !window.confirm(
    `评分依据还有未保存修改。${targetedQuestionIds.join('、')} 重新分析成功后会发布新版本，未保存修改不会保留。是否继续？`,
  )) return

  const sessionId = configStore.sessionId
  const generationContext = configStore.captureGenerationContext()
  const requestToken = createClientRequestToken()
  let request: ConfigGenerationRequest
  try {
    request = {
      ...configStore.sourceRequest('batched', false),
      client_request_token: requestToken,
    }
  } catch {
    regenerationError.value = '当前试卷来源信息不完整，无法安全地只重试这道题。请刷新页面后再试。'
    return
  }
  request.regenerate_question_ids = [...targetedQuestionIds]
  request.base_revision = configStore.editor?.revision
  request.sync_to_question_bank = false
  if (!configStore.markJobSubmissionPending(requestToken, 'generate', 'batched')) return
  const previousScoreReviewRequirements = new Set(scoreReviewRequiredQuestions.value)
  if (requireManualScoreReview) {
    replaceScoreReviewRequirements(new Set([
      ...scoreReviewRequiredQuestions.value,
      ...targetedQuestionIds,
    ]))
    scoreReviewSatisfiedQuestions.value = new Set(
      [...scoreReviewSatisfiedQuestions.value]
        .filter((questionId) => !targetedQuestionIds.includes(questionId)),
    )
  }
  activeRegenerationMode.value = 'batched'
  regenerationSubmitting.value = true
  regenerationMessage.value = `正在提交 ${targetedQuestionIds.join('、')} 的重新分析任务…`
  regenerationError.value = ''
  try {
    const next = await props.generationSubmitter(sessionId, request)
    attachInlineRegeneration(
      next,
      generationContext,
      sessionId,
      requireManualScoreReview ? previousScoreReviewRequirements : null,
    )
    if (requireManualScoreReview) {
      regenerationMessage.value = `${targetedQuestionIds.join('、')} 已开始单题重试；完成后请逐项重新赋分。`
    }
  } catch (error) {
    if (isAmbiguousWriteError(error)) {
      regenerationMessage.value = '请求结果未知，正在核对这一次任务…'
      try {
        const reconciled = await props.generationLoader(sessionId, requestToken)
        attachInlineRegeneration(
          reconciled,
          generationContext,
          sessionId,
          requireManualScoreReview ? previousScoreReviewRequirements : null,
        )
      } catch (reconciliationError) {
        regenerationMessage.value = ''
        if (isAuthoritativeNotFoundError(
          reconciliationError,
          'config_generation_job_not_found',
        )) {
          await abandonMissingRegeneration(sessionId, requestToken)
          if (requireManualScoreReview) {
            replaceScoreReviewRequirements(previousScoreReviewRequirements)
          }
        } else {
          regenerationError.value = '任务结果仍无法确认。为避免重复调用模型，当前保持锁定，请稍后再试。'
        }
      }
    } else {
      configStore.clearGenerationSubmissionPending()
      if (requireManualScoreReview) {
        replaceScoreReviewRequirements(previousScoreReviewRequirements)
      }
      activeRegenerationMode.value = null
      regenerationMessage.value = ''
      regenerationError.value = '重新分析没有开始，当前正式评分依据未改变，可以再次提交。'
    }
  } finally {
    regenerationSubmitting.value = false
  }
}

watch(
  () => {
    const current = inlineRegenerationJob.value
    if (current === null) return ''
    return `${current.id}:${current.status}:${String(current.result.outcome ?? '')}`
  },
  async () => {
    const current = inlineRegenerationJob.value
    const context = inlineRegenerationContext
    if (current === null || context === null || current.id !== context.jobId) return
    if (current.status === 'queued') {
      regenerationMessage.value = '重新生成正在等待开始，当前正式评分依据继续保留。'
      return
    }
    if (current.status === 'running' || current.status === 'paused') {
      regenerationMessage.value = '重新生成正在进行，当前正式评分依据继续保留。'
      return
    }
    if (current.status === 'failed') {
      if (context.previousScoreReviewRequirements !== null) {
        replaceScoreReviewRequirements(context.previousScoreReviewRequirements)
      }
      activeRegenerationMode.value = null
      regenerationMessage.value = ''
      regenerationError.value = '重新分析失败，当前正式评分依据仍然保留，可以再次提交。'
      return
    }
    if (current.status === 'cancelled') {
      if (context.previousScoreReviewRequirements !== null) {
        replaceScoreReviewRequirements(context.previousScoreReviewRequirements)
      }
      activeRegenerationMode.value = null
      regenerationMessage.value = '重新生成已取消，当前正式评分依据仍然保留。'
      return
    }
    if (current.status !== 'succeeded') return
    if (current.result.outcome !== 'complete') {
      if (context.previousScoreReviewRequirements !== null) {
        replaceScoreReviewRequirements(context.previousScoreReviewRequirements)
      }
      activeRegenerationMode.value = null
      regenerationMessage.value = '题目分析只完成了一部分，当前正式评分依据未替换；可以再次提交失败题。'
      return
    }
    if (loadedInlineRegenerationJobs.has(current.id)) return
    loadedInlineRegenerationJobs.add(current.id)
    regenerationMessage.value = '新评分依据已生成，正在更新当前编辑区…'
    try {
      const replaced = await configStore.reloadEditorForGeneration(
        context.generationContext,
        props.editorLoader,
      )
      if (replaced && configStore.sessionId === context.sessionId) {
        activeRegenerationMode.value = null
        regenerationMessage.value = '新评分依据已更新，请继续核对。'
        regenerationError.value = ''
      }
    } catch {
      activeRegenerationMode.value = null
      regenerationMessage.value = ''
      regenerationError.value = '新评分依据已生成，但暂时无法读取；当前正式版本仍保留，可以稍后重新加载。'
    }
  },
)

watch(
  () => sessionStore.currentSession?.id ?? null,
  (sessionId) => {
    loadScoreReviewRequirements(sessionId)
    inlineRegenerationJobId.value = null
    inlineRegenerationContext = null
    activeRegenerationMode.value = null
    regenerationMessage.value = ''
    regenerationError.value = ''
  },
  { immediate: true },
)
</script>

<template>
  <article class="session-config-view config-workspace">
    <PageHeader title="考试配置">
      <template
        v-if="sessionStore.loadState !== 'loading' && sessionStore.loadState !== 'error'"
        #actions
      >
        <ConfigStageRail
          :phase="configStore.phase"
          :active-stage="activeStage"
          :session-ready="sessionStore.currentSession !== null"
          :source-ready="configStore.sourceId !== null && configStore.sourceRevision !== null"
          :generation-submitted="configStore.jobId !== null"
          :editor-ready="configStore.editor?.configured === true"
          :template-present="templatePresent"
          :template-ready="templateReady"
          @select="selectStage"
        />
      </template>
    </PageHeader>

    <div v-if="sessionStore.loadState === 'loading'" class="session-config-view__state" role="status">
      正在读取考试列表…
    </div>
    <div v-else-if="sessionStore.loadState === 'error'" class="session-config-view__state" role="alert">
      <p>考试列表暂时无法读取，尚未改变任何考试。</p>
      <AppButton type="button" @click="sessionStore.initialize()">重新加载考试列表</AppButton>
    </div>
    <template v-else>
      <div id="config-intake-status-slot" />
      <p v-if="sessionStore.sessions.length === 0" class="session-config-view__empty">
        还没有考试，先创建草稿。
      </p>
      <Transition :name="transitionName" mode="out-in" @after-enter="focusPendingStage">
        <section v-if="activePanel === 'source'" key="source" class="config-workspace__panel" aria-label="试卷准备">
          <div id="config-source-stage" tabindex="-1">
            <div class="config-intake-bar">
              <SessionDraftPanel />
              <ConfigSourceUpload
                v-if="sessionStore.currentSession"
                :session-id="sessionStore.currentSession.id"
                :source="configStore.source"
                :before-upload="confirmSourceUpload"
                @uploaded="configStore.acceptUploadedSource"
              />
            </div>
            <template v-if="sessionStore.currentSession">
              <QuestionBlockReview
                v-if="configStore.source"
                :source="configStore.source"
                :decisions="configStore.decisions"
                :asset-decisions="configStore.assetDecisions"
                :question-states="configStore.questionStates"
                :duplicates="configStore.sourceDuplicates"
                :duplicates-unavailable="configStore.duplicatesUnavailable"
                @update:decisions="configStore.updateDecisions"
                @update:asset-decisions="configStore.updateAssetDecisions"
              />
              <div v-if="configStore.source" id="config-curriculum-volume-slot" />
            </template>
          </div>
          <div
            v-if="sessionStore.currentSession && (configStore.source || configStore.pendingJobRequestToken !== null || configStore.jobId !== null)"
            id="config-generation-stage"
            tabindex="-1"
          >
            <ConfigGenerationPanel
              :session-name="sessionStore.currentSession.name"
              @continue="selectStage('editor')"
            />
          </div>
        </section>
        <section
          v-else-if="sessionStore.currentSession && configStore.editor?.configured"
          key="editor"
          class="config-editor config-workspace__panel"
          aria-label="评分依据工作区"
        >
          <p
            v-if="regenerationMessage && !saveBlocked"
            class="rubric-ledger__regeneration-note"
            role="status"
          >
            {{ regenerationMessage }}
          </p>
          <RubricEditorTable
            :rows="configStore.effectiveEditorRows"
            :total-score="configStore.effectiveTotalScore"
            :issues="editorIssues"
            :disabled="saving || configJobActive || submissionPending"
            :show-regeneration-actions="saveBlocked"
            :can-regenerate-batched="canRegenerateBatched"
            :regeneration-busy="regenerationBusy"
            :regeneration-mode="activeRegenerationMode"
            :regeneration-submitting="regenerationSubmitting"
            :regeneration-question-ids="blockingQualityQuestionIds"
            :regeneration-message="regenerationMessage"
            :regeneration-error="regenerationError"
            @edit="configStore.updateEditor"
            @validity="rubricInputValid = $event"
            @regenerate="regenerateBlockedEditor"
          />

          <details v-if="scoringQuestions.length" class="config-editor__units">
            <summary>调整解答题小问与步骤点</summary>
            <label class="config-editor__question-picker">
              <span>选择题号</span>
              <select v-model="selectedScoringQuestion" aria-label="评分单元题号">
                <option v-for="questionId in scoringQuestions" :key="questionId" :value="questionId">
                  {{ questionId }}
                </option>
              </select>
            </label>
            <ScoringUnitEditor
              v-if="activeScoringQuestion"
              :question-id="activeScoringQuestion"
              :rows="activeScoringRows"
              :score-review-required="pendingScoreReviewQuestions.has(activeScoringQuestion)"
              :disabled="saving || configJobActive || submissionPending"
              @command="queueCommand"
              @retry="regenerateSelectedScoringQuestion"
            />
            <p class="config-editor__refine-note">AI 只重试选中一题，完成后需重新确认赋分。</p>
            <p v-if="regenerationError" class="config-editor__refine-error" role="alert">{{ regenerationError }}</p>
          </details>

          <div class="config-editor__save-bar">
            <span v-if="saveBlocked" class="config-editor__save-state is-blocked">
              需先处理阻断问题
            </span>
            <span v-else-if="saveNeeded" class="config-editor__save-state">
              有未保存修改
            </span>
            <span v-else-if="saving" class="config-editor__save-state">正在保存…</span>
            <div class="config-editor__save-actions">
              <a
                class="config-editor__next-link"
                :href="`/sessions/${sessionStore.currentSession.id}/regions`"
                :title="templateSummary"
              >样卷题框 · {{ templateAction }} →</a>
              <a
                v-if="templateReady"
                class="config-editor__next-link"
                :href="`/sessions/${sessionStore.currentSession.id}/grading-run`"
              >批改执行 →</a>
              <AppButton
                type="button"
                variant="primary"
                name="保存评分依据"
                class="config-editor__save-primary"
                :disabled="!saveNeeded || saveBlocked || saving || saveUnknown || configJobActive || submissionPending"
                @click="saveEditor"
              >{{ saving ? '正在保存…' : '保存评分依据' }}</AppButton>
            </div>
          </div>
          <ConfigSaveResult
            :status="configStore.saveStatus === 'saving' ? 'idle' : configStore.saveStatus"
            :mapping-status="configStore.mappingStatus"
            :failure-reason="saveFailureReason"
            :issues="configStore.serverIssues"
            @reload="reloadLatestEditor"
          />
        </section>
      </Transition>
    </template>
  </article>
</template>

<style scoped>
.session-config-view p { margin: 0; }
.session-config-view__state,
.session-config-view__empty { padding: var(--space-3) var(--space-6); border-block-end: var(--border-width) solid var(--border); color: var(--color-text-secondary); }
.session-config-view__state button { min-height: var(--control-height-default); margin-block-start: var(--space-3); }
.config-workspace__panel { min-width: 0; }
.config-forward-enter-active,
.config-forward-leave-active,
.config-back-enter-active,
.config-back-leave-active { transition: opacity 180ms ease, transform 220ms cubic-bezier(.2, .75, .25, 1); }
.config-forward-enter-from,
.config-back-leave-to { opacity: 0; transform: translateX(32px); }
.config-forward-leave-to,
.config-back-enter-from { opacity: 0; transform: translateX(-32px); }
@media (prefers-reduced-motion: reduce) {
  .config-forward-enter-active,
  .config-forward-leave-active,
  .config-back-enter-active,
  .config-back-leave-active { transition: none; }
}
</style>
