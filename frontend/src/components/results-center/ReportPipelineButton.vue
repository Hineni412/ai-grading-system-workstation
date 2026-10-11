<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { Sparkles } from '@lucide/vue'
import { classAnalysisApi } from '../../api/class-analysis'
import { TERMINAL_JOB_STATUSES, type JobResponse } from '../../api/jobs'
import { reportPipelineApi, type ReportPipelineStatus } from '../../api/report-pipeline'
import { useJobStore } from '../../stores/jobs'
import AppButton from '../design-system/AppButton.vue'
import AppDialog from '../design-system/AppDialog.vue'
import StatePanel from '../design-system/StatePanel.vue'
import { formatTokenCount } from '../file-center/report-format'
import { cachedPipelineStatus, invalidatePipelineStatus, requestPipelineStatus } from './pipeline-status-cache'

const props = defineProps<{
  sessionId: number
  activeJob: JobResponse | null
}>()

const jobs = useJobStore()
const open = ref(false)
const loading = ref(false)
const revalidating = ref(false)
const submitting = ref(false)
const status = ref<ReportPipelineStatus | null>(null)
const error = ref('')
const settingsSaving = ref(false)
const regenerateExisting = ref(false)
let controller: AbortController | null = null

const running = computed(() => (
  props.activeJob !== null && !TERMINAL_JOB_STATUSES.has(props.activeJob.status)
))
const pipelineActive = computed(() => (
  running.value || status.value?.active_job_id != null
))
const totalCalls = computed(() => (
  (status.value?.causes.call_count ?? 0)
  + (status.value?.personal_reports.call_count ?? 0)
))
const totalTokens = computed(() => (
  (status.value?.causes.estimated_tokens ?? 0)
  + (status.value?.personal_reports.estimated_tokens ?? 0)
))

const badgeCount = computed(() => {
  const value = status.value
  if (!value || !value.configured || value.complete) return 0
  return value.causes.call_count + value.personal_reports.call_count
})

// 预取：挂载、切换考试或整理任务进入终态后，先备好缓存，弹窗打开即显示。
async function prefetchStatus(sessionId: number): Promise<void> {
  try {
    const value = await requestPipelineStatus(sessionId)
    if (props.sessionId === sessionId && !regenerateExisting.value) status.value = value
  } catch { /* 预取失败不打扰用户，打开弹窗时再提示 */ }
}

watch(() => props.sessionId, (sessionId) => {
  controller?.abort()
  open.value = false
  regenerateExisting.value = false
  status.value = cachedPipelineStatus(sessionId)
  void prefetchStatus(sessionId)
}, { immediate: true })

watch(() => props.activeJob?.status, (value, previous) => {
  if (value && TERMINAL_JOB_STATUSES.has(value) && value !== previous) {
    invalidatePipelineStatus(props.sessionId)
    void prefetchStatus(props.sessionId)
  }
})

async function openDialog() {
  const sessionId = props.sessionId
  open.value = true
  regenerateExisting.value = false
  error.value = ''
  const cached = cachedPipelineStatus(props.sessionId) ?? status.value
  status.value = cached
  loading.value = !cached
  revalidating.value = !!cached
  controller?.abort()
  const next = new AbortController()
  controller = next
  try {
    const value = await requestPipelineStatus(sessionId)
    if (controller === next && props.sessionId === sessionId && !next.signal.aborted && !regenerateExisting.value) status.value = value
  } catch {
    if (controller === next && props.sessionId === sessionId && !next.signal.aborted) error.value = 'AI 整理状态暂时无法读取，请重试。'
  } finally {
    if (controller === next && !next.signal.aborted) {
      loading.value = false
      revalidating.value = false
    }
  }
}

function close() {
  if (submitting.value) return
  controller?.abort()
  controller = null
  if (regenerateExisting.value) status.value = cachedPipelineStatus(props.sessionId)
  regenerateExisting.value = false
  revalidating.value = false
  open.value = false
  error.value = ''
}

async function updateScope(event: Event) {
  const input = event.target
  if (!(input instanceof HTMLInputElement)) return
  const sessionId = props.sessionId
  regenerateExisting.value = input.checked
  error.value = ''
  revalidating.value = true
  controller?.abort()
  const next = new AbortController()
  controller = next
  try {
    const value = await reportPipelineApi.getStatus(sessionId, next.signal, regenerateExisting.value)
    if (controller === next && props.sessionId === sessionId && !next.signal.aborted) status.value = value
  } catch {
    if (controller === next && props.sessionId === sessionId && !next.signal.aborted) {
      status.value = null
      error.value = '更新范围的调用预估未能读取，请关闭后重试。'
    }
  } finally {
    if (controller === next) revalidating.value = false
  }
}

async function toggleAutoGenerate(event: Event) {
  const enabled = (event.target as HTMLInputElement).checked
  settingsSaving.value = true
  try {
    await classAnalysisApi.updateSettings(props.sessionId, enabled)
    if (status.value) status.value = { ...status.value, auto_generate: enabled }
  } catch {
    error.value = '自动整理开关未能保存，请重试。'
  } finally {
    settingsSaving.value = false
  }
}

async function confirm() {
  if (loading.value || revalidating.value || submitting.value || pipelineActive.value
    || status.value?.configured !== true || status.value.complete) return
  submitting.value = true
  error.value = ''
  try {
    const job = regenerateExisting.value
      ? await reportPipelineApi.start(props.sessionId, undefined, true)
      : await reportPipelineApi.start(props.sessionId)
    jobs.track(job)
    open.value = false
    status.value = null
  } catch {
    error.value = 'AI 整理未能提交，请稍后重试。'
  } finally {
    submitting.value = false
  }
}

onBeforeUnmount(() => controller?.abort())
</script>

<template>
  <AppButton
    variant="secondary"
    data-testid="report-pipeline-open"
    :title="badgeCount > 0 ? `预计调用模型 ${badgeCount} 次` : undefined"
    @click="openDialog"
  >
    <template #leading>
      <Sparkles :size="14" :stroke-width="2" aria-hidden="true" />
    </template>
    {{ running ? 'AI 整理中…' : 'AI 整理' }}{{ badgeCount > 0 ? ` · ${badgeCount}` : '' }}
  </AppButton>
  <AppDialog
    v-if="open"
    open
    title="AI 整理（错因与个人报告）"
    width="wide"
    :dismissible="!submitting"
    data-testid="report-pipeline-dialog"
    @update:open="close"
  >
    <form @submit.prevent="confirm">
      <StatePanel v-if="loading" kind="loading" title="正在读取整理状态…" />
      <p v-else-if="error && !status" class="file-center__warning" role="alert">{{ error }}</p>

      <template v-else-if="status">
        <p v-if="revalidating" role="status" data-testid="pipeline-revalidating">正在核对最新状态…</p>
        <p
          v-if="!status.configured"
          class="file-center__warning"
          role="alert"
          data-testid="pipeline-not-configured"
        >
          未配置内容生成模型，请前往 设置→模型配置 绑定后重试。
        </p>

        <label class="excel-settings-dialog__explanation" data-testid="pipeline-update-existing">
          <input type="checkbox" :checked="regenerateExisting" :disabled="revalidating || submitting || pipelineActive" @change="updateScope">
          更新本场已保存结果
        </label>
        <p class="excel-settings-dialog__explanation">
          {{ regenerateExisting ? '本次重新整理本场错因和个人报告。成功后替换原内容；失败保留上次成功内容。' : '本次只补尚未保存的内容，已有内容继续使用。' }}
        </p>
        <div class="excel-settings-preview" aria-label="整理内容概览">
          <div>
            <span>目标服务</span>
            <strong data-testid="pipeline-service">{{ status.service_name ?? '未配置' }}</strong>
          </div>
          <div>
            <span>模型</span>
            <strong data-testid="pipeline-model">{{ status.model_name ?? '未配置' }}</strong>
          </div>
          <div>
            <span>模型调用次数</span>
            <strong data-testid="pipeline-call-count">{{ totalCalls }} 次</strong>
          </div>
          <div>
            <span>文本 token 量（粗略估算）</span>
            <strong data-testid="pipeline-tokens">{{ formatTokenCount(totalTokens) }}</strong>
          </div>
        </div>

        <p class="excel-settings-dialog__explanation" data-testid="pipeline-breakdown">
          错因整理 {{ status.causes.pending_questions }} 题（约 {{ status.causes.call_count }} 次调用）；
          个人报告 {{ status.personal_reports.pending }} 人（其中 {{ status.personal_reports.cache_hits }}
          份复用已生成内容）。
        </p>
        <p
          v-if="status.review_pending > 0"
          class="excel-settings-dialog__explanation"
          data-testid="pipeline-review-pending"
        >
          还有 {{ status.review_pending }} 项待复核；建议先完成复核，再选择更新已保存结果。
        </p>
        <p
          v-if="status.complete"
          class="excel-settings-dialog__explanation"
          data-testid="pipeline-complete"
        >
          当前没有需要补做的部分。
        </p>
        <p
          v-if="pipelineActive"
          class="excel-settings-dialog__explanation"
          data-testid="pipeline-running"
        >
          正在整理中，完成后各页面自动更新。
        </p>
        <p class="excel-settings-dialog__explanation">
          将向上述服务发送题目资料和学生答卷图片，请使用支持图片的内容生成模型。
          图片用量另计，实际费用取决于服务商定价。AI 分析内容仅供参考，最终成绩保持教师确认结果。
        </p>
        <p v-if="error" class="file-center__warning" role="alert">{{ error }}</p>

        <label class="excel-settings-dialog__explanation" data-testid="pipeline-auto-generate">
          <input
            type="checkbox"
            :checked="status.auto_generate"
            :disabled="settingsSaving"
            @change="toggleAutoGenerate"
          >
          复核完成后自动整理（本场考试）
        </label>
      </template>

      <div class="excel-settings-dialog__actions">
        <span>确认后才会发起模型调用并产生费用。</span>
        <div>
          <AppButton variant="secondary" @click="close">取消</AppButton>
          <AppButton
            variant="primary"
            type="submit"
            data-testid="pipeline-confirm"
            :disabled="
              loading
              || revalidating
              || submitting
              || pipelineActive
              || status?.configured !== true
              || status?.complete === true
            "
          >
            确认整理
          </AppButton>
        </div>
      </div>
    </form>
  </AppDialog>
</template>
