<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import {
  abandonConfigGenerationRequest,
  createClientRequestToken,
  fetchConfigEditor,
  fetchConfigGenerationJobByToken,
  retryConfigGeneration,
  submitConfigGeneration,
  type ConfigEditorResponse,
  type ConfigGenerationRequest,
  type GenerationMode,
} from '../../api/config-workspace'
import { jobApi, type JobResponse } from '../../api/jobs'
import { isAmbiguousWriteError, isAuthoritativeNotFoundError } from '../../api/errors'
import {
  questionBankApi,
  type CurriculumCatalog,
  type CurriculumVolume,
} from '../../api/question-bank'
import { inferCurriculumVolumeId } from '../../domain/curriculum-volume-selection'
import {
  useConfigWorkspaceStore,
  type ConfigGenerationSummary,
} from '../../stores/config-workspace'
import { useJobStore } from '../../stores/jobs'

const props = withDefaults(defineProps<{
  sessionName?: string
  submitter?: (sessionId: number, request: ConfigGenerationRequest) => Promise<JobResponse>
  retryer?: (sessionId: number, jobId: number, questionIds: string[], requestToken: string) => Promise<JobResponse>
  editorLoader?: (sessionId: number) => Promise<ConfigEditorResponse>
  generationLoader?: (sessionId: number, requestToken: string) => Promise<JobResponse>
  requestAbandoner?: (sessionId: number, requestToken: string) => Promise<void>
  curriculumLoader?: () => Promise<CurriculumCatalog>
}>(), {
  sessionName: '',
  submitter: submitConfigGeneration,
  retryer: retryConfigGeneration,
  editorLoader: fetchConfigEditor,
  generationLoader: fetchConfigGenerationJobByToken,
  requestAbandoner: abandonConfigGenerationRequest,
  curriculumLoader: () => questionBankApi.getCurriculum(),
})
const emit = defineEmits<{
  continue: []
}>()

const configStore = useConfigWorkspaceStore()
const jobStore = useJobStore()
const submitting = ref(false)
const requestError = ref('')
const editorError = ref('')
const selectedFailed = ref<string[]>([])
const curriculum = ref<CurriculumCatalog | null>(null)
const selectedVolumeId = ref('')
const curriculumLoading = ref(true)
const curriculumError = ref('')
const volumeTeleportTarget = ref<HTMLElement | null>(null)
const editorLoads = new Set<number>()
const submissionUnknown = computed(() => configStore.pendingJobRequestToken !== null)
const workspacePending = computed(() => configStore.hasPendingSubmission)
const generationAvailable = computed(() => configStore.canGenerate)
const selectedVolume = computed<CurriculumVolume | null>(() =>
  curriculum.value?.volumes.find((item) => item.id === selectedVolumeId.value) ?? null)
const generationBlockReason = computed(() => {
  if (workspacePending.value) return '正在核对上一项请求，请稍候。'
  const source = configStore.source
  if (source === null || source.questions.length === 0) return '请先上传并完成试卷拆题。'
  const resolved = new Set(configStore.assetDecisions.map((item) => item.candidate_id))
  const uncertainIds = source.assets === undefined
    ? (source.ambiguous_assets ?? []).map((item) => item.candidate_id)
    : source.assets.filter((item) => item.assignment_state === 'uncertain')
      .map((item) => item.asset_id)
  const unresolved = uncertainIds.filter((item) => !resolved.has(item)).length
  if (unresolved > 0) return `还有 ${unresolved} 张黄色图片没有归属，请先拖到题目或答案区域。`
  if (curriculumLoading.value) return '正在读取教材目录。'
  if (selectedVolume.value === null) return '请先选择这份试卷对应的教材册别。'
  if (!generationAvailable.value) return '拆题结果尚未达到整卷生成条件。'
  return ''
})

onMounted(async () => {
  volumeTeleportTarget.value = document.querySelector<HTMLElement>('#config-curriculum-volume-slot')
  try {
    curriculum.value = await props.curriculumLoader()
    selectedVolumeId.value = inferCurriculumVolumeId(
      props.sessionName,
      curriculum.value.volumes,
    )
  } catch {
    curriculumError.value = '本地教材目录暂时无法读取，当前不会调用题目分析模型。'
  } finally {
    curriculumLoading.value = false
  }
})

const job = computed(() => configStore.jobId === null ? null : jobStore.jobs[configStore.jobId] ?? null)
const syncError = computed(() => configStore.jobId === null
  ? null : jobStore.syncErrors[configStore.jobId] ?? null)
const progress = computed(() => Math.min(1, Math.max(0, job.value?.progress ?? 0)))
const outcome = computed(() => job.value?.result.outcome === 'partial'
  ? 'partial' : job.value?.result.outcome === 'complete' ? 'complete' : '')
interface FailedBatch {
  batch_id: string
  question_ids: string[]
  category: string
  error: string
}
interface LocalJsonRepair {
  batch_id: string
  question_ids: string[]
}
const failedBatches = computed<FailedBatch[]>(() => {
  const raw = job.value?.result.failed_batches
  if (!Array.isArray(raw)) return []
  return raw.flatMap((item) => {
    if (typeof item !== 'object' || item === null || Array.isArray(item)) return []
    const value = item as Record<string, unknown>
    if (typeof value.batch_id !== 'string' || !Array.isArray(value.question_ids)) return []
    const ids = value.question_ids.filter((qid): qid is string => typeof qid === 'string' && qid.length > 0)
    return ids.length > 0 ? [{
      batch_id: value.batch_id,
      question_ids: ids,
      category: typeof value.category === 'string' ? value.category : '',
      error: typeof value.error === 'string' ? value.error : '',
    }] : []
  })
})
const localJsonRepairs = computed<LocalJsonRepair[]>(() => {
  const raw = job.value?.result.local_json_repairs
  if (!Array.isArray(raw)) return []
  return raw.flatMap((item) => {
    if (typeof item !== 'object' || item === null || Array.isArray(item)) return []
    const value = item as Record<string, unknown>
    if (typeof value.batch_id !== 'string' || !Array.isArray(value.question_ids)) return []
    const ids = value.question_ids.filter((qid): qid is string => typeof qid === 'string' && qid.length > 0)
    return [{ batch_id: value.batch_id, question_ids: ids }]
  })
})
const localStructureRepairs = computed<LocalJsonRepair[]>(() => {
  const raw = job.value?.result.local_structure_repairs
  if (!Array.isArray(raw)) return []
  return raw.flatMap((item) => {
    if (typeof item !== 'object' || item === null || Array.isArray(item)) return []
    const value = item as Record<string, unknown>
    if (typeof value.batch_id !== 'string' || !Array.isArray(value.question_ids)) return []
    const ids = value.question_ids.filter((qid): qid is string => typeof qid === 'string' && qid.length > 0)
    return [{ batch_id: value.batch_id, question_ids: ids }]
  })
})
const scoreAllocationPending = computed(
  () => job.value?.result.score_allocation_pending === true,
)
const scoreAllocationFailed = computed(
  () => job.value?.result.score_allocation_failed === true,
)
const scoreAllocationError = computed(() => {
  const value = job.value?.result.score_allocation_error
  return typeof value === 'string' ? value : ''
})
const scoreAllocationFailureCategory = computed(() => {
  const value = job.value?.result.score_allocation_failure_category
  return typeof value === 'string' ? value : ''
})
const retryable = computed(() => (
  job.value?.result.retryable === true
  || (
    job.value?.result.retryable !== false
    && failedBatches.value.length > 0
    && job.value?.payload.generation_mode !== 'whole_document'
  )
))
const questionBankSyncRequested = computed(
  () => job.value?.payload.sync_to_question_bank === true
    || job.value?.result.question_bank_sync_requested === true,
)
const questionBankSyncState = computed(() => {
  const value = job.value?.result.question_bank_sync_state
  return typeof value === 'string' ? value : ''
})
const questionBankSyncCopy = computed(() => {
  if (!questionBankSyncRequested.value) return ''
  if (questionBankSyncState.value === 'queued') {
    return '评分依据已发布，题库入库与 AI 打标签任务已经排队。'
  }
  if (questionBankSyncState.value === 'running') {
    return '评分依据已发布，正在执行题库入库与 AI 打标签。'
  }
  if (questionBankSyncState.value === 'submission_failed'
    || questionBankSyncState.value === 'blocked') {
    return '评分依据已发布，但题库任务没有启动；进入评分编辑页后可以重新提交，评分依据不受影响。'
  }
  if (outcome.value === 'partial') {
    return '已保存“完成后入库并打标签”的选择；当前等待评分依据完整生成，通过重试后会自动继续。'
  }
  return '已保存“完成后入库并打标签”的选择，评分依据发布成功后将由后台自动继续。'
})
const totalQuestionCount = computed(() => safeCount(job.value?.result.total_questions))
const totalBatchCount = computed(() => safeCount(job.value?.result.total_batch_count))
const generatedCount = computed(() => safeCount(job.value?.result.generated_questions))
const failedCount = computed(() => safeCount(job.value?.result.failed_count))
const includedSourceQuestionCount = computed(() => {
  const source = configStore.source
  if (source === null) return 0
  const excluded = new Set(
    configStore.decisions
      .filter((decision) => decision.excluded)
      .map((decision) => decision.question_id),
  )
  return source.questions.filter((question) => !excluded.has(question.question_id)).length
})
const retainedSummary = computed<ConfigGenerationSummary | null>(() => {
  const stored = configStore.generationSummary
  if (stored === null || totalQuestionCount.value > 0) return null
  const total = includedSourceQuestionCount.value || stored.totalQuestions
  const failed = Math.min(total, stored.failedQuestions)
  return {
    totalQuestions: total,
    succeededQuestions: Math.max(0, total - failed),
    failedQuestions: failed,
  }
})
const active = computed(() => job.value !== null
  && !['succeeded', 'failed', 'cancelled'].includes(job.value.status))
const terminal = computed(() => job.value !== null
  && ['succeeded', 'failed', 'cancelled'].includes(job.value.status))
const waitingForCancel = computed(() => job.value?.cancel_requested === true
  && (job.value.status === 'queued' || job.value.status === 'running'))
const refineJob = computed(() => job.value?.payload.mode === 'refine')
const publishedConfigPresent = computed(() => configStore.editor?.configured === true)
const sourceDiffersFromPublishedConfig = computed(() => {
  if (!publishedConfigPresent.value || configStore.source === null) return false
  return configStore.editor?.source?.sha256_prefix !== configStore.source.sha256_prefix
})
const safeDetail = computed(() => {
  if (terminal.value) return ''
  const detail = job.value?.detail.trim() ?? ''
  if (!detail || detail.length > 240 || /(?:[a-z]:[\\/]|\\\\|\/[^ ]+\/)/i.test(detail)) return ''
  return detail
})
const mappingNotice = computed(() => {
  if (job.value?.status !== 'succeeded' || outcome.value !== 'complete') return ''
  if (job.value.result.mapping_status === 'refreshed') {
    return '评分依据已生成，样卷映射已刷新。'
  }
  if (job.value.result.mapping_status === 'reconfirm_required') {
    return '评分依据已生成；样卷映射需要回到旧入口重新确认。'
  }
  if (job.value.result.mapping_status === 'not_present') {
    return '评分依据已生成；当前考试没有样卷映射。'
  }
  return ''
})

function safeCount(value: unknown): number {
  return Number.isSafeInteger(value) && Number(value) >= 0 ? Number(value) : 0
}

function failureCategoryCopy(category: string): string {
  if (category === 'model_transport') return '模型服务或网络请求失败'
  if (category === 'model_response_parse') return '模型已返回，但 JSON 无法解析'
  if (category === 'model_output_contract') return '模型已返回，但题目结构不符合约定'
  if (category === 'local_validation') return '模型已返回，但本地业务校验未通过'
  return '模型请求或结果处理失败'
}

function statusCopy(value: JobResponse): string {
  if (value.cancel_requested && (value.status === 'queued' || value.status === 'running')) {
    return '已请求取消'
  }
  if (value.status === 'queued') return '等待开始'
  if (value.status === 'running') return '正在生成'
  if (value.status === 'paused') return '生成已暂停'
  if (value.status === 'cancelled') return '已取消'
  if (value.status === 'failed') return '生成失败'
  return outcome.value === 'partial' ? '评分标准生成失败' : '评分标准生成成功'
}

function continueToEditor(): void {
  emit('continue')
}

async function restartAnalysis(): Promise<void> {
  prepareFreshGeneration()
  await startGeneration('batched')
}

function returnToEditor(): void {
  const current = job.value
  if (!current || !refineJob.value) return
  configStore.detachJob(current.id)
  jobStore.remove(current.id)
  requestError.value = ''
  document.querySelector<HTMLElement>('#rubric-ledger-title')?.focus()
}

function prepareFreshGeneration(): void {
  const current = job.value
  if (current === null || !terminal.value || workspacePending.value) return
  configStore.detachJob(current.id)
  requestError.value = ''
  editorError.value = ''
  selectedFailed.value = []
}

async function abandonMissingRequest(
  sessionId: number,
  requestToken: string,
  confirmedMessage: string,
): Promise<void> {
  try {
    await props.requestAbandoner(sessionId, requestToken)
    configStore.clearGenerationSubmissionPending()
    requestError.value = confirmedMessage
  } catch {
    requestError.value = '原请求可能仍在到达服务器，当前继续锁定。请稍后再次核对。'
  }
}

async function startGeneration(requestedMode: GenerationMode = 'batched'): Promise<void> {
  const ready = requestedMode === 'whole_document'
    ? configStore.canGenerateWholeDocument
    : configStore.canGenerate
  if (!ready || submitting.value || active.value || workspacePending.value
    || configStore.sessionId === null) return
  if (selectedVolume.value === null) {
    requestError.value = '请先选择这份试卷对应的年级和上下册；选择前不会调用模型。'
    return
  }
  if (configStore.hasDirtyEditor && !window.confirm(
    '评分依据还有未保存修改。新一轮完整生成成功后会用新结果替换当前正式版本，未保存修改不会保留。是否继续？',
  )) return
  const context = configStore.captureGenerationContext()
  const sessionId = configStore.sessionId
  const requestToken = createClientRequestToken()
  const request = {
    ...configStore.sourceRequest(requestedMode, true, selectedVolume.value.id),
    client_request_token: requestToken,
  }
  if (!configStore.markJobSubmissionPending(requestToken, 'generate', requestedMode)) return
  submitting.value = true
  requestError.value = ''
  try {
    const next = await props.submitter(sessionId, request)
    jobStore.track(next)
    configStore.attachJob(next.id, context)
  } catch (error) {
    if (isAmbiguousWriteError(error)) {
      requestError.value = '生成请求结果未知，正在核对任务记录…'
      try {
        const reconciled = await props.generationLoader(sessionId, requestToken)
        jobStore.track(reconciled)
        configStore.attachJob(reconciled.id, context)
        requestError.value = ''
      } catch (reconciliationError) {
        if (isAuthoritativeNotFoundError(reconciliationError, 'config_generation_job_not_found')) {
          await abandonMissingRequest(
            sessionId, requestToken, '服务器确认未收到这次请求，可以重新提交。',
          )
        } else {
          requestError.value = '生成请求结果未知，尚未找到可确认的任务。为避免重复生成，请稍后重新核对。'
        }
      }
    } else {
      configStore.clearGenerationSubmissionPending()
      requestError.value = '服务器已拒绝生成请求；当前试卷与核对结果已保留，可以修正后重试。'
    }
  } finally {
    submitting.value = false
  }
}

async function reconcileUnknownSubmission(): Promise<void> {
  const sessionId = configStore.sessionId
  const requestToken = configStore.pendingJobRequestToken
  if (sessionId === null || requestToken === null || submitting.value) return
  const context = configStore.captureGenerationContext()
  submitting.value = true
  requestError.value = '正在核对服务器任务记录…'
  try {
    const reconciled = await props.generationLoader(sessionId, requestToken)
    jobStore.track(reconciled)
    configStore.attachJob(reconciled.id, context, configStore.generationSummary ?? undefined)
    requestError.value = ''
  } catch (error) {
    if (isAuthoritativeNotFoundError(error, 'config_generation_job_not_found')) {
      await abandonMissingRequest(
        sessionId, requestToken, '服务器确认未收到这次请求，可以重新提交。',
      )
    } else {
      requestError.value = '仍未找到可确认的任务。为避免重复生成，当前保持锁定，请稍后再次核对。'
    }
  } finally {
    submitting.value = false
  }
}

async function retrySelected(resumeComplete = false): Promise<void> {
  const current = job.value
  if (current === null || submitting.value || workspacePending.value
    || configStore.sessionId === null) return
  const selectedBatches = failedBatches.value.filter((item) => selectedFailed.value.includes(item.batch_id))
  const ids = resumeComplete ? [] : [...new Set(selectedBatches.flatMap((item) => item.question_ids))]
  if (!resumeComplete && ids.length === 0) return
  const context = configStore.captureGenerationContext()
  const authoritativeTotal = includedSourceQuestionCount.value
    || safeCount(current.result.total_questions)
  const authoritativeFailed = Math.min(
    authoritativeTotal,
    safeCount(current.result.failed_count),
  )
  const retainedSummary: ConfigGenerationSummary = {
    totalQuestions: authoritativeTotal,
    succeededQuestions: Math.max(0, authoritativeTotal - authoritativeFailed),
    failedQuestions: authoritativeFailed,
  }
  const requestToken = createClientRequestToken()
  if (!configStore.markJobSubmissionPending(requestToken, 'retry', null, retainedSummary)) return
  submitting.value = true
  requestError.value = ''
  try {
    const next = await props.retryer(configStore.sessionId, current.id, ids, requestToken)
    jobStore.track(next)
    configStore.attachJob(next.id, context, retainedSummary)
  } catch (error) {
    if (isAmbiguousWriteError(error)) {
      requestError.value = '重试请求结果未知，正在核对这一次任务…'
      try {
        const reconciled = await props.generationLoader(configStore.sessionId, requestToken)
        jobStore.track(reconciled)
        configStore.attachJob(reconciled.id, context, retainedSummary)
        requestError.value = ''
      } catch (reconciliationError) {
        if (isAuthoritativeNotFoundError(reconciliationError, 'config_generation_job_not_found')) {
          await abandonMissingRequest(
            configStore.sessionId, requestToken,
            '服务器确认未收到这次请求，可以重新提交。',
          )
        } else {
          requestError.value = '重试请求结果仍无法确认。为避免重复生成，请稍后重新核对。'
        }
      }
    } else {
      configStore.clearGenerationSubmissionPending()
      requestError.value = '重试请求未提交成功，已有成功结果和失败题选择均已保留。'
    }
  } finally {
    submitting.value = false
  }
}

async function reloadEditor(current: JobResponse): Promise<void> {
  if (editorLoads.has(current.id)) return
  editorLoads.add(current.id)
  editorError.value = ''
  const context = configStore.captureGenerationContext()
  try {
    await configStore.reloadEditorForGeneration(context, props.editorLoader)
  } catch {
    editorLoads.delete(current.id)
    editorError.value = '评分依据已生成，但暂时无法读取。生成结果仍保留，可以重新读取。'
  }
}

watch(job, (current, previous) => {
  if (current?.id !== previous?.id) selectedFailed.value = []
  if (current?.status === 'succeeded' && current.result.outcome === 'complete') {
    const syncJobId = Number(current.result.question_bank_sync_job_id)
    if (Number.isSafeInteger(syncJobId) && syncJobId > 0) {
      void jobApi.getJob(syncJobId)
        .then((next) => jobStore.track(next))
        .catch(() => undefined)
    }
    void reloadEditor(current)
  }
}, { immediate: true })
</script>

<template>
  <section class="config-generation" aria-labelledby="config-generation-title">
    <header class="config-section-heading">
      <div>
        <h2 id="config-generation-title">生成评分依据</h2>
        <p>系统按题自动分析解题证据，完成后统一赋分；无需选择整卷或分批模式。</p>
      </div>
    </header>

    <p
      v-if="job === null && publishedConfigPresent"
      class="config-generation__candidate-note"
      role="status"
    >
      <strong>{{ sourceDiffersFromPublishedConfig ? '当前上传的是新的候选试卷。' : '当前正式评分依据仍然有效。' }}</strong>
      新一轮只有完整成功后才会替换正式版本；失败、取消或部分完成都保留旧版。
      替换成功后，样卷题框和扫描预检需要重新确认，历史批改结果不会删除。
    </p>

    <div v-if="job === null" class="config-generation__flow" role="note">
      <strong>一次完成题目分析与评分依据</strong>
      <span>先核对拆题结果；系统会按题从所选教材范围召回少量候选词，提取小问与踩分点证据，再对整卷统一赋分。失败时只重试失败题，不重做已完成题目。</span>
    </div>

    <Teleport :to="volumeTeleportTarget ?? 'body'" :disabled="volumeTeleportTarget === null">
      <div
        v-if="job === null"
        class="config-generation__metadata config-generation__metadata--volume"
        role="group"
        aria-labelledby="config-generation-volume-title"
      >
        <div>
          <strong id="config-generation-volume-title">选择教材册别</strong>
          <p>上传完成后先选教材；系统只召回与本题相关的精细词条，改变选项立即生效。</p>
        </div>
        <label>
          年级与学期
          <select v-model="selectedVolumeId" :disabled="curriculumLoading" @change="requestError = ''">
            <option value="">请选择</option>
            <option v-for="volume in curriculum?.volumes ?? []" :key="volume.id" :value="volume.id">
              {{ volume.label }} · {{ volume.textbook_version }}
            </option>
          </select>
        </label>
        <span v-if="selectedVolume" class="config-generation__volume-ready">已选择</span>
        <p v-if="curriculumError" class="config-generation__error" role="alert">
          {{ curriculumError }}
        </p>
      </div>
    </Teleport>

    <p
      v-if="job !== null && questionBankSyncCopy"
      class="config-generation__retained"
      role="status"
    >
      {{ questionBankSyncCopy }}
    </p>

    <template v-if="job === null">
      <button
        type="button"
        name="开始生成"
        class="config-generation__primary"
        :disabled="!generationAvailable || curriculumLoading || selectedVolume === null || submitting || workspacePending"
        @click="startGeneration()"
      >{{ submitting ? '正在提交…' : '分析试卷并生成评分依据' }}</button>
      <p
        v-if="generationBlockReason"
        class="config-generation__blocking-reason"
        role="status"
      >{{ generationBlockReason }}</p>
    </template>

    <div v-else class="config-generation__job" aria-live="polite">
      <div class="config-generation__status-line">
        <strong>{{ statusCopy(job) }}</strong>
        <span>{{ Math.round(progress * 100) }}%</span>
      </div>
      <progress :value="progress" max="1" aria-label="评分依据生成进度" />
      <p v-if="waitingForCancel">
        已收到取消请求，正在等待当前模型请求返回；服务器确认前任务仍未取消。
      </p>
      <p v-else-if="safeDetail">{{ safeDetail }}</p>
      <p v-if="mappingNotice" class="config-generation__mapping" role="status">{{ mappingNotice }}</p>
      <p v-if="localJsonRepairs.length > 0" class="config-generation__retained" role="status">
        本地程序已修复 {{ localJsonRepairs.length }} 个批次的 JSON（{{ localJsonRepairs.map((item) => item.batch_id).join('、') }}），未产生额外模型请求。
      </p>
      <p v-if="localStructureRepairs.length > 0" class="config-generation__retained" role="status">
        模型已返回；本地程序已统一 {{ localStructureRepairs.length }} 个批次的题号、小问号或步骤号（{{ localStructureRepairs.map((item) => item.batch_id).join('、') }}），无需重试，也未产生额外模型请求。
      </p>
      <p v-if="retainedSummary" class="config-generation__retained">
        <strong>上一轮已成功 {{ retainedSummary.succeededQuestions }} 题</strong>
        / 共 {{ retainedSummary.totalQuestions }} 题；
        当前恢复任务：{{ statusCopy(job) }}。
      </p>

      <p v-if="terminal && outcome === 'complete'" class="config-generation__success">
        <strong>评分标准生成成功。</strong>
        共 {{ totalQuestionCount || generatedCount }} 道题，已通过本地校验并完成分值配置。
      </p>
      <button
        v-if="terminal && outcome === 'complete' && !refineJob"
        type="button"
        name="进入评分标准编辑"
        class="config-generation__primary"
        @click="continueToEditor"
      >进入下一步：检查评分标准</button>

      <div v-if="['succeeded', 'failed', 'cancelled'].includes(job.status) && outcome === 'partial' && failedBatches.length > 0" class="config-generation__partial">
        <p>
          本次共 {{ totalQuestionCount }} 道题
          <template v-if="totalBatchCount">，分为 {{ totalBatchCount }} 个批次</template>；
          <strong>失败 {{ failedCount }} 道题</strong>，已成功 {{ generatedCount }} 道题。
          <template v-if="retryable">已通过批次保存在本机；失败题补齐后才会统一赋分。</template>
        </p>
        <fieldset v-if="retryable">
          <legend>选择要重试的失败批次</legend>
          <label v-for="batch in failedBatches" :key="batch.batch_id">
            <input v-model="selectedFailed" type="checkbox" :value="batch.batch_id" :aria-label="`选择失败批次 ${batch.batch_id}`">
            <span>
              {{ batch.batch_id }}（{{ batch.question_ids.join('、') }}）：
              {{ failureCategoryCopy(batch.category) }}
              <small v-if="batch.error">{{ batch.error }}</small>
            </span>
          </label>
        </fieldset>
        <div class="config-generation__failure-actions">
          <button v-if="retryable" type="button" name="重试所选批次" :disabled="submitting || workspacePending || selectedFailed.length === 0" @click="retrySelected()">
            重试所选失败题
          </button>
          <button type="button" name="重新分析全部题目" class="config-generation__secondary" :disabled="submitting || workspacePending" @click="restartAnalysis">
            重新分析全部题目
          </button>
        </div>
      </div>

      <div v-if="['succeeded', 'failed', 'cancelled'].includes(job.status) && outcome === 'partial' && failedBatches.length === 0 && scoreAllocationPending" class="config-generation__partial">
        <p>
          <strong>
            {{ totalQuestionCount || generatedCount }} 道题
            <template v-if="totalBatchCount">、共 {{ totalBatchCount }} 个批次</template>
            的生成结果已经保存在本机。
          </strong>
          <template v-if="scoreAllocationFailed">AI 统一配分没有成功；没有发布评分依据，也没有使用本地分数替代。</template>
          <template v-else>尚未完成 AI 统一配分。</template>
        </p>
        <p v-if="scoreAllocationError" class="config-generation__error" role="alert">
          <strong>{{ failureCategoryCopy(scoreAllocationFailureCategory) }}</strong>：
          {{ scoreAllocationError }}
        </p>
        <button type="button" name="重新进行 AI 统一配分" :disabled="submitting || workspacePending" @click="retrySelected(true)">
          重新进行 AI 统一配分
        </button>
      </div>

      <div v-if="['failed', 'cancelled'].includes(job.status) && outcome === 'complete'" class="config-generation__partial">
        <p>全部批次已经保存在本机，只差 AI 统一配分；继续时将调用模型一次，不会重新生成题目批次。</p>
        <button type="button" name="继续 AI 统一配分" :disabled="submitting || workspacePending" @click="retrySelected(true)">
          继续 AI 统一配分
        </button>
      </div>
      <p
        v-if="job.status === 'failed' && outcome !== 'partial' && outcome !== 'complete'"
        class="config-generation__error"
        role="alert"
      >
        <strong>评分标准生成失败。</strong>
        模型请求、返回格式或本地处理没有完成，因此没有发布任何新评分标准。
      </p>

      <button
        v-if="active && !job.cancel_requested"
        type="button"
        name="取消生成"
        class="config-generation__secondary"
        :disabled="workspacePending"
        @click="jobStore.cancel(job.id)"
      >取消生成</button>
      <div v-if="job.status === 'failed' && refineJob" class="config-generation__refine-actions">
        <button type="button" name="重新细化" class="config-generation__primary" :disabled="workspacePending" @click="returnToEditor">
          重新细化
        </button>
        <button type="button" name="返回编辑器" class="config-generation__secondary" :disabled="workspacePending" @click="returnToEditor">
          返回编辑器
        </button>
      </div>
      <button
        v-else-if="job.status === 'failed' && outcome !== 'partial' && outcome !== 'complete'"
        type="button"
        name="重新生成"
        class="config-generation__primary"
        :disabled="submitting || workspacePending"
        @click="startGeneration(job.payload.generation_mode === 'whole_document' ? 'whole_document' : 'batched')"
      >{{ job.payload.generation_mode === 'whole_document' ? '重新整卷生成' : '重新分批生成' }}</button>
    </div>

    <div v-if="syncError" class="config-generation__warning" role="alert">
      <span>任务状态暂时无法更新，已保留上次进度。</span>
      <button type="button" name="重新同步" @click="configStore.jobId !== null && jobStore.refresh(configStore.jobId)">重新同步</button>
    </div>
    <div v-if="editorError" class="config-generation__warning" role="alert">
      <span>{{ editorError }}</span>
      <button v-if="job" type="button" name="重新读取评分依据" @click="reloadEditor(job)">重新读取评分依据</button>
    </div>
    <div v-if="requestError || submissionUnknown" class="config-generation__error" role="alert">
      <span>{{ requestError || '有一次生成类请求的结果尚未确认。' }}</span>
      <button
        v-if="submissionUnknown"
        type="button"
        name="重新核对生成任务"
        :disabled="submitting"
        @click="reconcileUnknownSubmission"
      >重新核对</button>
    </div>
  </section>
</template>

<style scoped>
.config-generation { min-width: 0; margin-block-start: var(--space-6); border-block-start: var(--border-width) solid var(--color-border-default); }
.config-generation__flow { display: grid; gap: var(--space-1); margin: 0 0 var(--space-3); padding: var(--space-3) var(--space-4); border: var(--border-width) solid var(--color-border-default); border-inline-start: var(--border-selected-width) solid var(--color-accent); background: var(--color-bg-subtle); }
.config-generation__flow span { color: var(--color-text-secondary); line-height: var(--line-height-relaxed); }
.config-generation__blocking-reason { margin: var(--space-2) 0 0; color: #8a5a0a; font-size: var(--font-size-caption); }
.config-generation__volume,
.config-generation__metadata { margin: 0 0 var(--space-3); padding: var(--space-3) var(--space-4); border: var(--border-width) solid var(--color-border-default); background: var(--color-bg-surface); }
.config-generation__volume { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); color: var(--color-text-secondary); }
.config-generation__metadata { display: grid; grid-template-columns: minmax(0, 1fr) minmax(16rem, 0.65fr) auto; align-items: end; gap: var(--space-3); }
.config-generation__metadata--volume { margin-block: var(--space-4); }
.config-generation__volume-ready { align-self: end; padding-block: var(--space-2); color: var(--color-success); font-weight: var(--font-weight-medium); }
.config-generation__metadata p { margin: var(--space-1) 0 0; color: var(--color-text-secondary); }
.config-generation__metadata label { display: grid; gap: var(--space-1); }
.config-generation__metadata select { min-height: var(--control-height-default); padding-inline: var(--space-2); border: var(--border-width) solid var(--color-border-default); border-radius: var(--radius-control); background: var(--color-bg-surface); color: var(--color-text-primary); }
.config-generation__link { min-height: auto; padding: 0; border: 0; background: transparent; color: var(--color-accent); }
.config-generation__candidate-note { margin: 0 0 var(--space-3); padding: var(--space-3) var(--space-4); border-inline-start: var(--border-selected-width) solid var(--color-accent); background: var(--color-accent-subtle); color: var(--color-text-secondary); }
.config-generation__candidate-note strong { color: var(--color-text-primary); }
.config-generation__failure-actions { display: flex; flex-wrap: wrap; gap: var(--space-3); }
.config-generation__primary,
.config-generation__secondary,
.config-generation__partial button,
.config-generation__warning button { min-height: var(--control-height-default); padding-inline: var(--space-4); border: var(--border-width) solid var(--color-border-default); border-radius: var(--radius-control); background: var(--color-bg-surface); color: var(--color-text-primary); }
.config-generation__primary { border-color: var(--color-accent); background: var(--color-accent); color: var(--color-bg-surface); font-weight: var(--font-weight-medium); }
button:disabled { cursor: not-allowed; opacity: var(--opacity-disabled); }
.config-generation__job { padding: var(--space-4); border: var(--border-width) solid var(--color-border-default); border-inline-start: var(--border-selected-width) solid var(--color-accent); }
.config-generation__status-line { display: flex; justify-content: space-between; gap: var(--space-3); }
.config-generation__job progress { width: 100%; margin-block: var(--space-3); accent-color: var(--color-accent); }
.config-generation__job p { margin: 0 0 var(--space-3); color: var(--color-text-secondary); }
.config-generation__job .config-generation__retained { padding: var(--space-3); background: var(--color-success-subtle); color: var(--color-text-primary); }
.config-generation__partial { padding-block: var(--space-3); border-block-start: var(--border-width) solid var(--color-border-subtle); }
.config-generation__refine-actions { display: flex; flex-wrap: wrap; gap: var(--space-2); }
.config-generation__partial fieldset { display: flex; flex-wrap: wrap; gap: var(--space-3); margin: 0 0 var(--space-3); padding: var(--space-3); border: var(--border-width) solid var(--color-border-subtle); }
.config-generation__partial label { display: inline-flex; align-items: center; gap: var(--space-2); }
.config-generation__warning,
.config-generation__error { margin: var(--space-3) 0 0; padding: var(--space-3) var(--space-4); }
.config-generation__warning { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); background: var(--color-warning-subtle); }
.config-generation__error { background: var(--color-danger-subtle); color: var(--color-danger); }
@media (max-width: 820px) {
  .config-generation__metadata { grid-template-columns: 1fr; align-items: stretch; }
}
</style>
