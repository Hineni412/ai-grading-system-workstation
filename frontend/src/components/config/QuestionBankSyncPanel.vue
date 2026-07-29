<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import { createClientRequestToken } from '../../api/config-workspace'
import type { JobResponse } from '../../api/jobs'
import {
  retrySessionQuestionBankSync,
  submitSessionQuestionBankSync,
  type SessionQuestionBankSyncRequest,
} from '../../api/session-question-bank-sync'
import { useJobStore } from '../../stores/jobs'
import {
  questionBankApi,
  type CurriculumCatalog,
  type CurriculumVolume,
} from '../../api/question-bank'

const props = withDefaults(defineProps<{
  sessionId: number
  sessionName: string
  configRevision: string
  autoStart?: boolean
  submitter?: (
    sessionId: number,
    request: SessionQuestionBankSyncRequest,
  ) => Promise<JobResponse>
  retryer?: (
    sessionId: number,
    jobId: number,
    request: SessionQuestionBankSyncRequest,
  ) => Promise<JobResponse>
  curriculumLoader?: () => Promise<CurriculumCatalog>
}>(), {
  autoStart: false,
  submitter: submitSessionQuestionBankSync,
  retryer: retrySessionQuestionBankSync,
  curriculumLoader: () => questionBankApi.getCurriculum(),
})

const emit = defineEmits<{
  autoStartConsumed: []
}>()

const jobStore = useJobStore()
const activeJobId = ref<number | null>(null)
const submitting = ref(false)
const requestError = ref('')
const pendingToken = ref('')
const curriculum = ref<CurriculumCatalog | null>(null)
const selectedVolumeId = ref('')
const metadataPanelOpen = ref(false)
const curriculumLoading = ref(true)
const deferredByTeacher = ref(false)
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
const canRetry = computed(() => terminal.value
  && (
    job.value?.status === 'failed'
    || job.value?.result.retryable === true
    || safeCount(job.value?.result.failed_count) > 0
  ))
const selectedVolume = computed<CurriculumVolume | null>(() =>
  curriculum.value?.volumes.find((item) => item.id === selectedVolumeId.value) ?? null)

function inferVolumeId(name: string, volumes: CurriculumVolume[]): string {
  const normalized = name.replace(/\s+/g, '')
  const grade = normalized.match(/([七八九])年级/)?.[1] ?? ''
  const semesters = [
    ...(normalized.includes('上册') || normalized.includes('上学期') ? ['上'] : []),
    ...(normalized.includes('下册') || normalized.includes('下学期') ? ['下'] : []),
  ]
  if (!grade || semesters.length !== 1) return ''
  const gradeLabel = `${grade}年级`
  const semesterMark = semesters[0] ?? ''
  return volumes.find((item) =>
    item.grade === gradeLabel
    && (item.semester.includes(semesterMark) || item.label.includes(semesterMark)))?.id ?? ''
}

onMounted(async () => {
  try {
    curriculum.value = await props.curriculumLoader()
    selectedVolumeId.value = inferVolumeId(
      props.sessionName,
      curriculum.value.volumes,
    )
    metadataPanelOpen.value = selectedVolumeId.value === ''
  } catch {
    requestError.value = '本地教材目录暂时无法读取，当前不会启动入库或调用标签模型。'
  } finally {
    curriculumLoading.value = false
  }
  await attemptAutoStart(props.autoStart)
})

function safeCount(value: unknown): number {
  return Number.isSafeInteger(value) && Number(value) >= 0 ? Number(value) : 0
}

function statusCopy(current: JobResponse): string {
  if (current.status === 'queued') return '等待入库'
  if (current.status === 'running') {
    if (current.stage === 'question_bank_tagging') return '正在调用 AI 打标签'
    if (current.stage === 'question_bank_import') return '正在拆分并写入题库'
    return '正在准备题库'
  }
  if (current.status === 'failed') return '题库流程异常结束'
  if (current.status === 'cancelled') return '题库流程已取消'
  if (outcome.value === 'complete') return '试卷已入库并完成标签治理'
  if (outcome.value === 'partial') return '已部分入库，仍有标签需要处理'
  return '题库流程未完成'
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
    const next = await props.retryer(props.sessionId, current.id, {
      config_revision: props.configRevision,
      client_request_token: createClientRequestToken(),
      curriculum_volume_id: selectedVolume.value?.id,
    })
    jobStore.track(next)
    activeJobId.value = next.id
  } catch {
    requestError.value = '题库重试没有开始。评分标准和已成功入库的题目都已保留。'
  } finally {
    submitting.value = false
  }
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
</script>

<template>
  <section class="question-bank-sync" aria-labelledby="question-bank-sync-title">
    <div class="question-bank-sync__heading">
      <div>
        <h2 id="question-bank-sync-title">试卷入库与标签治理</h2>
        <p>这是评分标准之后的独立流程；失败不会撤销评分标准，也不会重新生成评分标准。</p>
      </div>
      <button
        v-if="job === null"
        type="button"
        :disabled="submitting"
        @click="start"
      >{{ submitting ? '正在提交…' : '将试卷入库并打标签' }}</button>
    </div>

    <p v-if="job === null" class="question-bank-sync__cost">
      默认不自动执行。开始后会调用 AI 模型打标签，可能产生模型费用；新造标签会进入人工审核，不会直接污染正式词表。
    </p>

    <div v-if="job === null && selectedVolume && !metadataPanelOpen && !deferredByTeacher" class="question-bank-sync__volume">
      <span>教材范围：<strong>{{ selectedVolume.label }}</strong> · 标签模型只会看到本册章节和小节</span>
      <button type="button" class="question-bank-sync__link" @click="metadataPanelOpen = true">
        修改
      </button>
    </div>

    <div v-if="job === null && metadataPanelOpen" class="question-bank-sync__metadata" role="group" aria-labelledby="question-bank-volume-title">
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
    <p v-else-if="job === null && deferredByTeacher" class="question-bank-sync__deferred">
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
        入库 {{ safeCount(job.result.imported_count) }} 题 ·
        已标注 {{ safeCount(job.result.tagged_count) }} 题 ·
        已关联 {{ safeCount(job.result.linked_count) }} 题
        <template v-if="safeCount(job.result.review_count)">
          · 待人工审核 {{ safeCount(job.result.review_count) }} 个新标签
        </template>
      </p>
      <button v-if="canRetry" type="button" :disabled="submitting" @click="retry">
        {{ submitting ? '正在提交…' : '只重试未完成的题库流程' }}
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
  border: var(--border-width) solid var(--color-border-default);
  border-inline-start: var(--border-selected-width) solid var(--color-accent);
  background: var(--color-bg-subtle);
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
  color: var(--color-bg-surface);
  font-weight: var(--font-weight-medium);
}
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
  border: var(--border-width) solid var(--color-border-default);
  background: var(--color-bg-surface);
  color: var(--color-text-secondary);
}
.question-bank-sync__metadata {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(220px, 0.6fr);
  gap: var(--space-3);
  align-items: end;
  padding: var(--space-3);
  border: var(--border-width) solid var(--color-accent);
  background: var(--color-bg-surface);
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
  border: var(--border-width) solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
}
.question-bank-sync__metadata-actions { grid-column: 1 / -1; justify-content: flex-end; }
.question-bank-sync button.question-bank-sync__secondary,
.question-bank-sync button.question-bank-sync__link {
  border-color: var(--color-border-default);
  background: var(--color-bg-surface);
  color: var(--color-text-primary);
}
.question-bank-sync button.question-bank-sync__link {
  min-height: auto;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--color-accent);
}
.question-bank-sync__deferred { color: var(--color-text-secondary); }
.question-bank-sync progress { width: 100%; accent-color: var(--color-accent); }
.question-bank-sync__error {
  padding: var(--space-2) var(--space-3);
  background: var(--color-danger-subtle);
  color: var(--color-danger) !important;
}
@media (max-width: 720px) {
  .question-bank-sync__heading { align-items: stretch; flex-direction: column; }
  .question-bank-sync__metadata { grid-template-columns: 1fr; }
}
</style>
