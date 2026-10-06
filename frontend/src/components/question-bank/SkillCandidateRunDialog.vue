<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { DialogRoot, DialogPortal, DialogContent, DialogTitle, DialogDescription } from 'reka-ui'
import AppButton from '../design-system/AppButton.vue'
import {
  questionBankApi,
  type SkillCandidateRunInfo,
  type SkillGapPreview,
} from '../../api/question-bank'
import { ApiError, isAmbiguousWriteError } from '../../api/errors'
import { useJobStore } from '../../stores/jobs'
import { TERMINAL_JOB_STATUSES } from '../../api/jobs'

const props = defineProps<{ open: boolean; volumeId: string; activeRunId: string | null }>()
const emit = defineEmits<{ close: []; changed: []; review: [] }>()

const TERMINAL_RUN_STATUSES = new Set(['completed', 'partial', 'failed', 'cancelled', 'stale'])
const jobs = useJobStore()
const preview = ref<SkillGapPreview | null>(null)
const run = ref<SkillCandidateRunInfo | null>(null)
const suggestionCount = ref<number | null>(null)
const state = ref<'loading' | 'ready' | 'error' | 'submitting' | 'ambiguous' | 'running'>('loading')
const message = ref('')
const showItems = ref(false)
let controller: AbortController | undefined
let pollTimer: ReturnType<typeof setInterval> | undefined
let returnTarget: HTMLElement | null = null
let token = ''
let pendingFingerprint = ''
let retryToken = ''

const storageKey = computed(() => `question-bank-skill-candidates:${props.volumeId}`)
const trackedJob = computed(() => {
  if (!run.value) return undefined
  return Object.values(jobs.jobs)
    .filter((entry) => entry.job_type === 'skill_candidate' && entry.payload.run_id === run.value!.run_id)
    .sort((a, b) => b.id - a.id)[0]
})
const activeJob = computed(() => trackedJob.value && !TERMINAL_JOB_STATUSES.has(trackedJob.value.status) ? trackedJob.value : undefined)
const progress = computed(() => {
  const jobResult = trackedJob.value?.result?.progress as Record<string, number> | undefined
  if (jobResult && Number.isInteger(jobResult.total)) return { total: jobResult.total ?? 0, processed: jobResult.processed ?? 0, completed: jobResult.completed ?? 0, failed: jobResult.failed ?? 0 }
  const current = run.value?.progress
  return current ? { total: current.total, processed: current.processed, completed: current.completed, failed: current.failed } : { total: 0, processed: 0, completed: 0, failed: 0 }
})
const running = computed(() => !!run.value && !TERMINAL_RUN_STATUSES.has(run.value.status))
const failedBatches = computed(() => run.value?.batches.filter((batch) => batch.status === 'failed') ?? [])
const plannedCount = computed(() => preview.value ? Math.min(preview.value.counts.ready, preview.value.max_per_run) : 0)

function stopPoll() { if (pollTimer) { clearInterval(pollTimer); pollTimer = undefined } }
function savePending(payload: Record<string, unknown>) { localStorage.setItem(storageKey.value, JSON.stringify(payload)) }
function clearPending() { localStorage.removeItem(storageKey.value) }

async function loadPreview() {
  controller?.abort()
  const request = new AbortController()
  controller = request
  const wasAmbiguous = state.value === 'ambiguous'
  state.value = 'loading'; preview.value = null
  if (!wasAmbiguous) message.value = ''
  try {
    const value = await questionBankApi.skillGapPreview(props.volumeId, request.signal)
    if (request.signal.aborted) return
    preview.value = value
    state.value = wasAmbiguous ? 'ambiguous' : 'ready'
  } catch {
    if (!request.signal.aborted) { state.value = 'error'; message.value = '技能缺口暂时无法统计，请重试。' }
  }
}

async function loadRun(runId: string) {
  stopPoll()
  const wasAmbiguous = state.value === 'ambiguous'
  try {
    const value = await questionBankApi.skillCandidateRun(runId, controller?.signal)
    run.value = value
    if (!wasAmbiguous) state.value = 'running'
    if (TERMINAL_RUN_STATUSES.has(value.status)) void finish()
    else if (!activeJob.value) pollTimer = setInterval(() => void loadRun(runId), 2000)
  } catch {
    if (!wasAmbiguous) message.value = '整理任务进度暂时无法读取，请稍后重试。'
  }
}

async function finish() {
  stopPoll()
  try {
    const list = await questionBankApi.skillCandidates(props.volumeId)
    suggestionCount.value = list.suggestions.filter((item) => item.run_id === run.value?.run_id).length
  } catch { suggestionCount.value = null }
  emit('changed')
}

async function start() {
  if (!preview.value || state.value === 'submitting') return
  if (state.value !== 'ambiguous') {
    token = crypto.randomUUID().replace(/-/g, '')
    pendingFingerprint = preview.value.fingerprint
  }
  savePending({ fingerprint: pendingFingerprint, token })
  state.value = 'submitting'; message.value = ''
  try {
    const result = await questionBankApi.startSkillCandidateRun(props.volumeId, pendingFingerprint, token)
    clearPending()
    run.value = result.run
    jobs.track(result.job)
    state.value = 'running'
  } catch (error) {
    if (isAmbiguousWriteError(error)) {
      state.value = 'ambiguous'
      message.value = '提交结果尚未确认。重新提交会沿用原请求编号，不会重复扣费。'
      return
    }
    clearPending()
    if (error instanceof ApiError && error.status === 409) {
      if (error.code === 'skill_candidate_revision_conflict') {
        message.value = '技能缺口已变化，请重新统计后确认。'
        void loadPreview()
        return
      }
      if (error.code === 'skill_candidate_busy') {
        message.value = '已有整理任务在进行。'
        try {
          const summary = await questionBankApi.skillCandidateSummary(props.volumeId)
          if (summary.active_run) void loadRun(summary.active_run.run_id)
          else state.value = 'error'
        } catch { state.value = 'error' }
        return
      }
    }
    state.value = 'error'
    message.value = '整理任务未能提交，请重新统计后重试。'
  }
}

async function retryFailed() {
  if (!run.value || state.value === 'submitting') return
  if (state.value !== 'ambiguous') retryToken = crypto.randomUUID().replace(/-/g, '')
  savePending({ retry: run.value.run_id, token: retryToken })
  state.value = 'submitting'; message.value = ''
  try {
    const result = await questionBankApi.retrySkillCandidateRun(run.value.run_id, retryToken)
    clearPending()
    run.value = result.run
    suggestionCount.value = null
    jobs.track(result.job)
    state.value = 'running'
  } catch (error) {
    if (isAmbiguousWriteError(error)) {
      state.value = 'ambiguous'
      message.value = '重试提交结果尚未确认。重新提交会沿用原请求编号，不会重复扣费。'
      return
    }
    clearPending()
    state.value = 'running'
    message.value = '重试未能提交，请稍后再试。'
  }
}

async function cancelRun() {
  if (!run.value) return
  try { run.value = await questionBankApi.cancelSkillCandidateRun(run.value.run_id) }
  catch { message.value = '取消未能提交，请稍后再试。' }
}

watch(() => props.open, (open) => {
  if (!open) { controller?.abort(); stopPoll(); return }
  returnTarget = document.activeElement instanceof HTMLElement ? document.activeElement : null
  suggestionCount.value = null
  const pendingRaw = localStorage.getItem(storageKey.value)
  if (pendingRaw) {
    try {
      const saved = JSON.parse(pendingRaw) as { fingerprint?: string; token: string; retry?: string }
      if (!/^[0-9a-f]{32}$/.test(saved.token)) throw new Error()
      if (typeof saved.retry === 'string') {
        token = ''; pendingFingerprint = ''; retryToken = saved.token
        state.value = 'ambiguous'
        message.value = '上次重试提交结果尚未确认；重新提交会沿用原请求编号。'
        void loadRun(saved.retry)
        return
      }
      if (typeof saved.fingerprint === 'string') {
        token = saved.token; pendingFingerprint = saved.fingerprint
        state.value = 'ambiguous'
        message.value = '上次提交结果尚未确认；重新提交会沿用原请求编号，不会重复扣费。'
        void loadPreview()
        return
      }
      throw new Error()
    } catch { clearPending() }
  }
  if (props.activeRunId) { void loadRun(props.activeRunId); return }
  const restored = Object.values(jobs.jobs)
    .filter((entry) => entry.job_type === 'skill_candidate' && !TERMINAL_JOB_STATUSES.has(entry.status))
    .sort((a, b) => b.id - a.id)[0]
  const restoredRunId = typeof restored?.payload.run_id === 'string' ? restored.payload.run_id : ''
  if (restoredRunId) { void loadRun(restoredRunId); return }
  void loadPreview()
}, { immediate: true })

watch(() => trackedJob.value?.status, (status, previous) => {
  if (!status || status === previous || !TERMINAL_JOB_STATUSES.has(status)) return
  const runId = run.value?.run_id
  if (runId) void questionBankApi.skillCandidateRun(runId).then((value) => { run.value = value; void finish() }).catch(() => emit('changed'))
})
watch(running, (value) => { if (value && !activeJob.value && !pollTimer) pollTimer = setInterval(() => { const id = run.value?.run_id; if (id) void loadRun(id) }, 2000) })

function restoreFocus(event: Event) { event.preventDefault(); returnTarget?.focus({ preventScroll: true }) }
onBeforeUnmount(() => { controller?.abort(); stopPoll() })
</script>

<template>
  <DialogRoot :open="open" @update:open="!$event && emit('close')"><DialogPortal>
    <div v-if="open" class="qb-modal-layer" @click.self="emit('close')">
      <DialogContent class="qb-skill-run-dialog qb-import-dialog" @close-auto-focus="restoreFocus">
        <header>
          <DialogTitle as="h2">整理成技能候选</DialogTitle>
          <button class="qb-link" aria-label="关闭整理面板" @click="emit('close')">关闭</button>
        </header>
        <DialogDescription>把挂不上技能的判定点整理成候选，供你逐一审核。</DialogDescription>
        <p v-if="state === 'loading'" role="status">正在统计技能缺口，此步骤不调用模型…</p>
        <p v-if="message" role="alert" class="qb-feedback is-error">{{ message }}</p>

        <template v-if="state === 'ambiguous' && run && !preview">
          <footer>
            <AppButton variant="secondary" @click="emit('close')">关闭</AppButton>
            <AppButton variant="primary" @click="retryFailed">重新提交</AppButton>
          </footer>
        </template>
        <template v-else-if="state === 'running' && run">
          <p role="status">
            <template v-if="running">正在整理，已处理 {{ progress.processed }} / {{ progress.total }} 次请求</template>
            <template v-else-if="run.status === 'cancelled'">任务已取消，已完成的章节保留结果。</template>
            <template v-else-if="suggestionCount !== null">整理完成，已生成 {{ suggestionCount }} 条建议<template v-if="failedBatches.length">，{{ failedBatches.length }} 个章节失败</template>。</template>
            <template v-else>整理结束<template v-if="failedBatches.length">，{{ failedBatches.length }} 个章节失败</template>。</template>
          </p>
          <progress v-if="running" :value="progress.processed" :max="Math.max(progress.total, 1)" />
          <p v-if="run.stale" class="qb-feedback is-warning">标准已更新，本次结果仅供查看。</p>
          <ul v-if="failedBatches.length" class="qb-skill-run-failed">
            <li v-for="batch in failedBatches" :key="batch.batch_id">第 {{ batch.chapter_key }} 章：{{ batch.error?.message || '整理未完成' }}</li>
          </ul>
          <footer>
            <AppButton v-if="running" variant="secondary" @click="cancelRun">取消整理</AppButton>
            <template v-else>
              <AppButton v-if="failedBatches.length && run.retryable" variant="secondary"
                @click="retryFailed">重试失败的部分</AppButton>
              <p v-if="failedBatches.length && run.retryable" class="qb-skill-run-cost">
                将重新发送 {{ failedBatches.length }} 次请求，会产生费用。
              </p>
              <AppButton variant="primary" @click="emit('review')">去审核</AppButton>
            </template>
          </footer>
        </template>

        <template v-else-if="preview && (state === 'ready' || state === 'ambiguous' || state === 'submitting')">
          <p class="qb-skill-run-summary">
            本次将整理 <strong>{{ plannedCount }}</strong> 个判定点，按章分成
            <strong>{{ preview.planned_requests }}</strong> 次 AI 请求。
          </p>
          <p v-if="preview.counts.ready > preview.max_per_run">
            每次最多整理 {{ preview.max_per_run }} 个，剩余的可在本次结束后继续。
          </p>
          <ul class="qb-skill-run-chapters">
            <li v-for="batch in preview.batches" :key="batch.chapter_key">
              {{ batch.chapter_label }} · {{ batch.gap_count }} 个判定点
            </li>
          </ul>
          <details class="qb-skill-run-items">
            <summary @click="showItems = !showItems">查看逐条判定点 · {{ preview.items.length }} 条</summary>
            <ul>
              <li v-for="item in preview.items" :key="item.gap_key">
                {{ item.paper_title }} 第{{ item.question_number }}题 · {{ item.target }}
              </li>
            </ul>
          </details>
          <p class="qb-skill-run-cost">
            确认后将使用题库打标所用的 AI 模型发送 {{ preview.planned_requests }} 次请求，会产生费用。每次请求只发送一次，失败不会自动重发。只发送题目和判定点文字，不发送学生信息。
          </p>
          <footer>
            <AppButton variant="secondary" @click="emit('close')">取消</AppButton>
            <AppButton variant="primary" :disabled="state === 'submitting' || !preview.counts.ready" @click="start">
              {{ state === 'ambiguous' ? '重新提交' : state === 'submitting' ? '正在提交…' : '确认并开始整理' }}
            </AppButton>
          </footer>
        </template>
        <AppButton v-if="state === 'error'" variant="secondary" @click="loadPreview">重新统计</AppButton>
      </DialogContent>
    </div>
  </DialogPortal></DialogRoot>
</template>
