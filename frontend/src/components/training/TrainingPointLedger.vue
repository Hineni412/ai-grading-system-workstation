<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import type { PersonalizedPaperInstance, TrainingAssessmentPoint, TrainingAssessmentQuestion, TrainingPointState, TrainingScanBatch } from '../../api/training'
import { issueLabel, needsReview, pointKey, resultTotals, stateLabel, type ReturnRow } from '../../features/training/training-return'
import AppButton from '../design-system/AppButton.vue'
import TrainingFeedbackSheet from './TrainingFeedbackSheet.vue'
import TrainingScanIssueForm from './TrainingScanIssueForm.vue'
const props = defineProps<{ row: ReturnRow; batch: TrainingScanBatch; paper?: PersonalizedPaperInstance; busy: boolean; notes: Record<string, string> }>()
const emit = defineEmits<{
  lock: [q: TrainingAssessmentQuestion, p: TrainingAssessmentPoint, state: TrainingPointState]
  note: [key: string, value: string]; onlyPending: [value: boolean]; view: [value: 'points' | 'feedback']
  assess: [retry: boolean]; recover: []; query: []; publish: []; cancel: []; next: []; replay: []; withdraw: []
  resolve: [action: 'match' | 'replace' | 'dismiss', paperId: string, pageNumber: number]; openDraft: [id: string]
}>()
const body = ref<HTMLElement | null>(null)
const assessment = computed(() => props.row.record?.assessment)
const feedback = computed(() => props.row.record?.feedback)
const totals = computed(() => resultTotals(assessment.value))
const editingDisabled = computed(() => props.busy || Boolean(props.row.record?.busy) || Boolean(props.row.record?.loading) || props.row.stage === 'running')
const expanded = ref<Record<string, boolean>>({})
const canPublish = computed(() => assessment.value && assessment.value.status !== 'running' && totals.value.completed > 0)
const staleFeedback = computed(() => feedback.value && feedback.value.status !== 'withdrawn' && feedback.value.source_review_revision !== assessment.value?.review_revision)
const questions = computed(() => (assessment.value?.questions ?? []).map(q => ({ ...q, review_points: q.review_points.filter(p => !props.row.record?.onlyPending || needsReview(p)) })).filter(q => q.review_points.length || !props.row.record?.onlyPending))
const states: TrainingPointState[] = ['met', 'not_met', 'unreadable']
function key(q: TrainingAssessmentQuestion, p: TrainingAssessmentPoint) { return props.row.submission ? pointKey(props.row.submission, q, p) : '' }
function pendingLabel(p: TrainingAssessmentPoint) { return p.candidate_state === 'unreadable' ? 'AI 无法辨认' : !p.candidate_state ? 'AI 未判定' : 'AI 拿不准' }
function focusPending() {
  const point = body.value?.querySelector<HTMLElement>('[data-pending="true"]')
  if (!point || !body.value) return
  const box = point.getBoundingClientRect(), scrollbox = body.value.getBoundingClientRect()
  body.value.scrollTop += box.top - scrollbox.top - 8
  point.focus({ preventScroll: true })
}
watch(() => props.row.id, async () => { await nextTick(); focusPending() }, { immediate: true })
watch(() => assessment.value?.review_revision, async (current, previous) => { if (current !== previous) { await nextTick(); focusPending() } })
defineExpose({ focusPending })
</script>
<template>
  <section class="return-ledger" aria-label="判定点台账">
    <header class="return-ledger__header">
      <strong v-if="row.page">异常页 · 上传文件第 {{ row.page.upload_page_number }} 页</strong>
      <strong v-else-if="row.stage === 'scan_issue'">{{ row.submission?.missing_pages.length ? `缺少第 ${row.submission.missing_pages.join('、')} 页` : '需要核对' }}</strong>
      <strong v-else-if="row.stage === 'unjudged'">整卷 {{ paper?.question_count ?? 0 }} 题 · {{ paper?.criterion_point_count ?? 0 }} 个判定点</strong>
      <strong v-else-if="assessment && ['review', 'update', 'done'].includes(row.stage)">{{ row.stage === 'done' ? `${row.submission?.student_name || ''} · ` : '' }}达成 {{ totals.met }}/{{ totals.total }} 点 · {{ row.stage === 'done' ? '已完成' : totals.pending ? `待复核 ${totals.pending} 点` : '已全部确定' }}</strong>
      <strong v-else>{{ row.label }}</strong>
      <label v-if="['review', 'update'].includes(row.stage)" class="only-pending"><input type="checkbox" :checked="row.record?.onlyPending" @change="emit('onlyPending', ($event.target as HTMLInputElement).checked)">只看待复核</label>
      <details v-if="assessment" class="request-record"><summary>请求记录 · {{ assessment.expected_question_count }} 题 · 已请求 {{ assessment.request_count }} 次 · {{ (assessment.usage.total_tokens ?? 0).toLocaleString() }} tokens</summary><p>{{ assessment.model_name || '模型判定' }} · {{ assessment.action_message }}</p></details>
      <nav v-if="feedback && ['review', 'update', 'done'].includes(row.stage)" class="ledger-view-tabs" aria-label="台账内容"><button type="button" :aria-pressed="row.record?.view === 'feedback'" @click="emit('view', 'feedback')">掌握度变化</button><button type="button" :aria-pressed="row.record?.view === 'points'" @click="emit('view', 'points')">逐点结果</button></nav>
    </header>
    <div ref="body" class="return-ledger__body">
      <p v-if="staleFeedback" class="stale-feedback">判定已修改，需重新确认并更新掌握度</p>
      <TrainingScanIssueForm v-if="row.page" :key="row.id" :batch="batch" :page="row.page" :busy="busy" @resolve="(action, id, n) => emit('resolve', action, id, n)" />
      <div v-else-if="row.stage === 'scan_issue'"><p>{{ row.submission?.issue_codes.map(issueLabel).join('；') }}</p><p>补传扫描件，或在异常页中匹配到这一份</p><AppButton variant="ghost" :disabled="busy" @click="emit('cancel')">取消这份提交</AppButton></div>
      <div v-else-if="row.stage === 'running'" class="ledger-running"><div v-for="n in 3" :key="n" class="ledger-skeleton" aria-hidden="true" /><p>后台正在判定，页面会自动查询结果</p><AppButton v-if="row.record?.queryPaused" variant="secondary" :disabled="Boolean(row.record?.busy) || busy" @click="emit('query')">重新查询</AppButton><AppButton variant="ghost" :disabled="Boolean(row.record?.busy) || busy" @click="emit('recover')">接管中断状态</AppButton></div>
      <p v-else-if="row.stage === 'cancelled'">这一份没有计入本次回收</p>
      <AppButton v-else-if="row.stage === 'unknown'" variant="secondary" :disabled="busy || row.record?.loading" @click="emit('query')">{{ row.record?.loading ? '正在读取…' : '重新读取' }}</AppButton>
      <template v-else-if="assessment">
        <p v-if="assessment.action_message && assessment.status !== 'succeeded'" class="ledger-action">{{ assessment.action_message }}</p>
        <AppButton v-if="['failed', 'cancelled'].includes(assessment.status) && row.stage === 'review'" variant="secondary" :disabled="editingDisabled" @click="emit('assess', true)">教师确认后重试一次（1 次请求）</AppButton>
        <TrainingFeedbackSheet v-if="feedback && row.record?.view === 'feedback'" :feedback="feedback" :busy="editingDisabled" @replay="emit('replay')" @withdraw="emit('withdraw')" @open-draft="emit('openDraft', $event)" />
        <ol v-else-if="['review', 'update', 'done'].includes(row.stage)" class="ledger-questions"><li v-for="q in questions" :key="q.task_item_code"><header><strong>第 {{ q.item_order }} 题</strong><span class="point-dots"><i v-for="p in assessment.questions.find(item => item.task_item_code === q.task_item_code)?.review_points" :key="p.point_id" :class="[`is-${p.state || 'missing'}`, { 'is-pending': needsReview(p) }]" :title="needsReview(p) ? `待复核 · ${stateLabel(p.state)}` : stateLabel(p.state)" /></span><span>{{ q.met_count }}/{{ q.total_count }} 点</span></header>
          <article v-for="p in q.review_points" :key="p.point_id" class="ledger-point" :class="{ 'is-pending': needsReview(p) }" :data-point-key="key(q, p)" :data-pending="needsReview(p)" tabindex="-1" :aria-label="`第 ${q.item_order} 题 · ${p.content}`">
            <div class="point-text"><button type="button" class="point-content" :class="{ expanded: expanded[key(q, p)] }" :aria-expanded="Boolean(expanded[key(q, p)])" @click="expanded[key(q, p)] = !expanded[key(q, p)]">{{ p.content }}</button><small v-if="p.evidence">AI：{{ p.evidence }}</small><small v-if="p.teacher_locked && p.teacher_reason">教师：{{ p.teacher_reason }}</small><span v-if="needsReview(p)" class="point-warning">{{ pendingLabel(p) }}</span></div>
            <div class="point-controls"><div class="point-segments"><button v-for="state in states" :key="state" type="button" :data-final-state="state" :aria-label="`${p.content}：${stateLabel(state)}`" :aria-pressed="!needsReview(p) && p.state === state" :disabled="editingDisabled" @click="emit('lock', q, p, state)">{{ stateLabel(state) }}</button></div><small>{{ p.teacher_locked ? '教师已锁定' : 'AI' }}</small><details class="point-menu"><summary aria-label="判定点更多操作">⋯</summary><div><AppButton variant="ghost" :disabled="editingDisabled" @click="emit('lock', q, p, 'uncertain')">标为不确定</AppButton><label>加备注<input class="app-input" :value="notes[key(q, p)] || ''" maxlength="500" :disabled="editingDisabled" @input="emit('note', key(q, p), ($event.target as HTMLInputElement).value)"></label></div></details></div>
            <span v-if="row.record?.busy === key(q, p)" class="point-saving">正在保存…</span>
          </article><p v-if="!q.review_points.length && q.review_status !== 'completed'" class="point-warning">本题缺少判定点，仍需教师处理。</p>
        </li></ol>
      </template>
      <p v-if="row.record?.message" role="status" class="ledger-notice">{{ row.record.message }}</p><p v-if="row.record?.error" role="alert" class="ledger-error">{{ row.record.error }}</p>
    </div>
    <footer v-if="['unjudged', 'failed', 'review', 'update'].includes(row.stage)" class="return-ledger__footer">
      <AppButton v-if="row.stage === 'unjudged'" variant="primary" :disabled="editingDisabled" @click="emit('assess', false)">判定这一份（1 次模型请求）</AppButton>
      <AppButton v-else-if="row.stage === 'failed'" variant="secondary" :disabled="editingDisabled" @click="emit('assess', true)">教师确认后重试一次（1 次请求）</AppButton>
      <template v-else><span>{{ totals.pending ? `本份待复核 ${totals.pending} 点` : '本份已全部确定' }}</span><div><AppButton variant="primary" :disabled="editingDisabled || !canPublish" @click="emit('publish')">{{ totals.pending ? `确认已完成的 ${totals.completed} 题并更新` : '确认并更新掌握度' }}</AppButton><AppButton variant="ghost" @click="emit('next')">下一份 ›</AppButton></div><small>J/K 切换学生 · Tab 下一个待复核点 · 1/2/3 达成/未达成/无法辨认</small></template>
    </footer>
  </section>
</template>
<style scoped>
.return-ledger{display:flex;flex-direction:column;min-width:0;min-height:0;overflow:hidden;border:1px solid var(--color-border-default);border-radius:var(--radius-panel);background:var(--color-bg-surface);font-size:var(--font-size-dense)}
.return-ledger__header{display:flex;align-items:center;flex-wrap:wrap;gap:var(--space-2);flex:none;padding:var(--space-3);border-bottom:1px solid var(--color-border-subtle)}.return-ledger__header>strong{font-size:var(--font-size-dense)}.only-pending{margin-left:auto;display:flex;gap:var(--space-1);align-items:center;font-size:var(--font-size-caption)}.request-record{flex-basis:100%;color:var(--color-text-muted);font-size:var(--font-size-caption)}summary{cursor:pointer}.request-record p{margin:var(--space-2) 0}.ledger-view-tabs{display:flex;gap:var(--space-4);width:100%}.ledger-view-tabs button{padding:var(--space-2) 0;border:0;border-bottom:2px solid transparent;background:transparent;font:inherit;color:var(--color-text-secondary);cursor:pointer}.ledger-view-tabs button[aria-pressed=true]{border-color:var(--color-accent);color:var(--color-accent)}
.return-ledger__body{flex:1;min-height:0;overflow-y:auto;padding:var(--space-3)}.return-ledger__body>div>p{line-height:var(--line-height-body);color:var(--color-text-secondary)}.stale-feedback{padding:var(--space-2);border:1px solid var(--color-warning);background:var(--color-warning-subtle);color:var(--color-warning);border-radius:var(--radius-control);font-size:var(--font-size-caption)}.ledger-action{color:var(--color-text-secondary);line-height:var(--line-height-body)}
.ledger-questions{padding:0;margin:0;list-style:none}.ledger-questions>li{padding-block:var(--space-3);border-bottom:1px solid var(--color-border-subtle)}.ledger-questions>li:first-child{padding-top:0}.ledger-questions header{display:flex;gap:var(--space-2);align-items:center}.ledger-questions header>span:last-child{margin-left:auto;font-size:var(--font-size-caption);color:var(--color-text-muted)}.point-dots{display:flex;gap:3px;flex-wrap:wrap}.point-dots i{width:8px;height:8px;border-radius:50%;background:var(--color-text-muted)}.point-dots .is-met{background:var(--color-success)}.point-dots .is-not_met{background:var(--color-danger)}.point-dots .is-unreadable{background:var(--color-info)}.point-dots .is-uncertain{background:var(--color-warning)}.point-dots .is-pending{background:transparent;border:2px solid var(--color-warning)}
.ledger-point{position:relative;display:flex;gap:var(--space-2);padding:var(--space-2);margin-top:var(--space-2);border:1px solid transparent;border-radius:var(--radius-control)}.ledger-point.is-pending{border-color:color-mix(in srgb,var(--color-warning) 55%,transparent);background:color-mix(in srgb,var(--color-warning-subtle) 55%,white)}.ledger-point:focus{outline:2px solid var(--color-accent);outline-offset:1px}.point-text{min-width:0;flex:1}.point-content{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;width:100%;padding:0;background:transparent;border:0;color:var(--color-text-primary);font:inherit;font-size:var(--font-size-caption);line-height:var(--line-height-body);text-align:left;cursor:pointer}.point-content.expanded{display:block}.point-text small{display:block;margin-top:var(--space-1);font-size:var(--font-size-caption);color:var(--color-text-muted);line-height:var(--line-height-body);overflow-wrap:anywhere}.point-warning{display:block;color:var(--color-warning);font-size:var(--font-size-caption);margin-top:var(--space-1)}
.point-controls{display:flex;flex-wrap:wrap;align-items:center;justify-content:flex-end;align-content:start;gap:var(--space-1);flex:0 0 164px}.point-segments{display:flex;width:100%}.point-segments button{flex:1;padding:4px 3px;border:1px solid var(--color-border-default);background:var(--color-bg-surface);font:inherit;font-size:var(--font-size-caption);white-space:nowrap;cursor:pointer}.point-segments button+button{border-left:0}.point-segments button:first-child{border-radius:6px 0 0 6px}.point-segments button:last-child{border-radius:0 6px 6px 0}.point-segments button[aria-pressed=true]{background:var(--color-accent-subtle);color:var(--color-accent)}.point-segments button:disabled{opacity:var(--opacity-disabled);cursor:wait}.point-controls>small{color:var(--color-text-muted);font-size:var(--font-size-caption)}.point-menu{font-size:var(--font-size-caption)}.point-menu summary{padding:0 var(--space-1);list-style:none}.point-menu[open]{flex-basis:100%}.point-menu label{display:grid;gap:var(--space-1)}.point-menu input{width:100%;min-width:0;font-size:var(--font-size-caption)}.point-menu :deep(button){font-size:var(--font-size-caption)}.point-saving{position:absolute;bottom:-6px;right:var(--space-2);font-size:var(--font-size-caption);color:var(--color-accent);background:var(--color-bg-surface)}
.return-ledger__footer{flex:none;display:flex;flex-direction:column;gap:var(--space-2);padding:var(--space-3);border-top:1px solid var(--color-border-default);background:var(--color-bg-surface)}.return-ledger__footer>span{font-size:var(--font-size-caption);color:var(--color-text-muted)}.return-ledger__footer>div{display:flex;justify-content:space-between;gap:var(--space-2);flex-wrap:wrap}.return-ledger__footer small{font-size:var(--font-size-caption);line-height:1.6;color:var(--color-text-muted)}.ledger-error{color:var(--color-danger);font-size:var(--font-size-caption);line-height:var(--line-height-body)}.ledger-notice{color:var(--color-text-secondary);font-size:var(--font-size-caption);line-height:var(--line-height-body)}.ledger-skeleton{height:45px;margin-bottom:var(--space-3);background:var(--color-bg-subtle);border-radius:var(--radius-control);animation:ledger-loading 1s ease infinite alternate}@keyframes ledger-loading{to{opacity:.4}}@media(prefers-reduced-motion:reduce){.ledger-skeleton{animation:none}}
@media(max-width:1023px){.return-ledger__body{max-height:65vh}.point-controls{flex-basis:180px}}@media(max-width:500px){.ledger-point{flex-wrap:wrap}.point-controls{flex-basis:100%}.point-segments{width:auto;flex:1}}
</style>
