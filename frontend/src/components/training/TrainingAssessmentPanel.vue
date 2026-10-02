<script setup lang="ts">
import { masteryDetail } from '../knowledge-overview/model'
import AppButton from '@/components/design-system/AppButton.vue'

import { computed, onBeforeUnmount, ref, watch } from 'vue'

import {
  trainingApi,
  type TrainingAssessmentOutcome,
  type TrainingAssessmentPoint,
  type TrainingFeedback,
  type TrainingPointState,
  type TrainingSubmission,
  type TrainingScanPage,
} from '../../api/training'
import { ApiError, isAmbiguousWriteError } from '../../api/errors'
import StatusBadge from '../design-system/StatusBadge.vue'
import { knowledgeLeafLabel } from '../../api/question-bank'

const props = defineProps<{
  submission: TrainingSubmission
  pages?: TrainingScanPage[]
}>()

const emit = defineEmits<{
  openDraft: [draftId: string]
  summaryChange: [summary: { label: string; tone: 'info' | 'success' | 'warning' | 'danger'; met: number; total: number; published: boolean }]
}>()

interface PointEdit {
  state: TrainingPointState
  evidence: string
  reason: string
}

const assessment = ref<TrainingAssessmentOutcome | null>(null)
const feedback = ref<TrainingFeedback | null>(null)
const feedbackIsCurrent = computed(() => feedback.value?.source_review_revision === assessment.value?.review_revision)
const publishedCurrent = computed(() => feedback.value?.status === 'complete' && feedbackIsCurrent.value)
const activeView = ref<'review' | 'feedback'>('review')
const selectedPageId = ref('')
const answerPages = computed(() => (props.pages ?? []).filter(page => page.state === 'assigned').sort((a, b) => (a.page_number ?? 0) - (b.page_number ?? 0)))
const answerPage = computed(() => answerPages.value.find(page => page.scan_page_id === selectedPageId.value) ?? answerPages.value[0])
const resultTotals = computed(() => ({
  met: assessment.value?.questions.reduce((sum, question) => sum + question.met_count, 0) ?? 0,
  total: assessment.value?.expected_point_count ?? 0,
  pending: assessment.value?.questions.reduce((sum, question) => sum + question.uncertain_count + question.unreadable_count, 0) ?? 0,
}))
const skillChanges = computed(() => {
  const changes = feedback.value?.mastery_changes ?? []
  const skills = changes.filter(item => /^(sk_|ki_)/.test(String(item.stable_key)))
  return skills.length ? skills : changes
})
const aggregateChanges = computed(() => (feedback.value?.mastery_changes ?? []).filter(item => !skillChanges.value.includes(item)))
watch(feedback, value => { if (value && value.status !== 'withdrawn' && feedbackIsCurrent.value) activeView.value = 'feedback' })
watch([assessment, feedback], () => {
  const value = assessment.value
  emit('summaryChange', {
    label: publishedCurrent.value ? '已完成' : value ? statusLabel(value) : '待批改',
    tone: publishedCurrent.value ? 'success' : value ? statusTone(value) : 'info',
    met: resultTotals.value.met, total: resultTotals.value.total, published: publishedCurrent.value,
  })
})
const edits = ref<Record<string, PointEdit>>({})
const busy = ref('')
const message = ref('')
const errorMessage = ref('')
const waitingForResult = ref(false)
const queryPaused = ref(false)
let pollTimer: ReturnType<typeof setTimeout> | undefined
let queryFailures = 0
let viewVersion = 0

function stopPolling(): void {
  clearTimeout(pollTimer)
  pollTimer = undefined
}

function scheduleResultQuery(): void {
  stopPolling()
  pollTimer = setTimeout(() => { void queryResult() }, 3000)
}

async function queryResult(): Promise<void> {
  const version = viewVersion
  queryPaused.value = false
  try {
    const result = await trainingApi.getTrainingAssessment(
      props.submission.submission_id,
      props.submission.revision,
    )
    if (version !== viewVersion) return
    assessment.value = result
    queryFailures = 0
    errorMessage.value = ''
    followAssessment()
    if (!waitingForResult.value) await loadFeedback(version)
  } catch {
    if (version !== viewVersion) return
    queryFailures += 1
    if (queryFailures < 3) scheduleResultQuery()
    else {
      queryPaused.value = true
      errorMessage.value = '暂时无法确认后台结果，请重新查询；不会自动追加模型请求。'
    }
  }
}

function followAssessment(): void {
  waitingForResult.value = assessment.value?.status === 'running'
  if (waitingForResult.value) {
    message.value = '后台正在判定，页面会自动查询最终结果。'
    scheduleResultQuery()
  } else {
    stopPolling()
    message.value = assessment.value?.action_message || ''
  }
}

const canPublish = computed(() => (
  assessment.value
  && assessment.value.status !== 'running'
  && assessment.value.questions.some(
    (question) => question.review_status === 'completed',
  )
))

watch(
  assessment,
  (value) => {
    if (!value) return
    const next = { ...edits.value }
    for (const question of value.questions) {
      for (const point of question.review_points) {
        const key = pointKey(question.task_item_code, point.point_id)
        next[key] = {
          state: point.state ?? 'uncertain',
          evidence: point.evidence || '',
          reason: point.teacher_reason || '教师核对原始训练答卷',
        }
      }
    }
    edits.value = next
  },
  { immediate: true },
)

function requestToken(): string {
  const bytes = new Uint8Array(16)
  globalThis.crypto.getRandomValues(bytes)
  return [...bytes]
    .map((value) => value.toString(16).padStart(2, '0'))
    .join('')
}

function pointKey(taskItemCode: string, pointId: string): string {
  return `${taskItemCode}:${pointId}`
}

function editValue(
  taskItemCode: string,
  point: TrainingAssessmentPoint,
): PointEdit {
  return edits.value[pointKey(taskItemCode, point.point_id)] ?? {
    state: point.state ?? 'uncertain',
    evidence: point.evidence || '',
    reason: point.teacher_reason || '教师核对原始训练答卷',
  }
}

function setEditField(
  taskItemCode: string,
  point: TrainingAssessmentPoint,
  field: keyof PointEdit,
  event: Event,
): void {
  const key = pointKey(taskItemCode, point.point_id)
  const value = (event.target as HTMLInputElement | HTMLSelectElement).value
  edits.value = {
    ...edits.value,
    [key]: {
      ...editValue(taskItemCode, point),
      [field]: value,
    },
  }
}

function stateLabel(state: TrainingPointState | null | undefined): string {
  return {
    met: '已达成',
    not_met: '未达成',
    uncertain: '不确定',
    unreadable: '无法辨认',
  }[state ?? 'uncertain']
}

function statusLabel(value: TrainingAssessmentOutcome): string {
  if (value.status === 'failed') return '判定失败'
  if (value.status === 'cancelled') return '已取消'
  if (value.status === 'running') return '判定进行中'
  if (['complete', 'completed'].includes(value.workflow_status)) return '待确认更新'
  return '待复核'
}

function statusTone(value: TrainingAssessmentOutcome): 'info' | 'success' | 'warning' | 'danger' {
  if (value.status === 'failed' || value.status === 'cancelled') return 'danger'
  if (value.status === 'running') return 'info'
  if (['complete', 'completed'].includes(value.workflow_status)) return 'success'
  return 'warning'
}

function safeError(error: unknown): string {
  if (error instanceof ApiError) {
    if (
      error.code === 'training_assessment_review_conflict'
      || error.code === 'training_assessment_revision_conflict'
    ) {
      return '判定内容已在另一处变化，请刷新后再操作。'
    }
    if (error.code === 'training_assessment_not_found') {
      return ''
    }
  }
  return '本次操作未完成，已有判定和证据保持不变，请按页面提示重试。'
}

async function restore(): Promise<void> {
  const version = ++viewVersion
  stopPolling()
  assessment.value = null
  feedback.value = null
  edits.value = {}
  waitingForResult.value = false
  queryPaused.value = false
  queryFailures = 0
  busy.value = 'restore'
  try {
    const result = await trainingApi.getTrainingAssessment(
      props.submission.submission_id,
      props.submission.revision,
    )
    if (version !== viewVersion) return
    assessment.value = result
    followAssessment()
  } catch (error) {
    if (version !== viewVersion) return
    const text = safeError(error)
    if (text) {
      errorMessage.value = text
      waitingForResult.value = true
      queryPaused.value = true
    }
    return
  } finally {
    if (version === viewVersion) busy.value = ''
  }
  if (!waitingForResult.value) await loadFeedback(version)
}

async function loadFeedback(version = viewVersion): Promise<void> {
  try {
    const result = await trainingApi.getTrainingFeedback(
      props.submission.submission_id,
      props.submission.revision,
    )
    if (version === viewVersion) feedback.value = result
  } catch (error) {
    if (version !== viewVersion) return
    const text = safeError(error)
    if (text) errorMessage.value = text
  }
}

async function startAssessment(): Promise<void> {
  if (busy.value || waitingForResult.value) return
  if (!window.confirm('开始整卷判定将发送本份训练答卷、题目和判定点，调用模型 1 次并产生费用。实际费用以模型服务商计费为准。失败后不会自动重试。确认开始吗？')) return
  const version = viewVersion
  busy.value = 'assess'
  errorMessage.value = ''
  message.value = ''
  try {
    const result = await trainingApi.startTrainingAssessment(
      props.submission.submission_id,
      props.submission.revision,
    )
    if (version !== viewVersion) return
    assessment.value = result
    followAssessment()
  } catch (error) {
    if (version !== viewVersion) return
    if (isAmbiguousWriteError(error)) {
      waitingForResult.value = true
      message.value = '请求等待较久，正在查询后台结果；不会重新发送判定请求。'
      await queryResult()
    } else errorMessage.value = safeError(error)
  } finally {
    if (version === viewVersion) busy.value = ''
  }
}

async function savePoint(
  taskItemCode: string,
  point: TrainingAssessmentPoint,
): Promise<void> {
  if (!assessment.value || busy.value) return
  const key = pointKey(taskItemCode, point.point_id)
  const edit = edits.value[key]
  if (!edit?.evidence.trim() || !edit.reason.trim()) {
    errorMessage.value = '请填写教师核对依据和改判原因。'
    return
  }
  busy.value = key
  errorMessage.value = ''
  try {
    assessment.value = await trainingApi.reviewTrainingPoint(
      assessment.value,
      {
        operation_token: requestToken(),
        task_item_code: taskItemCode,
        point_id: point.point_id,
        final_state: edit.state,
        teacher_evidence: edit.evidence.trim(),
        teacher_reason: edit.reason.trim(),
      },
    )
    feedback.value = null
    message.value = '教师最终判定已锁定，覆盖数已按题重新计算。'
  } catch (error) {
    errorMessage.value = safeError(error)
  } finally {
    busy.value = ''
  }
}

async function assessmentAction(
  action: 'recover' | 'retry',
): Promise<void> {
  if (!assessment.value || busy.value) return
  if (action === 'retry' && !window.confirm('重试整卷判定将再次发送本份训练答卷、题目和判定点，追加 1 次模型请求并产生费用。实际费用以模型服务商计费为准。确认重试一次吗？')) return
  const version = viewVersion
  stopPolling()
  busy.value = action
  errorMessage.value = ''
  try {
    const result = await trainingApi.controlTrainingAssessment(
      assessment.value,
      {
        operation_token: requestToken(),
        action,
        reason: action === 'retry'
          ? '教师确认失败后显式重试一次'
          : '教师确认接管中断的判定运行',
      },
    )
    if (version !== viewVersion) return
    assessment.value = result
    followAssessment()
  } catch (error) {
    if (version !== viewVersion) return
    if (isAmbiguousWriteError(error)) {
      waitingForResult.value = true
      await queryResult()
    } else {
      errorMessage.value = safeError(error)
      if (waitingForResult.value) scheduleResultQuery()
    }
  } finally {
    if (version === viewVersion) busy.value = ''
  }
}

async function syncEvidence(
  action: 'publish' | 'withdraw',
): Promise<void> {
  if (!assessment.value || busy.value) return
  busy.value = action
  errorMessage.value = ''
  try {
    feedback.value = await trainingApi.syncTrainingEvidence(
      assessment.value,
      action,
      requestToken(),
    )
    message.value = feedback.value.summary.message
  } catch (error) {
    errorMessage.value = safeError(error)
  } finally {
    busy.value = ''
  }
}

async function replayEvidence(): Promise<void> {
  if (busy.value) return
  busy.value = 'replay'
  errorMessage.value = ''
  try {
    await trainingApi.replayTrainingEvidence()
    feedback.value = await trainingApi.getTrainingFeedback(
      props.submission.submission_id,
      props.submission.revision,
    )
    message.value = feedback.value.summary.message
  } catch (error) {
    errorMessage.value = safeError(error)
  } finally {
    busy.value = ''
  }
}

function masteryTitle(item: Record<string, unknown>): string {
  return knowledgeLeafLabel(String(item.display_name || item.stable_key || '训练目标'))
}

function masteryValue(
  item: Record<string, unknown>,
  key: 'mastery_before' | 'mastery_after',
): string {
  const legacyKey = key === 'mastery_before' ? 'v2_before' : 'v2_after'
  const snapshot = item[key] ?? item[legacyKey]
  if (
    !snapshot
    || typeof snapshot !== 'object'
    || !('value' in snapshot)
    || typeof snapshot.value !== 'number'
  ) {
    return '暂无'
  }
  if ('tier' in snapshot) return masteryDetail(snapshot as Parameters<typeof masteryDetail>[0])
  return `${Math.round(snapshot.value * 100)}%`
}

watch(() => [props.submission.submission_id, props.submission.revision], restore, { immediate: true })
onBeforeUnmount(() => {
  viewVersion += 1
  stopPolling()
})
</script>

<template>
  <section class="assessment-ledger" aria-label="训练判定与反馈">
    <header class="ledger-heading">
      <div><h3>批改与反馈</h3><span v-if="assessment">达成 <b>{{ resultTotals.met }} / {{ resultTotals.total }}</b> 个判定点<span v-if="resultTotals.pending"> · {{ resultTotals.pending }} 点待复核</span></span></div>
      <StatusBadge v-if="assessment" :tone="publishedCurrent ? 'success' : statusTone(assessment)" :label="publishedCurrent ? '已完成' : statusLabel(assessment)" />
    </header>

    <div v-if="!assessment" class="ledger-start">
      <p>整卷合并为 1 次模型请求；失败后由教师决定是否重试。</p>
      <AppButton variant="primary" :disabled="Boolean(busy) || waitingForResult" @click="startAssessment">{{ busy === 'restore' ? '正在读取判定…' : waitingForResult ? '正在等待后台结果…' : busy === 'assess' ? '正在判定…' : '开始整卷判定（1 次请求）' }}</AppButton>
    </div>
    <AppButton v-if="queryPaused" variant="secondary" :disabled="Boolean(busy)" @click="queryResult">重新查询结果</AppButton>

    <template v-if="assessment">
      <nav class="ledger-tabs" aria-label="批改内容">
        <button type="button" :aria-pressed="activeView === 'review'" @click="activeView = 'review'">逐题复核</button>
        <button v-if="feedback" type="button" :aria-pressed="activeView === 'feedback'" @click="activeView = 'feedback'">掌握度反馈</button>
        <details class="ledger-request"><summary>请求记录</summary><p>{{ assessment.expected_question_count }} 题 · 已请求 {{ assessment.request_count }} 次<span v-if="assessment.usage.total_tokens"> · {{ assessment.usage.total_tokens.toLocaleString() }} tokens</span></p></details>
      </nav>
      <p v-if="assessment.action_message && assessment.status !== 'succeeded'" class="ledger-action">{{ assessment.action_message }}</p>
      <AppButton v-if="assessment.status === 'failed'" variant="secondary" :disabled="Boolean(busy)" @click="assessmentAction('retry')">{{ busy === 'retry' ? '正在重试…' : '教师确认后重试一次' }}</AppButton>
      <AppButton v-else-if="assessment.status === 'running'" variant="secondary" :disabled="Boolean(busy)" @click="assessmentAction('recover')">接管中断状态</AppButton>

      <div v-show="activeView === 'review'" class="ledger-publish">
        <span>{{ publishedCurrent ? '掌握度已更新；修改判定后可再次确认。' : '确认已完成题目的结果，更新学生掌握度。' }}<small v-if="resultTotals.pending">不确定和无法辨认项保留待复核。</small></span>
        <AppButton variant="primary" :disabled="Boolean(busy) || !canPublish" @click="syncEvidence('publish')">{{ busy === 'publish' ? '正在更新…' : '确认并更新掌握度' }}</AppButton>
      </div>

      <div v-show="activeView === 'review'" class="review-workspace">
        <ol class="question-ledger">
          <li v-for="question in assessment.questions" :key="question.task_item_code">
            <header><strong>第 {{ question.item_order }} 题</strong><span>{{ question.met_count }}/{{ question.total_count }} 个点达成</span></header>
            <div class="point-list">
              <details v-for="point in question.review_points" :key="point.point_id" :open="point.state === 'uncertain' || point.state === 'unreadable' || !point.state">
                <summary><span>{{ point.content }}</span><em :class="`point-state is-${point.state || 'uncertain'}`">{{ stateLabel(point.state) }}{{ point.teacher_locked ? ' · 已锁定' : '' }}</em></summary>
                <p v-if="point.evidence" class="point-evidence">{{ point.evidence }}</p>
                <div class="point-review-form">
                  <label>最终判定<select class="app-input" :value="editValue(question.task_item_code, point).state" :disabled="Boolean(busy)" @change="setEditField(question.task_item_code, point, 'state', $event)"><option value="met">已达成</option><option value="not_met">未达成</option><option value="uncertain">不确定</option><option value="unreadable">无法辨认</option></select></label>
                  <label>教师核对依据<input class="app-input" :value="editValue(question.task_item_code, point).evidence" maxlength="500" :disabled="Boolean(busy)" @input="setEditField(question.task_item_code, point, 'evidence', $event)"></label>
                  <label>锁定原因<input class="app-input" :value="editValue(question.task_item_code, point).reason" maxlength="500" :disabled="Boolean(busy)" @input="setEditField(question.task_item_code, point, 'reason', $event)"></label>
                  <AppButton variant="secondary" :disabled="Boolean(busy)" @click="savePoint(question.task_item_code, point)">{{ busy === pointKey(question.task_item_code, point.point_id) ? '正在保存…' : '锁定此判定点' }}</AppButton>
                </div>
              </details>
            </div>
          </li>
        </ol>
        <aside v-if="answerPage" class="answer-preview" aria-label="原始扫描答卷">
          <header><strong>原始答卷</strong><a :href="answerPage.preview_url" target="_blank" rel="noopener">放大查看</a></header>
          <a :href="answerPage.preview_url" target="_blank" rel="noopener"><img :src="answerPage.preview_url" :alt="`原始答卷第 ${answerPage.page_number} 页`"></a>
          <nav aria-label="答卷页码"><button v-for="page in answerPages" :key="page.scan_page_id" type="button" :aria-pressed="page.scan_page_id === answerPage.scan_page_id" @click="selectedPageId = page.scan_page_id">第 {{ page.page_number }} 页</button></nav>
        </aside>
      </div>

      <section v-if="feedback" v-show="activeView === 'feedback'" class="feedback-sheet" aria-label="学生训练反馈">
        <p v-if="!feedbackIsCurrent && feedback.status !== 'withdrawn'" class="feedback-message">判定已修改；以下为上次保存的反馈，请重新确认并更新掌握度。</p>
        <header><h4>掌握度变化</h4><span>已保存 {{ feedback.summary.published_question_count }}/{{ feedback.summary.total_question_count }} 题证据</span><StatusBadge :tone="publishedCurrent ? 'success' : 'warning'" :label="feedback.status === 'withdrawn' ? '证据已撤回' : !feedbackIsCurrent ? '上次反馈' : feedback.status === 'complete' ? '已更新' : '部分更新'" /></header>
        <p v-if="feedback.status !== 'complete'" class="feedback-message">{{ feedback.summary.message }}</p>
        <AppButton v-if="feedback.status === 'publication_pending'" variant="secondary" :disabled="Boolean(busy)" @click="replayEvidence">{{ busy === 'replay' ? '正在补发…' : '安全补发未完成证据' }}</AppButton>
        <div v-if="skillChanges.length" class="mastery-table">
          <table><thead><tr><th>训练目标</th><th>训练前</th><th>训练后</th></tr></thead><tbody><tr v-for="item in skillChanges" :key="String(item.stable_key)"><td><details><summary>{{ masteryTitle(item) }}</summary><p>{{ String(item.display_name || '') }}</p><p>{{ String(item.reason || '') }}</p></details></td><td>{{ masteryValue(item, 'mastery_before') }}</td><td>{{ masteryValue(item, 'mastery_after') }}</td></tr></tbody></table>
        </div>
        <details v-if="aggregateChanges.length" class="aggregate-feedback"><summary>章节与小节汇总（{{ aggregateChanges.length }} 项）</summary><table><tbody><tr v-for="item in aggregateChanges" :key="String(item.stable_key)"><td><details><summary>{{ masteryTitle(item) }}</summary><p>{{ String(item.display_name || '') }}</p><p>{{ String(item.reason || '') }}</p></details></td><td>{{ masteryValue(item, 'mastery_before') }} → {{ masteryValue(item, 'mastery_after') }}</td></tr></tbody></table></details>
        <div class="next-round-card"><div><strong>下一轮补练</strong><p>{{ feedback.next_round.message }}</p></div><AppButton v-if="feedback.next_round.draft_id" variant="primary" @click="emit('openDraft', feedback.next_round.draft_id)">打开下一轮草稿</AppButton></div>
        <details v-if="feedback.status !== 'withdrawn'" class="feedback-maintenance"><summary>更正本次反馈</summary><AppButton variant="danger" :disabled="Boolean(busy)" @click="syncEvidence('withdraw')">撤回本次证据</AppButton></details>
      </section>
    </template>
    <p v-if="message && message !== assessment?.action_message" class="training-notice" role="status">{{ message }}</p>
    <p v-if="errorMessage" class="training-error" role="alert">{{ errorMessage }}</p>
  </section>
</template>

<style scoped>
.assessment-ledger{min-width:0;padding:var(--space-5);background:var(--color-bg-surface)}
.assessment-ledger h3,.assessment-ledger h4,.assessment-ledger p{margin:0}
.assessment-ledger h3{font-size:var(--font-size-h3)}
.ledger-heading,.feedback-sheet>header{display:flex;justify-content:space-between;align-items:center;gap:var(--space-3)}
.ledger-heading>div{display:flex;flex-wrap:wrap;align-items:baseline;gap:var(--space-3)}
.ledger-heading span,.feedback-sheet header>span{font-size:var(--font-size-dense);color:var(--color-text-secondary)}
.ledger-tabs{display:flex;align-items:center;gap:var(--space-4);margin:var(--space-4) 0;border-bottom:1px solid var(--color-border-default)}
.ledger-tabs>button{padding:var(--space-2) 0 var(--space-3);border:0;border-bottom:2px solid transparent;background:transparent;color:var(--color-text-secondary);font:inherit;font-size:var(--font-size-dense);cursor:pointer}
.ledger-tabs>button[aria-pressed=true]{border-color:var(--color-accent);color:var(--color-accent);font-weight:600}
.ledger-request{margin-left:auto;font-size:var(--font-size-caption);color:var(--color-text-muted);position:relative}
.ledger-request summary{cursor:pointer}.ledger-request p{position:absolute;right:0;z-index:2;min-width:220px;padding:var(--space-3);background:var(--color-bg-surface);border:1px solid var(--color-border-default);border-radius:var(--radius-control);box-shadow:var(--shadow-floating)}
.ledger-start{display:flex;justify-content:space-between;align-items:center;gap:var(--space-4);padding:var(--space-6) 0}
.ledger-start p,.ledger-action,.feedback-message,.next-round-card p,.point-evidence{font-size:var(--font-size-dense);color:var(--color-text-secondary);line-height:var(--line-height-body)}
.review-workspace{display:grid;grid-template-columns:minmax(0,1fr);gap:var(--space-5);align-items:start}
.review-workspace:has(.answer-preview){grid-template-columns:minmax(0,1.15fr) minmax(250px,.85fr)}
.question-ledger{list-style:none;padding:0;margin:0;min-width:0}
.question-ledger>li{padding:var(--space-3) 0;border-bottom:1px solid var(--color-border-default)}
.question-ledger>li:first-child{padding-top:0}
.question-ledger header{display:flex;align-items:center;justify-content:space-between;gap:var(--space-3);font-size:var(--font-size-dense)}
.question-ledger header>span{color:var(--color-text-muted);font-variant-numeric:tabular-nums}
.point-list details{margin-top:var(--space-2)}
.point-list summary{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:start;gap:var(--space-3);cursor:pointer;font-size:var(--font-size-dense);line-height:var(--line-height-body)}
.point-list summary>span::before{content:'▸';display:inline-block;margin-right:var(--space-2);color:var(--color-text-muted)}.point-list details[open]>summary>span::before{content:'▾'}
.point-state{font-style:normal;font-size:var(--font-size-caption);color:var(--color-success);white-space:nowrap}
.point-state.is-not_met{color:var(--color-danger)}.point-state.is-uncertain,.point-state.is-unreadable{color:var(--color-warning)}
.point-evidence{margin-top:var(--space-2)!important;padding:var(--space-2) var(--space-3);background:var(--color-bg-subtle);border-radius:var(--radius-control)}
.point-review-form{display:grid;gap:var(--space-2);padding:var(--space-3);margin-top:var(--space-2);border:1px solid var(--color-border-subtle);border-radius:var(--radius-control)}
.point-review-form label{display:grid;grid-template-columns:90px minmax(0,1fr);align-items:center;gap:var(--space-2);font-size:var(--font-size-dense);color:var(--color-text-secondary)}
.point-review-form button{justify-self:end}
.answer-preview{position:sticky;top:var(--space-4);min-width:0;border:1px solid var(--color-border-default);border-radius:var(--radius-control);overflow:hidden;background:var(--color-bg-subtle)}
.answer-preview>header{display:flex;justify-content:space-between;align-items:center;padding:var(--space-3);font-size:var(--font-size-dense)}
.answer-preview a{color:var(--color-accent)}
.answer-preview img{display:block;width:100%;max-height:62vh;object-fit:contain;background:white}
.answer-preview nav{display:flex;flex-wrap:wrap;gap:var(--space-2);padding:var(--space-3)}
.answer-preview nav button{padding:var(--space-1) var(--space-2);border:1px solid var(--color-border-default);border-radius:var(--radius-control);background:var(--color-bg-surface);font:inherit;font-size:var(--font-size-caption);cursor:pointer}
.answer-preview nav button[aria-pressed=true]{background:var(--color-accent-subtle);color:var(--color-accent);border-color:var(--color-accent)}
.ledger-publish{display:flex;align-items:center;justify-content:space-between;gap:var(--space-4);padding:0 0 var(--space-4);margin-bottom:var(--space-3);border-bottom:1px solid var(--color-border-default)}
.ledger-publish>span{font-size:var(--font-size-dense);color:var(--color-text-secondary)}
.ledger-publish small{display:block;color:var(--color-warning)}
.feedback-sheet>header{flex-wrap:wrap;margin-bottom:var(--space-4)}
.feedback-sheet h4{font-size:var(--font-size-h3)}
.feedback-sheet header>span{margin-left:auto}
.mastery-table,.aggregate-feedback{min-width:0;overflow-x:auto}
.feedback-sheet table{width:100%;border-collapse:collapse;font-size:var(--font-size-dense)}
.feedback-sheet th,.feedback-sheet td{padding:var(--space-3);text-align:left;border-bottom:1px solid var(--color-border-subtle)}
.feedback-sheet th{color:var(--color-text-muted);font-weight:500;background:var(--color-bg-subtle)}
.feedback-sheet td:nth-child(n+2){width:90px;font-variant-numeric:tabular-nums;white-space:nowrap}
.feedback-sheet td:nth-child(3){font-weight:600}
.feedback-sheet td summary{cursor:pointer;line-height:var(--line-height-body)}
.feedback-sheet td p{font-size:var(--font-size-caption);color:var(--color-text-secondary);margin-top:var(--space-2)}
.aggregate-feedback{margin-top:var(--space-3);font-size:var(--font-size-dense);color:var(--color-text-secondary)}
.aggregate-feedback>summary,.feedback-maintenance>summary{cursor:pointer}
.next-round-card{display:flex;align-items:center;justify-content:space-between;gap:var(--space-4);margin-top:var(--space-5);padding-top:var(--space-4);border-top:1px solid var(--color-border-default)}
.next-round-card p{margin-top:var(--space-1)}
.feedback-maintenance{margin-top:var(--space-4);font-size:var(--font-size-caption);color:var(--color-text-muted)}
.feedback-maintenance button{margin-top:var(--space-2)}
.training-notice,.training-error{margin-top:var(--space-3)!important;font-size:var(--font-size-dense)}
.training-error{color:var(--color-danger)}
@media(max-width:1050px){.review-workspace:has(.answer-preview){grid-template-columns:minmax(0,1fr)}.answer-preview{position:static}.answer-preview img{max-height:48vh}}
@media(max-width:760px){.assessment-ledger{padding:var(--space-4)}.ledger-heading,.ledger-start,.ledger-publish,.next-round-card{align-items:flex-start;flex-wrap:wrap}.point-list summary{gap:var(--space-2)}.feedback-sheet th,.feedback-sheet td{padding:var(--space-2)}.feedback-sheet td:nth-child(n+2){width:58px}.point-review-form label{grid-template-columns:1fr}.ledger-publish button{width:100%}}
</style>
