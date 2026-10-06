<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import AppButton from '../design-system/AppButton.vue'
import AppDialog from '../design-system/AppDialog.vue'
import { questionBankApi, type QuestionRepairKind, type QuestionRepairPart, type QuestionRepairPreview } from '../../api/question-bank'
import { ApiError, isAmbiguousWriteError } from '../../api/errors'
import { useJobStore } from '../../stores/jobs'
import { useQuestionBankStore } from '../../stores/question-bank'
import { TERMINAL_JOB_STATUSES } from '../../api/jobs'

const props = defineProps<{ open: boolean; volumeId: string; volumeLabel: string; kind: QuestionRepairKind }>()
const emit = defineEmits<{ close: []; refreshed: [] }>()
const jobs = useJobStore()
const bank = useQuestionBankStore()
const preview = ref<QuestionRepairPreview | null>(null)
const state = ref<'loading' | 'ready' | 'error' | 'submitting' | 'ambiguous' | 'running'>('loading')
const revalidating = ref(false)
const refreshFailed = ref(false)
const message = ref('')
const activeJobId = ref<number | null>(null)
const job = computed(() => activeJobId.value ? jobs.jobs[activeJobId.value] : undefined)
const targetIds = computed(() => preview.value?.items.filter(item => !item.blocked_reason).slice(0, 500).map(item => item.id) ?? [])
const labels: Record<QuestionRepairPart, string> = { tags: '题目标签', evidence: '解题证据', criteria: '判定点', skills: '技能关联' }
const remaining = computed(() => (job.value?.result.remaining ?? []) as { id: number; question_number: string; missing: QuestionRepairPart[]; reason: string }[])
let controller: AbortController | undefined
let returnTarget: HTMLElement | null = null
let token = ''
const storageKey = computed(() => `question-bank-repair:${props.volumeId}:${props.kind}`)
async function load() {
  controller?.abort()
  const requestController = new AbortController()
  controller = requestController
  message.value = ''
  refreshFailed.value = false
  revalidating.value = false
  const cached = bank.repairPreviews.get(`${props.volumeId}:${props.kind}`) ?? null
  if (cached) {
    // 已有缓存先显示清单，后台核对最新状态，核对完成前不允许确认提交。
    preview.value = cached
    state.value = 'ready'
    revalidating.value = true
  } else {
    preview.value = null
    state.value = 'loading'
  }
  try {
    const value = await bank.loadRepairPreview(props.volumeId, props.kind, requestController.signal)
    if (requestController.signal.aborted) return
    preview.value = value; state.value = 'ready'
    revalidating.value = false
  } catch {
    if (requestController.signal.aborted) return
    revalidating.value = false
    if (preview.value) {
      refreshFailed.value = true
      message.value = '无法核对最新状态，请重试。'
    } else {
      state.value = 'error'
      message.value = '缺失清单暂时无法读取，请重试。'
    }
  }
}
watch(() => props.open, open => {
  if (!open) { controller?.abort(); return }
  returnTarget = document.activeElement instanceof HTMLElement ? document.activeElement : null
  const restored = Object.values(jobs.jobs).filter(j => j.job_type === 'question_bank_repair'
    && j.payload.curriculum_volume_id === props.volumeId && j.payload.kind === props.kind).sort((a, b) => b.id - a.id)[0]
  activeJobId.value = restored?.id ?? null
  const pending = localStorage.getItem(storageKey.value)
  if (pending) {
    try {
      const saved = JSON.parse(pending) as { preview: QuestionRepairPreview; token: string }
      if (!/^[0-9a-f]{32}$/.test(saved.token) || saved.preview.curriculum_volume_id !== props.volumeId || saved.preview.kind !== props.kind) throw new Error()
      preview.value = saved.preview; token = saved.token; state.value = 'ambiguous'
      message.value = '上次提交结果尚未确认；请找回原任务，避免重复请求。'
      return
    } catch { localStorage.removeItem(storageKey.value) }
  }
  if (restored && !TERMINAL_JOB_STATUSES.has(restored.status)) { state.value = 'running'; return }
  void load()
}, { immediate: true })
async function start() {
  if (!preview.value || !targetIds.value.length || state.value === 'submitting') return
  const original = preview.value
  if (state.value !== 'ambiguous') token = crypto.randomUUID().replace(/-/g, '')
  localStorage.setItem(storageKey.value, JSON.stringify({ preview: original, token }))
  state.value = 'submitting'; message.value = ''
  try {
    const result = await questionBankApi.submitRepair(original, targetIds.value, token)
    activeJobId.value = result.id; jobs.track(result); state.value = 'running'
    localStorage.removeItem(storageKey.value)
  } catch (error) {
    if (isAmbiguousWriteError(error)) { state.value = 'ambiguous'; message.value = '提交结果尚未确认。找回本次任务会沿用原请求编号。'; return }
    localStorage.removeItem(storageKey.value)
    state.value = 'error'
    message.value = error instanceof ApiError && error.status === 409 ? '缺失清单已变化，请重新统计后确认。' : '补齐任务未能提交，请重新统计后重试。'
  }
}
watch(() => job.value?.status, (status, previous) => {
  if (status && TERMINAL_JOB_STATUSES.has(status) && status !== previous) {
    bank.invalidateRepairPreviews(props.volumeId)
    emit('refreshed')
  }
})
function restoreFocus(event: Event) { event.preventDefault(); returnTarget?.focus({ preventScroll: true }) }
onBeforeUnmount(() => controller?.abort())
</script>

<template>
  <AppDialog
    :open="open"
    title="只补缺失部分"
    :description="`${volumeLabel} · ${kind === 'skills' ? '未挂技能题目' : kind === 'analysis' ? '分析未完成题目' : '有缺失的题目'}`"
    class="qb-repair-dialog"
    @update:open="(value: boolean) => { if (!value) emit('close') }"
    @close-auto-focus="restoreFocus"
  >
        <p v-if="state === 'loading'" role="status">正在统计题目和缺失部分…</p>
        <p v-if="message" role="alert">{{ message }}</p>
        <template v-if="state === 'running' && job">
          <p role="status">{{ job.status === 'queued' ? '等待开始' : job.status === 'running' ? '正在补齐缺失部分' : job.status === 'succeeded' ? `已补齐 ${job.result.completed_count ?? 0} / ${job.result.requested_count ?? 0} 题` : job.status === 'cancelled' ? '任务已取消，已完成的部分已保留' : '本次补齐未完成，已完成的部分已保留' }}</p>
          <progress :value="job.progress" max="1" />
          <p>{{ job.detail }}</p>
          <p v-if="jobs.syncErrors[job.id]" role="alert">进度暂时无法更新，任务可能仍在进行。<AppButton variant="ghost" size="small" @click="jobs.refresh(job.id)">更新进度</AppButton></p>
          <ul v-if="remaining.length" class="qb-repair-results"><li v-for="item in remaining" :key="item.id">第 {{ item.question_number }} 题：{{ item.missing.map(part => labels[part]).join('、') }} · {{ item.reason }}</li></ul>
          <AppButton v-if="TERMINAL_JOB_STATUSES.has(job.status)" variant="secondary" @click="activeJobId = null; load()">重新查看剩余缺失</AppButton>
        </template>
        <template v-else-if="preview">
          <p v-if="revalidating" class="qb-repair-check" role="status">正在核对最新状态…</p>
          <p class="qb-repair-summary">发现 <strong>{{ preview.question_count }}</strong> 道缺失题；本次可处理 <strong>{{ targetIds.length }}</strong> 道。</p>
          <p v-if="preview.repairable_count > 500">每次最多处理 500 道，剩余题目可在本次结束后继续补齐。</p>
          <div class="qb-repair-steps"><div v-for="part in (['tags', 'evidence', 'criteria', 'skills'] as const)" :key="part"><strong>{{ labels[part] }} · {{ preview.counts[part] }} 题缺失</strong><span>{{ part === 'tags' ? '只为缺失或过期标签的题补标签' : part === 'evidence' || part === 'criteria' ? '进入判定点分析流程，保留已有可用结果' : '已有判定点直接补关联；缺判定点先补齐再关联' }}</span></div></div>
          <details class="qb-repair-list" open><summary>查看逐题清单 · {{ preview.items.length }} 题</summary><table class="app-table app-table--sticky"><thead><tr><th>题目</th><th>缺失部分</th><th>本次处理</th></tr></thead><tbody><tr v-for="item in preview.items" :key="item.id"><td><strong>第 {{ item.question_number }} 题</strong><small>{{ item.paper_title }}</small></td><td>{{ item.missing.map(part => labels[part]).join('、') }}</td><td>{{ item.blocked_reason || (targetIds.includes(item.id) ? '只补左侧所列部分' : '下一批处理') }}</td></tr></tbody></table></details>
          <p class="qb-repair-cost">确认后将使用配置的 AI 模型，按服务商计费，金额取决于题目长度和模型。已有完整题不提交；失败或结果不确定时不自动追加请求。</p>
        </template>
        <AppButton v-if="state === 'error' || refreshFailed" variant="secondary" @click="load">重新统计</AppButton>
        <template v-if="state === 'submitting' || state === 'ambiguous' || state === 'ready'" #footer>
          <AppButton variant="secondary" @click="emit('close')">取消</AppButton>
          <AppButton variant="primary" :disabled="revalidating || refreshFailed || !targetIds.length || state === 'submitting'" @click="start">{{ state === 'ambiguous' ? '找回本次任务' : state === 'submitting' ? '正在提交…' : `确认补齐 ${targetIds.length} 题` }}</AppButton>
        </template>
  </AppDialog>
</template>
