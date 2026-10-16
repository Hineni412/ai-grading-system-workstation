<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import AppButton from '../design-system/AppButton.vue'
import AppDialog from '../design-system/AppDialog.vue'
import { questionBankApi, type QuestionRepairKind, type QuestionRepairPart, type QuestionRepairPreview } from '../../api/question-bank'
import { ApiError, isAmbiguousWriteError } from '../../api/errors'
import { useJobStore } from '../../stores/jobs'
import { useQuestionBankStore } from '../../stores/question-bank'
import { TERMINAL_JOB_STATUSES } from '../../api/jobs'
import { repairPartLabels as labels, repairStage } from './repair-workflow'

const props = defineProps<{ open: boolean; volumeId: string; volumeLabel: string; kind: QuestionRepairKind }>()
const emit = defineEmits<{ close: []; refreshed: []; openQuestion: [ref: { questionId: number; paperId: number | null }] }>()
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
const organizeCount = computed(() => preview.value?.items.filter(item => repairStage(item) === 'organize').length ?? 0)
const reviewCount = computed(() => preview.value?.items.filter(item => repairStage(item) === 'review').length ?? 0)
const repairSteps = computed(() => (['tags', 'evidence', 'criteria', 'types', 'knowledge_points', 'skills'] as const)
  .map(part => ({ part, count: preview.value?.items.filter(item => targetIds.value.includes(item.id)
    && item.missing.includes(part) && (part !== 'types' || !item.type_pending_reason)).length ?? 0 }))
  .filter(step => step.count > 0))
const remaining = computed(() => (job.value?.result.remaining ?? []) as { id: number; question_number: string; missing: QuestionRepairPart[]; reason: string }[])
let controller: AbortController | undefined
let returnTarget: HTMLElement | null = null
let token = ''
const storageKey = computed(() => `question-bank-repair:${props.volumeId}:${props.kind}`)
function cachedPreview(): QuestionRepairPreview | null {
  const exact = bank.repairPreviews.get(`${props.volumeId}:${props.kind}`)
  if (exact) return exact
  const all = bank.repairPreviews.get(`${props.volumeId}:all`)
  if (props.kind !== 'analysis' || !all) return null
  const items = all.items.filter(item => item.missing.some(part =>
    part === 'tags' || part === 'evidence' || part === 'criteria'))
  const counts: QuestionRepairPreview['counts'] = {
    tags: 0, evidence: 0, criteria: 0, skills: 0, types: 0, knowledge_points: 0,
  }
  for (const item of items) for (const part of item.missing) counts[part] += 1
  return {
    ...all,
    kind: 'analysis',
    fingerprint: '',
    items,
    counts,
    question_count: items.length,
    repairable_count: items.filter(item => !item.blocked_reason).length,
  }
}

async function load() {
  controller?.abort()
  const requestController = new AbortController()
  controller = requestController
  message.value = ''
  refreshFailed.value = false
  revalidating.value = false
  const cached = cachedPreview()
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
watch(() => bank.repairPreviews.get(`${props.volumeId}:all`), () => {
  if (!props.open || state.value !== 'loading') return
  const cached = cachedPreview()
  if (!cached) return
  preview.value = cached
  state.value = 'ready'
  revalidating.value = true
})
watch(() => props.open, (open, previous) => {
  if (!open) {
    if (previous && job.value && !TERMINAL_JOB_STATUSES.has(job.value.status)) jobs.announce(job.value.id)
    controller?.abort()
    return
  }
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
  if (!preview.value || !targetIds.value.length || revalidating.value || refreshFailed.value
    || (state.value !== 'ready' && state.value !== 'ambiguous')) return
  const original = preview.value
  if (state.value !== 'ambiguous') token = crypto.randomUUID().replace(/-/g, '')
  localStorage.setItem(storageKey.value, JSON.stringify({ preview: original, token }))
  state.value = 'submitting'; message.value = ''
  try {
    const result = await questionBankApi.submitRepair(original, targetIds.value, token)
    activeJobId.value = result.id; jobs.track(result, { announce: false }); state.value = 'running'
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
    title="AI 补齐清单"
    :description="`${volumeLabel} · ${kind === 'skills' ? '未挂关键步骤题目' : kind === 'analysis' ? '分析未完成题目' : '有缺失的题目'}`"
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
          <p class="qb-repair-summary">待处理 <strong>{{ preview.question_count }}</strong> 题；可由 AI 补齐 <strong>{{ preview.repairable_count }}</strong> 题，待题型／标准整理 <strong>{{ organizeCount }}</strong> 题，需核对资料 <strong>{{ reviewCount }}</strong> 题。</p>
          <p>先补基础分析，再检查缺失匹配。题型、知识点和关键步骤可能在同次分析中完成，也可能需要后续补充；本清单不启动章节题型整理。</p>
          <p v-if="preview.repairable_count > 500">每次最多处理 500 道，剩余题目可在本次结束后继续补齐。</p>
          <div v-if="repairSteps.length" class="qb-repair-steps"><div v-for="{ part, count } in repairSteps" :key="part"><strong>{{ labels[part] }} · 本次补齐 {{ count }} 题</strong><span>{{ part === 'tags' ? '只为缺失或过期标签的题补标签' : part === 'evidence' || part === 'criteria' ? '进入判定点分析流程，保留已有可用结果' : part === 'types' ? '从当前候选中选择主题型；已确认无匹配的题型不单独重试' : '只补缺失关联，保留判定点正文' }}</span></div></div>
          <details class="qb-repair-list" open><summary>查看逐题清单 · {{ preview.items.length }} 题</summary><table class="app-table app-table--sticky"><thead><tr><th>题目</th><th>缺失部分</th><th>处理方式</th></tr></thead><tbody><tr v-for="item in preview.items" :key="item.id"><td><strong>第 {{ item.question_number }} 题</strong><small>{{ item.paper_title }}</small></td><td>{{ item.missing.map(part => labels[part]).join('、') }}</td><td><template v-if="item.blocked_reason"><strong>{{ repairStage(item) === 'organize' ? '待题型／标准整理' : '需核对资料' }}</strong><small>{{ item.blocked_reason }}</small><AppButton variant="ghost" size="small" @click="emit('openQuestion', { questionId: item.id, paperId: null })">{{ repairStage(item) === 'organize' ? '查看题目' : '打开题目核对' }}</AppButton></template><template v-else>{{ targetIds.includes(item.id) ? item.type_pending_reason ? `AI 可补其他缺失；${item.type_pending_reason}` : 'AI 补齐所列缺失部分' : '下一批 AI 补齐' }}</template></td></tr></tbody></table></details>
          <p v-if="targetIds.length" class="qb-repair-cost">确认后将使用配置的 AI 模型，按服务商计费，金额取决于题目长度和模型。待整理与需核对资料的题目不提交；失败或结果不确定时不自动追加请求。</p>
          <p v-else>当前没有可由 AI 补齐的题目。请按具体原因整理题型或标准，或打开题目核对资料。</p>
        </template>
        <AppButton v-if="state === 'error' || refreshFailed" variant="secondary" @click="load">重新统计</AppButton>
        <template #footer>
          <AppButton v-if="state === 'running' && job && !TERMINAL_JOB_STATUSES.has(job.status)" variant="primary" @click="emit('close')">转到后台</AppButton>
          <template v-else-if="state === 'submitting' || state === 'ambiguous' || state === 'ready'">
            <AppButton variant="secondary" @click="emit('close')">{{ targetIds.length ? '取消' : '关闭' }}</AppButton>
            <AppButton v-if="targetIds.length" variant="primary" :disabled="revalidating || refreshFailed || state === 'submitting'" @click="start">{{ state === 'ambiguous' ? '找回本次任务' : state === 'submitting' ? '正在提交…' : `确认补齐 ${targetIds.length} 题` }}</AppButton>
          </template>
        </template>
  </AppDialog>
</template>
