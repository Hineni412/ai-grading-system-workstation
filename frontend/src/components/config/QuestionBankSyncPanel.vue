<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { createClientRequestToken, type ConfigAmbiguousAssetDecision } from '../../api/config-workspace'
import { findLatestJob, type JobResponse } from '../../api/jobs'
import {
  retrySessionQuestionBankSync,
  getSessionQuestionBankAnalysisStatus,
  submitSessionQuestionBankSync,
  type SessionQuestionBankAnalysisStatus,
  type SessionQuestionBankSyncRequest,
} from '../../api/session-question-bank-sync'
import { useJobStore } from '../../stores/jobs'
import { useSessionStore } from '../../stores/session'
import { useCurriculumScopeStore } from '../../stores/curriculum-scope'
import {
  questionBankApi,
  type CurriculumCatalog,
  type CurriculumVolume,
} from '../../api/question-bank'
import { inferCurriculumVolumeId } from '../../domain/curriculum-volume-selection'

const props = withDefaults(defineProps<{
  sessionId: number
  sessionName: string
  configRevision: string
  autoStart?: boolean
  /**
   * Teacher-reviewed image bindings from the source review step. Forwarded to
   * the sync endpoints so the import follows them instead of re-guessing.
   */
  assetDecisions?: ConfigAmbiguousAssetDecision[]
  submitter?: (
    sessionId: number,
    request: SessionQuestionBankSyncRequest,
  ) => Promise<JobResponse>
  retryer?: (
    sessionId: number,
    jobId: number,
    request: SessionQuestionBankSyncRequest,
  ) => Promise<JobResponse>
  continuationSubmitter?: (
    questionIds: readonly number[],
    curriculumVolumeId: string,
    clientRequestToken: string,
  ) => Promise<JobResponse>
  curriculumLoader?: () => Promise<CurriculumCatalog>
  latestJobLoader?: (sessionId: number) => Promise<JobResponse | null>
  statusLoader?: (sessionId: number) => Promise<SessionQuestionBankAnalysisStatus>
}>(), {
  autoStart: false,
  assetDecisions: () => [],
  submitter: submitSessionQuestionBankSync,
  retryer: retrySessionQuestionBankSync,
  continuationSubmitter: (
    questionIds: readonly number[],
    curriculumVolumeId: string,
    clientRequestToken: string,
  ) => questionBankApi.submitTagging(
    questionIds,
    curriculumVolumeId,
    undefined,
    undefined,
    false,
    clientRequestToken,
  ),
  curriculumLoader: () => questionBankApi.getCurriculum(),
  latestJobLoader: (sessionId: number) => findLatestJob(
    sessionId,
    'question_bank_sync',
  ),
  statusLoader: getSessionQuestionBankAnalysisStatus,
})

const emit = defineEmits<{
  autoStartConsumed: []
}>()

const jobStore = useJobStore()
const sessionStore = useSessionStore()
const curriculumScope = useCurriculumScopeStore()
const activeJobId = ref<number | null>(null)
const submitting = ref(false)
const requestError = ref('')
const pendingToken = ref('')
const curriculum = ref<CurriculumCatalog | null>(null)
const selectedVolumeId = ref('')
const metadataPanelOpen = ref(false)
const curriculumLoading = ref(true)
const restoringJob = ref(true)
const deferredByTeacher = ref(false)
const liveStatus = ref<SessionQuestionBankAnalysisStatus | null>(null)
let autoAttempted = false

const latestTrackedJob = computed(() => Object.values(jobStore.jobs)
  .filter((item) => item.job_type === 'question_bank_sync'
    && item.payload.session_id === props.sessionId
    && item.payload.config_revision === props.configRevision)
  .sort((left, right) => right.id - left.id)[0] ?? null)
const job = computed(() => activeJobId.value === null
  ? latestTrackedJob.value
  : jobStore.jobs[activeJobId.value] ?? latestTrackedJob.value)
const progress = computed(() => Math.min(1, Math.max(0, job.value?.progress ?? 0)))
const outcome = computed(() => String(job.value?.result.outcome ?? ''))
const terminal = computed(() => job.value !== null
  && ['succeeded', 'failed', 'cancelled'].includes(job.value.status))
const canRetry = computed(() => terminal.value && (
  (liveStatus.value?.incomplete_question_ids.length ?? 0) > 0
  || (liveStatus.value === null && (
    job.value?.status === 'failed'
    || job.value?.result.retryable === true
    || safeCount(job.value?.result.failed_count) > 0
  ))
))
const statusCounts = computed(() => ({
  total: (liveStatus.value?.question_count
    ?? safeCount(job.value?.result.question_count))
    || safeCount(job.value?.result.imported_count),
  tagged: liveStatus.value?.tagged_count
    ?? safeCount(job.value?.result.complete_tagged_count),
  evidence: liveStatus.value?.evidence_count
    ?? safeCount(job.value?.result.evidence_count),
  criteria: liveStatus.value?.criteria_count
    ?? safeCount(job.value?.result.criteria_count),
  complete: liveStatus.value?.complete_count
    ?? Math.min(
      safeCount(job.value?.result.complete_tagged_count),
      safeCount(job.value?.result.criteria_count),
    ),
}))
const selectedVolume = computed<CurriculumVolume | null>(() =>
  curriculum.value?.volumes.find((item) => item.id === selectedVolumeId.value) ?? null)

onMounted(async () => {
  try {
    curriculum.value = await props.curriculumLoader()
    const savedVolumeId = sessionStore.sessions.find(
      item => item.id === props.sessionId,
    )?.curriculum_volume_id
    const preferredVolumeId = savedVolumeId || curriculumScope.selectedVolumeId
    selectedVolumeId.value = preferredVolumeId
      && curriculum.value.volumes.some(item => item.id === preferredVolumeId)
      ? preferredVolumeId
      : inferCurriculumVolumeId(props.sessionName, curriculum.value.volumes)
    metadataPanelOpen.value = selectedVolumeId.value === ''
  } catch {
    requestError.value = '本地教材目录暂时无法读取，当前不会启动入库或调用标签模型。'
  } finally {
    curriculumLoading.value = false
  }
  await restoreLatestJob()
  await attemptAutoStart(props.autoStart)
  void refreshLiveStatus()
  window.addEventListener('focus', refreshLiveStatus)
})

onBeforeUnmount(() => window.removeEventListener('focus', refreshLiveStatus))

async function refreshLiveStatus(): Promise<void> {
  try {
    const next = await props.statusLoader(props.sessionId)
    if (next.question_count > 0) liveStatus.value = next
  } catch {
    // Keep the saved job snapshot visible when the lightweight refresh fails.
  }
}

async function restoreLatestJob(): Promise<void> {
  if (latestTrackedJob.value !== null) {
    activeJobId.value = latestTrackedJob.value.id
    restoringJob.value = false
    return
  }
  try {
    const restored = await props.latestJobLoader(props.sessionId)
    if (
      restored !== null
      && restored.job_type === 'question_bank_sync'
      && restored.payload.session_id === props.sessionId
      && restored.payload.config_revision === props.configRevision
    ) {
      jobStore.track(restored)
      activeJobId.value = restored.id
    }
  } catch {
    requestError.value = '已保存的题库任务暂时无法读取；当前不会重复入库或调用模型。'
  } finally {
    restoringJob.value = false
  }
}

function safeCount(value: unknown): number {
  return Number.isSafeInteger(value) && Number(value) >= 0 ? Number(value) : 0
}

function reviewRefs(value: unknown): string {
  if (!Array.isArray(value)) return ''
  const refs = [...new Set(value
    .map((item) => String(item ?? '').trim())
    .filter((item) => item.length > 0))]
  if (refs.length === 0) return ''
  const visible = refs.slice(0, 8).join('、')
  return refs.length > 8 ? `${visible} 等 ${refs.length} 题` : visible
}

function statusCopy(current: JobResponse): string {
  if (current.status === 'queued') return '等待入库'
  if (current.status === 'running') {
    if (current.job_type === 'tagging_sync') return '正在补齐未完成题目的标签与训练判定点'
    if (current.stage === 'question_bank_tagging') return '正在调用 AI 打标签'
    if (current.stage === 'question_bank_import') return '正在拆分并写入题库'
    return '正在准备题库'
  }
  if (current.status === 'failed') return '题库流程异常结束'
  if (current.status === 'cancelled') return '题库流程已取消'
  if (
    liveStatus.value !== null
    && liveStatus.value.question_count > 0
    && liveStatus.value.complete_count === liveStatus.value.question_count
  ) return '试卷已入库，标签与训练判定点已保存'
  if (liveStatus.value !== null && liveStatus.value.incomplete_question_ids.length > 0) {
    return '已部分入库，仍有标签或训练判定点需要处理'
  }
  if (outcome.value === 'complete') {
    return safeCount(current.result.taxonomy_review_count)
      ? '试卷已入库，部分标签仍待归并'
      : '试卷已入库，标签与训练判定点已保存'
  }
  if (outcome.value === 'partial') return '已部分入库，仍有标签或训练判定点需要处理'
  return '题库流程未完成'
}

function failureCopy(current: JobResponse): string {
  if (current.status !== 'failed') return ''
  if (current.stage === 'question_bank_import') {
    if (current.error?.includes('local_source_file_missing')) {
      return '本地拆题入库时找不到来源文件或配套图片。尚未调用模型，因此模型记录中不会出现请求。'
    }
    if (current.error?.includes('local_storage_permission_denied')) {
      return '本地题库目录没有写入权限。尚未调用模型，因此模型记录中不会出现请求。'
    }
    return '失败发生在本地拆题入库阶段，尚未调用模型，因此模型记录中不会出现请求。'
  }
  if (current.stage === 'question_bank_tagging') {
    return '本地入库已完成，失败发生在 AI 标签阶段；可在设置中心的调用记录中继续核对。'
  }
  return '题库流程未完成；评分标准仍然保留。'
}

function assetDecisionPayload(): ConfigAmbiguousAssetDecision[] | undefined {
  return props.assetDecisions.length
    ? props.assetDecisions.map((item) => ({ ...item }))
    : undefined
}

async function start(): Promise<boolean> {
  if (submitting.value || job.value !== null) return false
  if (curriculumLoading.value) return false
  if (selectedVolume.value === null) {
    metadataPanelOpen.value = true
    deferredByTeacher.value = false
    requestError.value = '请先确认这份试卷对应的年级和上下册。'
    return false
  }
  submitting.value = true
  requestError.value = ''
  const token = pendingToken.value || createClientRequestToken()
  pendingToken.value = token
  try {
    const next = await props.submitter(props.sessionId, {
      config_revision: props.configRevision,
      client_request_token: token,
      curriculum_volume_id: selectedVolume.value.id,
      asset_decisions: assetDecisionPayload(),
    })
    jobStore.track(next)
    activeJobId.value = next.id
    pendingToken.value = ''
    return true
  } catch {
    requestError.value = '题库任务尚未确认提交。可再次点击核对；评分标准不受影响。'
    return false
  } finally {
    submitting.value = false
  }
}

async function retry(): Promise<void> {
  const current = job.value
  if (current === null || submitting.value) return
  submitting.value = true
  requestError.value = ''
  try {
    const incompleteIds = liveStatus.value?.incomplete_question_ids ?? []
    const volumeId = selectedVolume.value?.id
      || String(current.payload.curriculum_volume_id ?? '').trim()
    const next = incompleteIds.length > 0
      ? await submitCurrentIncomplete(incompleteIds, volumeId)
      : await props.retryer(props.sessionId, current.id, {
          config_revision: props.configRevision,
          client_request_token: createClientRequestToken(),
          curriculum_volume_id: selectedVolume.value?.id,
          asset_decisions: assetDecisionPayload(),
        })
    jobStore.track(next)
    activeJobId.value = next.id
  } catch {
    requestError.value = '题库重试没有开始。评分标准和已成功入库的题目都已保留。'
  } finally {
    submitting.value = false
  }
}

async function submitCurrentIncomplete(
  questionIds: readonly number[],
  volumeId: string,
): Promise<JobResponse> {
  if (!volumeId) {
    throw new Error('curriculum volume is required')
  }
  return props.continuationSubmitter(
    questionIds,
    volumeId,
    createClientRequestToken(),
  )
}

function deferIntake(): void {
  metadataPanelOpen.value = false
  deferredByTeacher.value = true
  requestError.value = ''
}

async function attemptAutoStart(enabled: boolean): Promise<void> {
  if (!enabled || autoAttempted || curriculumLoading.value) return
  autoAttempted = true
  if (job.value === null) await start()
  emit('autoStartConsumed')
}

watch(
  () => props.autoStart,
  (enabled) => attemptAutoStart(enabled),
  { immediate: true },
)

watch(
  () => Object.values(jobStore.jobs)
    .filter((item) => ['tagging_sync', 'question_bank_sync'].includes(item.job_type)
      && ['succeeded', 'failed', 'cancelled'].includes(item.status))
    .map((item) => `${item.id}:${item.status}:${item.updated_at}`)
    .sort()
    .join('|'),
  () => { void refreshLiveStatus() },
)
</script>

<template>
  <section class="question-bank-sync" aria-labelledby="question-bank-sync-title">
    <div class="question-bank-sync__heading">
      <div>
        <h2 id="question-bank-sync-title">试卷入库与标签治理</h2>
        <p>这是评分标准之后的独立流程；失败不会撤销评分标准，也不会重新生成评分标准。</p>
      </div>
      <button
        v-if="job === null && !restoringJob"
        type="button"
        :disabled="submitting"
        @click="start"
      >{{ submitting ? '正在提交…' : '将试卷入库并打标签' }}</button>
    </div>

    <p v-if="job === null && !restoringJob" class="question-bank-sync__cost">
      默认不自动执行。开始后会调用 AI 模型打标签，可能产生模型费用；新造标签会进入人工审核，不会直接污染正式词表。
    </p>

    <div v-if="job === null && !restoringJob && selectedVolume && !metadataPanelOpen && !deferredByTeacher" class="question-bank-sync__volume">
      <span>教材范围：<strong>{{ selectedVolume.label }}</strong> · 标签模型只会看到本册章节和小节</span>
      <button type="button" class="question-bank-sync__link" @click="metadataPanelOpen = true">
        修改
      </button>
    </div>

    <div v-if="job === null && !restoringJob && metadataPanelOpen" class="question-bank-sync__metadata" role="group" aria-labelledby="question-bank-volume-title">
      <div>
        <strong id="question-bank-volume-title">入库前确认教材册别</strong>
        <p>文件标题无法可靠判断时，请老师补选。确认后，AI 只能从该册章节和小节中选择标签。</p>
      </div>
      <label>
        年级与学期
        <select v-model="selectedVolumeId" :disabled="curriculumLoading">
          <option value="">请选择</option>
          <option v-for="volume in curriculum?.volumes ?? []" :key="volume.id" :value="volume.id">
            {{ volume.label }} · {{ volume.textbook_version }}
          </option>
        </select>
      </label>
      <div class="question-bank-sync__metadata-actions">
        <button type="button" class="question-bank-sync__secondary" @click="deferIntake">
          暂不入库
        </button>
        <button type="button" :disabled="selectedVolume === null || submitting" @click="start">
          确认并开始入库
        </button>
      </div>
    </div>
    <p v-else-if="job === null && !restoringJob && deferredByTeacher" class="question-bank-sync__deferred">
      已暂缓入库；评分标准不受影响。需要时可在这里重新确认教材册别。
      <button type="button" class="question-bank-sync__link" @click="metadataPanelOpen = true">现在确认</button>
    </p>

    <div v-if="job !== null" class="question-bank-sync__progress" aria-live="polite">
      <div>
        <strong>{{ statusCopy(job) }}</strong>
        <span>{{ Math.round(progress * 100) }}%</span>
      </div>
      <progress :value="progress" max="1" aria-label="试卷入库与标签治理进度" />
      <p v-if="terminal">
        题目入库成功 {{ safeCount(job.result.imported_count) }} 题 ·
        当前标签 {{ statusCounts.tagged }}/{{ statusCounts.total }} ·
        解题证据 {{ statusCounts.evidence }}/{{ statusCounts.total }} ·
        训练判定点 {{ statusCounts.criteria }}/{{ statusCounts.total }} ·
        联合分析完整 {{ statusCounts.complete }}/{{ statusCounts.total }}
        <template v-if="safeCount(liveStatus?.pending_taxonomy_count)">
          · 当前待审核新词 {{ safeCount(liveStatus?.pending_taxonomy_count) }} 个
        </template>
        <template v-if="reviewRefs(liveStatus?.incomplete_source_refs ?? job.result.taxonomy_review_source_refs)">
          · 待处理：{{ reviewRefs(liveStatus?.incomplete_source_refs ?? job.result.taxonomy_review_source_refs) }}
        </template>
      </p>
      <p
        v-if="job.status === 'failed'"
        class="question-bank-sync__error"
        role="alert"
      >
        {{ failureCopy(job) }}
      </p>
      <p
        v-if="terminal && job.result.restore_required === true"
        class="question-bank-sync__warning"
      >
        检测到旧版流程曾被已删除的同卷阻塞。现在可直接重试，系统会全新入库，
        不恢复旧题或旧标签；已经完成的分析不会重复调用 AI。
      </p>
      <button v-if="canRetry" type="button" :disabled="submitting" @click="retry">
        {{ submitting
          ? '正在提交…'
          : (liveStatus?.incomplete_question_ids.length ?? 0) > 0
            ? '继续完成未完成题目'
            : safeCount(job.result.taxonomy_review_count) && !safeCount(job.result.failed_count)
            ? '标签处理后重新本地校验'
            : '继续完成未完成题目' }}
      </button>
    </div>

    <p v-if="requestError" class="question-bank-sync__error" role="alert">
      {{ requestError }}
    </p>
  </section>
</template>

<style scoped>
.question-bank-sync {
  display: grid;
  gap: var(--space-2);
  margin-block-end: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border: var(--border-width) solid var(--border);
  border-inline-start: var(--border-selected-width) solid var(--color-accent);
  border-radius: var(--radius-control);
  background: var(--card);
}
.question-bank-sync__heading,
.question-bank-sync__progress > div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}
.question-bank-sync h2,
.question-bank-sync p { margin: 0; }
.question-bank-sync h2 { font-size: var(--font-size-h3); }
.question-bank-sync__heading p,
.question-bank-sync__cost,
.question-bank-sync__progress p {
  margin-block-start: var(--space-1);
  color: var(--color-text-secondary);
  font-size: var(--font-size-dense);
}
.question-bank-sync button {
  min-height: var(--control-height-default);
  padding-inline: var(--space-3);
  border: var(--border-width) solid var(--color-accent);
  border-radius: var(--radius-control);
  background: var(--color-accent);
  color: var(--primary-foreground);
  font-weight: var(--font-weight-medium);
}
.question-bank-sync button:hover:not(:disabled) { background: var(--color-accent-hover); }
.question-bank-sync button:disabled { cursor: not-allowed; opacity: var(--opacity-disabled); }
.question-bank-sync__volume,
.question-bank-sync__metadata-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}
.question-bank-sync__volume {
  padding: var(--space-2) var(--space-3);
  border: var(--border-width) solid var(--border);
  border-radius: var(--radius-control);
  background: var(--card);
  color: var(--color-text-secondary);
}
.question-bank-sync__metadata {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(220px, 0.6fr);
  gap: var(--space-3);
  align-items: end;
  padding: var(--space-3);
  border: var(--border-width) solid var(--color-accent);
  border-radius: var(--radius-control);
  background: var(--card);
}
.question-bank-sync__metadata label {
  display: grid;
  gap: var(--space-1);
  color: var(--color-text-secondary);
  font-size: var(--font-size-dense);
}
.question-bank-sync__metadata select {
  min-height: var(--control-height-default);
  padding-inline: var(--space-2);
  border: var(--border-width) solid var(--border);
  border-radius: var(--radius-control);
  background: var(--card);
  color: var(--color-text-primary);
}
.question-bank-sync__metadata-actions { grid-column: 1 / -1; justify-content: flex-end; }
.question-bank-sync button.question-bank-sync__secondary,
.question-bank-sync button.question-bank-sync__link {
  border-color: var(--border);
  background: var(--card);
  color: var(--color-text-primary);
}
.question-bank-sync button.question-bank-sync__secondary:hover:not(:disabled) { background: var(--secondary); }
.question-bank-sync button.question-bank-sync__link {
  min-height: auto;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--color-accent);
}
.question-bank-sync__deferred { color: var(--color-text-secondary); }
.question-bank-sync progress { width: 100%; }
.question-bank-sync__error {
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-control);
  background: var(--color-danger-subtle);
  color: var(--color-danger) !important;
}
@media (max-width: 720px) {
  .question-bank-sync__heading { align-items: stretch; flex-direction: column; }
  .question-bank-sync__metadata { grid-template-columns: 1fr; }
}
</style>
