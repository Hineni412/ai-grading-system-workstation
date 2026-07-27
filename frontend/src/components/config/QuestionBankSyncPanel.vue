<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { createClientRequestToken } from '../../api/config-workspace'
import type { JobResponse } from '../../api/jobs'
import {
  retrySessionQuestionBankSync,
  submitSessionQuestionBankSync,
  type SessionQuestionBankSyncRequest,
} from '../../api/session-question-bank-sync'
import { useJobStore } from '../../stores/jobs'

const props = withDefaults(defineProps<{
  sessionId: number
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
}>(), {
  autoStart: false,
  submitter: submitSessionQuestionBankSync,
  retryer: retrySessionQuestionBankSync,
})

const emit = defineEmits<{
  autoStartConsumed: []
}>()

const jobStore = useJobStore()
const activeJobId = ref<number | null>(null)
const submitting = ref(false)
const requestError = ref('')
const pendingToken = ref('')
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
  submitting.value = true
  requestError.value = ''
  const token = pendingToken.value || createClientRequestToken()
  pendingToken.value = token
  try {
    const next = await props.submitter(props.sessionId, {
      config_revision: props.configRevision,
      client_request_token: token,
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
    })
    jobStore.track(next)
    activeJobId.value = next.id
  } catch {
    requestError.value = '题库重试没有开始。评分标准和已成功入库的题目都已保留。'
  } finally {
    submitting.value = false
  }
}

watch(
  () => props.autoStart,
  async (enabled) => {
    if (!enabled || autoAttempted) return
    autoAttempted = true
    if (job.value !== null) {
      emit('autoStartConsumed')
      return
    }
    await start()
    emit('autoStartConsumed')
  },
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

    <div v-else class="question-bank-sync__progress" aria-live="polite">
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
.question-bank-sync progress { width: 100%; accent-color: var(--color-accent); }
.question-bank-sync__error {
  padding: var(--space-2) var(--space-3);
  background: var(--color-danger-subtle);
  color: var(--color-danger) !important;
}
@media (max-width: 720px) {
  .question-bank-sync__heading { align-items: stretch; flex-direction: column; }
}
</style>
