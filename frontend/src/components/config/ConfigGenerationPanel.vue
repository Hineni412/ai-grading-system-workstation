<script setup lang="ts">
import { computed, ref, watch } from 'vue'

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
import type { JobResponse } from '../../api/jobs'
import { isAmbiguousWriteError, isAuthoritativeNotFoundError } from '../../api/errors'
import {
  useConfigWorkspaceStore,
  type ConfigGenerationSummary,
} from '../../stores/config-workspace'
import { useJobStore } from '../../stores/jobs'

const props = withDefaults(defineProps<{
  submitter?: (sessionId: number, request: ConfigGenerationRequest) => Promise<JobResponse>
  retryer?: (sessionId: number, jobId: number, questionIds: string[], requestToken: string) => Promise<JobResponse>
  editorLoader?: (sessionId: number) => Promise<ConfigEditorResponse>
  generationLoader?: (sessionId: number, requestToken: string) => Promise<JobResponse>
  requestAbandoner?: (sessionId: number, requestToken: string) => Promise<void>
}>(), {
  submitter: submitConfigGeneration,
  retryer: retryConfigGeneration,
  editorLoader: fetchConfigEditor,
  generationLoader: fetchConfigGenerationJobByToken,
  requestAbandoner: abandonConfigGenerationRequest,
})

const configStore = useConfigWorkspaceStore()
const jobStore = useJobStore()
const mode = ref<GenerationMode>('batched')
const submitting = ref(false)
const requestError = ref('')
const editorError = ref('')
const selectedFailed = ref<string[]>([])
const editorLoads = new Set<number>()
const submissionUnknown = computed(() => configStore.pendingJobRequestToken !== null)
const workspacePending = computed(() => configStore.hasPendingSubmission)

const job = computed(() => configStore.jobId === null ? null : jobStore.jobs[configStore.jobId] ?? null)
const syncError = computed(() => configStore.jobId === null
  ? null : jobStore.syncErrors[configStore.jobId] ?? null)
const progress = computed(() => Math.min(1, Math.max(0, job.value?.progress ?? 0)))
const outcome = computed(() => job.value?.result.outcome === 'partial'
  ? 'partial' : job.value?.result.outcome === 'complete' ? 'complete' : '')
interface FailedBatch {
  batch_id: string
  question_ids: string[]
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
    return ids.length > 0 ? [{ batch_id: value.batch_id, question_ids: ids }] : []
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
const generatedCount = computed(() => safeCount(job.value?.result.generated_questions))
const failedCount = computed(() => safeCount(job.value?.result.failed_count))
const active = computed(() => job.value !== null
  && !['succeeded', 'failed', 'cancelled'].includes(job.value.status))
const waitingForCancel = computed(() => job.value?.cancel_requested === true
  && (job.value.status === 'queued' || job.value.status === 'running'))
const refineJob = computed(() => job.value?.payload.mode === 'refine')
const safeDetail = computed(() => {
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

function statusCopy(value: JobResponse): string {
  if (value.cancel_requested && (value.status === 'queued' || value.status === 'running')) {
    return '已请求取消'
  }
  if (value.status === 'queued') return '等待开始'
  if (value.status === 'running') return '正在生成'
  if (value.status === 'paused') return '生成已暂停'
  if (value.status === 'cancelled') return '已取消'
  if (value.status === 'failed') return '生成失败'
  return outcome.value === 'partial' ? '部分完成' : '生成完成'
}

function returnToEditor(): void {
  const current = job.value
  if (!current || !refineJob.value) return
  configStore.detachJob(current.id)
  jobStore.remove(current.id)
  requestError.value = ''
  document.querySelector<HTMLElement>('#rubric-ledger-title')?.focus()
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

async function startGeneration(requestedMode: GenerationMode = mode.value): Promise<void> {
  if (!configStore.canGenerate || submitting.value || active.value || workspacePending.value
    || configStore.sessionId === null) return
  const context = configStore.captureGenerationContext()
  const sessionId = configStore.sessionId
  const requestToken = createClientRequestToken()
  const request = {
    ...configStore.sourceRequest(requestedMode),
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
  const retainedSummary: ConfigGenerationSummary = {
    totalQuestions: safeCount(current.result.total_questions),
    succeededQuestions: safeCount(current.result.generated_questions),
    failedQuestions: safeCount(current.result.failed_count),
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
    void reloadEditor(current)
  }
}, { immediate: true })
</script>

<template>
  <section class="config-generation" aria-labelledby="config-generation-title">
    <header class="config-section-heading">
      <div>
        <h2 id="config-generation-title">生成评分依据</h2>
        <p>系统每批处理 3 题；本地修复后仍失败的批次才需要手动重试。</p>
      </div>
    </header>

    <div class="config-generation__modes" role="note">
      <strong>小批次生成</strong>
      <small>按顺序每批最多 3 题，每批只请求一次；不会自动重试，也不会再调用 AI 统一赋分。</small>
    </div>

    <button
      v-if="job === null"
      type="button"
      name="开始生成"
      class="config-generation__primary"
      :disabled="!configStore.canGenerate || submitting || workspacePending"
      @click="startGeneration()"
    >{{ submitting ? '正在提交…' : '开始生成' }}</button>

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
      <p v-if="configStore.generationSummary" class="config-generation__retained">
        <strong>上一轮已成功 {{ configStore.generationSummary.succeededQuestions }} 题</strong>
        / 共 {{ configStore.generationSummary.totalQuestions }} 题；
        当前恢复任务：{{ statusCopy(job) }}。
      </p>

      <div v-if="['succeeded', 'failed', 'cancelled'].includes(job.status) && outcome === 'partial'" class="config-generation__partial">
        <p><strong>已成功 {{ generatedCount }} 题</strong>，失败 {{ failedCount }} 题。成功批次已保存，不会重复请求。</p>
        <fieldset>
          <legend>选择要重试的失败批次</legend>
          <label v-for="batch in failedBatches" :key="batch.batch_id">
            <input v-model="selectedFailed" type="checkbox" :value="batch.batch_id" :aria-label="`选择失败批次 ${batch.batch_id}`">
            {{ batch.batch_id }}（{{ batch.question_ids.join('、') }}）
          </label>
        </fieldset>
        <button type="button" name="重试所选批次" :disabled="submitting || workspacePending || selectedFailed.length === 0" @click="retrySelected()">
          重试所选批次
        </button>
      </div>

      <div v-if="['failed', 'cancelled'].includes(job.status) && outcome === 'complete'" class="config-generation__partial">
        <p>全部批次已经保存在本机，只差本地校验和发布；继续时不会调用模型。</p>
        <button type="button" name="完成本地发布" :disabled="submitting || workspacePending" @click="retrySelected(true)">
          完成本地发布
        </button>
      </div>

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
        name="重新分批生成"
        class="config-generation__primary"
        :disabled="submitting || workspacePending"
        @click="startGeneration('batched')"
      >重新分批生成</button>
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
.config-generation__modes { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-3); margin: 0 0 var(--space-3); padding: var(--space-4); border: var(--border-width) solid var(--color-border-default); background: var(--color-bg-subtle); }
.config-generation__failure-actions { display: flex; flex-wrap: wrap; gap: var(--space-3); }
.config-generation__modes legend { padding-inline: var(--space-1); font-weight: var(--font-weight-medium); }
.config-generation__modes label { display: flex; align-items: flex-start; gap: var(--space-2); min-width: 0; }
.config-generation__modes span { display: grid; gap: var(--space-1); }
.config-generation__modes small { color: var(--color-text-secondary); line-height: var(--line-height-relaxed); }
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
@media (max-width: 720px) { .config-generation__modes { grid-template-columns: minmax(0, 1fr); } }
</style>
