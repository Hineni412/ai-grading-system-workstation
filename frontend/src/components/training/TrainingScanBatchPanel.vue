<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, toRef, watch } from 'vue'
import type { PersonalizedPaperInstance, TrainingPointState } from '../../api/training'
import { useTrainingReturn } from '../../features/training/use-training-return'
import { actionable, batchLabel, nextPending, type InitialFocus, type ReturnRow } from '../../features/training/training-return'
import FeedbackBanner from '../design-system/FeedbackBanner.vue'
import AppButton from '../design-system/AppButton.vue'
import TrainingReturnStart from './TrainingReturnStart.vue'
import TrainingReturnSummary from './TrainingReturnSummary.vue'
import TrainingReturnQueue from './TrainingReturnQueue.vue'
import TrainingAnswerSheet from './TrainingAnswerSheet.vue'
import TrainingPointLedger from './TrainingPointLedger.vue'
import TrainingCostConfirmDialog from './TrainingCostConfirmDialog.vue'
const props = defineProps<{ instances: PersonalizedPaperInstance[]; initialFocus?: InitialFocus }>()
const emit = defineEmits<{ openDraft: [id: string]; progressChange: [value: { received: number; total: number; completed: boolean }]; focusConsumed: [] }>()
const controller = useTrainingReturn(toRef(props, 'instances'), toRef(props, 'initialFocus'))
const { batch, batches, records, notes, rows, selectedId, selected, busy, historyLoaded, message, errorMessage, bulk } = controller
const root = ref<HTMLElement | null>(null)
const ledger = ref<InstanceType<typeof TrainingPointLedger> | null>(null)
async function focusPending() { await nextTick(); ledger.value?.focusPending() }
defineExpose({ focusPending })
const queueFilter = ref('all'), countFilter = ref(''), supplement = ref(false)
const filteredRows = computed(() => rows.value.filter(r => countFilter.value ? r.stage === countFilter.value : queueFilter.value === 'pending' ? actionable(r) : queueFilter.value === 'done' ? r.stage === 'done' : true))
const confirmation = ref<{ batch: boolean; retry: boolean; list: Array<{ submission_id: string; revision: number }>; names: string[] } | null>(null)
watch(batch, () => { countFilter.value = ''; queueFilter.value = 'all'; supplement.value = false; confirmation.value = null })
watch([batch, records], () => {
  const submissions = batch.value?.submissions.filter(s => s.status !== 'cancelled') ?? []
  emit('progressChange', { received: submissions.filter(s => s.status === 'ready').length, total: submissions.length,
    completed: submissions.length > 0 && rows.value.filter(r => r.submission && r.stage !== 'cancelled').every(r => r.stage === 'done') })
}, { deep: true })
let consumed = false
watch(selectedId, id => { if (id && props.initialFocus && !consumed) { consumed = true; emit('focusConsumed') } })
watch(historyLoaded, loaded => { if (loaded && !rows.value.length && props.initialFocus && !consumed) { consumed = true; emit('focusConsumed') } })
function snapshot(list: ReturnRow[]) { return list.filter(r => r.submission).map(r => ({ submission_id: r.submission!.submission_id, revision: r.submission!.revision })) }
function askAssessment(isBatch: boolean, retry = false) {
  if (busy.value || bulk.value) return
  const list = isBatch ? rows.value.filter(r => r.stage === 'unjudged') : selected.value ? [selected.value] : []
  if (!list.length || list.some(r => r.record?.busy || r.record?.loading)) return
  confirmation.value = { batch: isBatch, retry, list: snapshot(list), names: list.map(r => r.submission?.student_name || r.submission?.student_code || r.submission?.student_id || '') }
}
async function confirmAssessment() {
  const pending = confirmation.value
  confirmation.value = null
  if (!pending) return
  if (pending.batch) await controller.runBulk('assess', pending.list)
  else {
    const item = pending.list[0], row = rows.value.find(r => r.id === item?.submission_id && r.submission?.revision === item?.revision)
    if (row?.submission && (pending.retry ? ['failed', 'cancelled'].includes(row.record?.assessment?.status ?? '') : row.stage === 'unjudged')) await controller.assess(row.submission, pending.retry ? 'retry' : 'assess')
  }
}
function publishBatch() { void controller.runBulk('publish', snapshot(rows.value.filter(r => r.stage === 'update'))) }
function filterCount(value: string) { countFilter.value = countFilter.value === value ? '' : value; queueFilter.value = 'all' }
function filterQueue(value: string) { queueFilter.value = value; countFilter.value = '' }
function selectRow(id: string) { selectedId.value = id }
function query() {
  const row = selected.value
  if (row?.submission) void (row.stage === 'running' ? controller.queryResult(row.submission) : controller.loadSubmission(row.submission))
}
function keydown(event: KeyboardEvent) {
  if (root.value?.checkVisibility && !root.value.checkVisibility()) return
  if (confirmation.value || event.ctrlKey || event.altKey || event.metaKey || event.defaultPrevented) return
  const target = event.target as HTMLElement
  if (target.closest('input,select,textarea,[contenteditable="true"],dialog,[role="dialog"]')) return
  const lower = event.key.toLowerCase()
  if (lower === 'j' || lower === 'k') {
    const list = filteredRows.value, index = list.findIndex(r => r.id === selectedId.value)
    if (!list.length) return
    selectedId.value = list[(index + (lower === 'j' ? 1 : -1) + list.length) % list.length]!.id
    event.preventDefault(); return
  }
  const points = [...(root.value?.querySelectorAll<HTMLElement>('.return-ledger [data-pending="true"]') ?? [])]
  if (event.key === 'Tab' && points.length) {
    const parent = target.closest<HTMLElement>('[data-point-key]')
    const index = parent ? points.indexOf(parent) : -1
    const next = points[(index + (event.shiftKey ? -1 : 1) + points.length) % points.length]!
    const body = next.closest<HTMLElement>('.return-ledger__body')
    if (body) body.scrollTop += next.getBoundingClientRect().top - body.getBoundingClientRect().top - 8
    next.focus({ preventScroll: true }); event.preventDefault(); return
  }
  if (['1', '2', '3'].includes(event.key) && selected.value?.submission && !selected.value.record?.busy && !busy.value) {
    const parent = target.closest<HTMLElement>('[data-point-key]')
    const state = ({ '1': 'met', '2': 'not_met', '3': 'unreadable' } as Record<string, TrainingPointState>)[event.key]!
    const button = parent?.querySelector<HTMLButtonElement>(`[data-final-state="${state}"]`)
    if (button && !button.disabled) { event.preventDefault(); button.click() }
  }
}
onMounted(() => window.addEventListener('keydown', keydown))
onBeforeUnmount(() => window.removeEventListener('keydown', keydown))
</script>
<template>
  <section ref="root" class="training-scan-panel" aria-label="答卷回收与批改">
    <TrainingReturnSummary v-if="batch" :batch="batch" :batches="batches" :rows="rows" :busy="busy" :bulk="bulk" :filter="countFilter" @batch="controller.openBatch" @refresh="controller.openBatch(batch.batch_id)" @filter="filterCount" @supplement="supplement = !supplement" @assess="askAssessment(true)" @publish="publishBatch" @issue="selectedId = rows.find(r => r.stage === 'issue')?.id ?? selectedId" @stop="controller.stopRemaining" />
    <div v-else-if="batches.length" class="return-history"><select class="app-input" aria-label="扫描批次" value="" :disabled="busy" @change="controller.openBatch(($event.target as HTMLSelectElement).value)"><option value="">新建批次…</option><option v-for="b in batches" :key="b.batch_id" :value="b.batch_id">{{ batchLabel(b) }}</option></select></div>
    <FeedbackBanner v-if="message" role="status" tone="info" :description="message" /><FeedbackBanner v-if="errorMessage" role="alert" tone="error" :description="errorMessage" />
    <div v-if="!historyLoaded && busy" class="return-loading" aria-label="正在恢复扫描批次"><div v-for="n in 3" :key="n" /></div>
    <AppButton v-else-if="!historyLoaded" variant="secondary" @click="controller.restoreBatches">重新读取批次</AppButton>
    <TrainingReturnStart v-else-if="!batch" :instances="instances" :busy="busy" @import="controller.importFiles" />
    <template v-if="batch">
      <TrainingReturnStart v-if="supplement" :instances="instances" :busy="busy" supplement @import="controller.importFiles" />
      <div class="return-workspace">
        <TrainingReturnQueue :rows="filteredRows" :selected-id="selectedId" :count="batch.submissions.length" :filter="queueFilter" :has-next="Boolean(nextPending(rows, selectedId))" @select="selectRow" @filter="filterQueue" @next="controller.next" />
        <TrainingAnswerSheet v-if="selected" :submission="selected.submission" :issue="selected.page" :pages="batch.pages" :current-page="selected.record?.page ?? 1" @page="n => { if (selected?.record) selected.record.page = n }" />
        <TrainingPointLedger v-if="selected" ref="ledger" :row="selected" :batch="batch" :paper="instances.find(p => p.paper_instance_id === selected?.submission?.paper_instance_id)" :busy="busy || Boolean(bulk)" :notes="notes"
          @lock="(q, p, state) => selected?.submission && controller.lockPoint(selected.submission, q, p, state)" @note="(key, value) => notes[key] = value"
          @only-pending="value => { if (selected?.record) selected.record.onlyPending = value }" @view="value => { if (selected?.record) selected.record.view = value }"
          @assess="retry => askAssessment(false, retry)" @recover="selected.submission && controller.assess(selected.submission, 'recover')" @query="query"
          @publish="selected.submission && controller.syncEvidence(selected.submission)" @cancel="selected.submission && controller.cancelSubmission(selected.submission)" @next="controller.next"
          @replay="selected.submission && controller.replayEvidence(selected.submission)" @withdraw="selected.submission && controller.syncEvidence(selected.submission, 'withdraw')"
          @resolve="(action, id, n) => selected?.page && controller.resolve(selected.page, action, id, n)" @open-draft="emit('openDraft', $event)" />
      </div>
    </template>
    <TrainingCostConfirmDialog v-if="confirmation" :names="confirmation.names" :retry="confirmation.retry" :batch="confirmation.batch" @close="confirmation = null" @confirm="confirmAssessment" />
  </section>
</template>
<style scoped>
.training-scan-panel{min-width:0;display:flex;flex-direction:column;gap:var(--space-3)}.return-workspace{display:grid;grid-template-columns:240px minmax(0,1fr) 400px;gap:var(--space-3);height:max(460px,calc(100dvh - 240px));min-height:0}.return-notice,.return-error{margin:0;font-size:var(--font-size-caption);line-height:var(--line-height-body)}.return-error{color:var(--color-danger)}.return-notice{color:var(--color-text-secondary)}.return-history select{max-width:420px;width:100%}.return-loading{display:grid;gap:var(--space-3);max-width:760px}.return-loading div{height:65px;border-radius:var(--radius-control);background:var(--color-bg-subtle)}
@media(min-width:1024px) and (max-width:1279px){.return-workspace{grid-template-columns:220px minmax(0,1fr);grid-template-rows:minmax(0,55%) minmax(0,45%);height:max(650px,calc(100dvh - 230px))}.return-workspace>:first-child{grid-row:1/3}.return-workspace>:nth-child(2){max-height:55vh}.return-workspace>:nth-child(3){grid-column:2}}
@media(max-width:1023px){.return-workspace{display:flex;flex-direction:column;height:auto}.return-workspace>:nth-child(2){max-height:55vh;min-height:220px}.return-workspace>:nth-child(3){min-height:300px}}
</style>
