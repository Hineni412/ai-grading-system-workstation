<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import {
  trainingApi,
  type TrainingAssessmentOutcome,
  type TrainingAssessmentPoint,
  type TrainingFeedback,
  type TrainingPointState,
  type TrainingSubmission,
} from '../../api/training'
import { ApiError, isAmbiguousWriteError } from '../../api/errors'
import StatusBadge from '../design-system/StatusBadge.vue'

const props = defineProps<{
  submission: TrainingSubmission
}>()

const emit = defineEmits<{
  openDraft: [draftId: string]
}>()

interface PointEdit {
  state: TrainingPointState
  evidence: string
  reason: string
}

const assessment = ref<TrainingAssessmentOutcome | null>(null)
const feedback = ref<TrainingFeedback | null>(null)
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
  if (value.workflow_status === 'complete') return '复核完成'
  return '需要复核'
}

function statusTone(value: TrainingAssessmentOutcome): 'info' | 'success' | 'warning' | 'danger' {
  if (value.status === 'failed' || value.status === 'cancelled') return 'danger'
  if (value.status === 'running') return 'info'
  if (value.workflow_status === 'complete') return 'success'
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
  return String(item.display_name || item.stable_key || '训练目标')
}

function masteryValue(
  item: Record<string, unknown>,
  key: 'v2_before' | 'v2_after',
): string {
  const snapshot = item[key]
  if (
    !snapshot
    || typeof snapshot !== 'object'
    || !('value' in snapshot)
    || typeof snapshot.value !== 'number'
  ) {
    return '暂无'
  }
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
      <div>
        <span class="ledger-kicker">判定 → 复核 → 证据</span>
        <h5>训练结果闭环</h5>
      </div>
      <StatusBadge
        v-if="assessment"
        class="ledger-status"
        :tone="statusTone(assessment)"
        :label="statusLabel(assessment)"
      />
    </header>

    <div v-if="!assessment" class="ledger-start">
      <p>
        页面已齐全。开始后会把这名学生的整卷合并为
        <strong>1 次模型请求</strong>，失败不会自动追加请求。
      </p>
      <button
        type="button"
        class="training-button"
        :disabled="Boolean(busy) || waitingForResult"
        @click="startAssessment"
      >
        {{ busy === 'restore' ? '正在读取判定…' : waitingForResult ? '正在等待后台结果…' : busy === 'assess' ? '正在判定…' : '开始整卷判定（1 次请求）' }}
      </button>
    </div>

    <button v-if="queryPaused" type="button" class="training-link" :disabled="Boolean(busy)" @click="queryResult">重新查询结果</button>

    <template v-if="assessment">
      <div class="ledger-summary">
        <span>{{ assessment.expected_question_count }} 题</span>
        <span>{{ assessment.expected_point_count }} 个判定点</span>
        <span>已请求 {{ assessment.request_count }} 次</span>
        <span v-if="assessment.usage.total_tokens">
          本次 {{ assessment.usage.total_tokens.toLocaleString() }} tokens
        </span>
      </div>

      <p v-if="assessment.action_message" class="ledger-action">
        {{ assessment.action_message }}
      </p>

      <div v-if="assessment.status === 'failed'" class="ledger-recovery">
        <button
          type="button"
          class="training-link"
          :disabled="Boolean(busy)"
          @click="assessmentAction('retry')"
        >
          {{ busy === 'retry' ? '正在重试…' : '教师确认后重试一次' }}
        </button>
      </div>
      <div v-else-if="assessment.status === 'running'" class="ledger-recovery">
        <button
          type="button"
          class="training-link"
          :disabled="Boolean(busy)"
          @click="assessmentAction('recover')"
        >
          接管中断状态
        </button>
      </div>

      <ol class="question-ledger">
        <li
          v-for="question in assessment.questions"
          :key="question.task_item_code"
        >
          <header>
            <div>
              <strong>第 {{ question.item_order }} 题</strong>
              <small>{{ question.task_item_code }}</small>
            </div>
            <span>
              {{ question.met_count }}/{{ question.total_count }} 个点达成
            </span>
          </header>
          <div class="point-list">
            <details
              v-for="point in question.review_points"
              :key="point.point_id"
              :open="point.state === 'uncertain' || point.state === 'unreadable' || !point.state"
            >
              <summary>
                <span>{{ point.content }}</span>
                <em :class="`point-state is-${point.state || 'uncertain'}`">
                  {{ stateLabel(point.state) }}
                  {{ point.teacher_locked ? ' · 教师已锁定' : '' }}
                </em>
              </summary>
              <p v-if="point.evidence">{{ point.evidence }}</p>
              <div class="point-review-form">
                <label>
                  最终判定
                  <select
                    :value="editValue(question.task_item_code, point).state"
                    :disabled="Boolean(busy)"
                    @change="setEditField(question.task_item_code, point, 'state', $event)"
                  >
                    <option value="met">已达成</option>
                    <option value="not_met">未达成</option>
                    <option value="uncertain">不确定</option>
                    <option value="unreadable">无法辨认</option>
                  </select>
                </label>
                <label>
                  教师核对依据
                  <input
                    :value="editValue(question.task_item_code, point).evidence"
                    maxlength="500"
                    :disabled="Boolean(busy)"
                    @input="setEditField(question.task_item_code, point, 'evidence', $event)"
                  >
                </label>
                <label>
                  锁定原因
                  <input
                    :value="editValue(question.task_item_code, point).reason"
                    maxlength="500"
                    :disabled="Boolean(busy)"
                    @input="setEditField(question.task_item_code, point, 'reason', $event)"
                  >
                </label>
                <button
                  type="button"
                  class="training-link"
                  :disabled="Boolean(busy)"
                  @click="savePoint(question.task_item_code, point)"
                >
                  {{ busy === pointKey(question.task_item_code, point.point_id) ? '正在保存…' : '锁定此判定点' }}
                </button>
              </div>
            </details>
          </div>
        </li>
      </ol>

      <div class="ledger-publish">
        <div>
          <strong>发布逐题训练证据</strong>
          <p>不确定和无法辨认项不会补成“未达成”；这里不产生考试总分或排名。</p>
        </div>
        <div>
          <button
            type="button"
            class="training-button"
            :disabled="Boolean(busy) || !canPublish"
            @click="syncEvidence('publish')"
          >
            {{ busy === 'publish' ? '正在发布…' : '发布已完成题目' }}
          </button>
          <button
            v-if="feedback && feedback.status !== 'withdrawn'"
            type="button"
            class="training-link is-danger"
            :disabled="Boolean(busy)"
            @click="syncEvidence('withdraw')"
          >
            撤回本次证据
          </button>
        </div>
      </div>

      <section v-if="feedback" class="feedback-sheet" aria-label="学生训练反馈">
        <header>
          <div>
            <span class="ledger-kicker">学生个人反馈</span>
            <h6>{{ feedback.summary.message }}</h6>
          </div>
          <strong>
            {{ feedback.summary.published_question_count }}/{{ feedback.summary.total_question_count }}
            题已发布
          </strong>
        </header>

        <button
          v-if="feedback.status === 'publication_pending'"
          type="button"
          class="training-link"
          :disabled="Boolean(busy)"
          @click="replayEvidence"
        >
          {{ busy === 'replay' ? '正在补发…' : '安全补发未完成证据' }}
        </button>

        <div v-if="feedback.mastery_changes.length" class="mastery-strip">
          <article
            v-for="item in feedback.mastery_changes"
            :key="String(item.stable_key)"
          >
            <strong>{{ masteryTitle(item) }}</strong>
            <span>
              {{ masteryValue(item, 'v2_before') }}
              <b aria-hidden="true">→</b>
              {{ masteryValue(item, 'v2_after') }}
            </span>
            <small>{{ String(item.reason || '') }}</small>
          </article>
        </div>

        <div class="next-round-card">
          <div>
            <strong>下一轮：{{ feedback.next_round.status === 'draft' ? '草稿待确认' : '暂未生成草稿' }}</strong>
            <p>{{ feedback.next_round.message }}</p>
          </div>
          <button
            v-if="feedback.next_round.draft_id"
            type="button"
            class="training-button is-secondary"
            @click="emit('openDraft', feedback.next_round.draft_id)"
          >
            打开下一轮草稿
          </button>
        </div>
        <p class="feedback-safety">
          下一轮不会自动冻结、打印或发送；只有教师确认后，才会生成正式训练卷。
        </p>
      </section>
    </template>

    <p v-if="message" class="training-notice">{{ message }}</p>
    <p v-if="errorMessage" class="training-error" role="alert">{{ errorMessage }}</p>
  </section>
</template>

<style scoped>
.assessment-ledger {
  margin-top: 0.75rem;
  padding: 0.85rem;
  border: 1px solid var(--color-border-default);
  border-left: 4px solid var(--color-success);
  border-radius: var(--radius-control);
  background: var(--color-bg-subtle);
}

.ledger-heading,
.question-ledger li > header,
.ledger-publish,
.feedback-sheet > header,
.next-round-card {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.85rem;
}

.ledger-kicker {
  display: block;
  margin-bottom: 0.15rem;
  color: var(--color-success);
  font-size: 0.68rem;
  font-weight: 800;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.assessment-ledger h5,
.assessment-ledger h6,
.assessment-ledger p {
  margin: 0;
}

.ledger-status {
  flex: 0 0 auto;
}

.ledger-start,
.ledger-publish,
.feedback-sheet {
  margin-top: 0.75rem;
}

.ledger-start p,
.ledger-action,
.ledger-publish p,
.feedback-sheet p,
.point-list p {
  color: var(--color-text-secondary);
  font-size: 0.84rem;
}

.ledger-start .training-button {
  margin-top: 0.65rem;
}

.ledger-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  margin-top: 0.7rem;
}

.ledger-summary span {
  padding: 0.25rem 0.45rem;
  border: 1px solid var(--color-border-default);
  border-radius: 4px;
  background: var(--color-bg-surface);
  color: var(--color-text-secondary);
  font-size: 0.75rem;
  font-variant-numeric: tabular-nums;
}

.ledger-action,
.ledger-recovery {
  margin-top: 0.65rem !important;
}

.question-ledger {
  display: grid;
  gap: 0.65rem;
  margin: 0.85rem 0 0;
  padding: 0;
  list-style: none;
}

.question-ledger > li {
  padding: 0.7rem;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-bg-surface);
}

.question-ledger header div,
.point-list,
.point-review-form {
  display: grid;
  gap: 0.45rem;
}

.question-ledger header small {
  color: var(--color-text-muted);
  font-size: 0.68rem;
  letter-spacing: 0.04em;
}

.question-ledger header > span {
  color: var(--color-success);
  font-size: 0.8rem;
  font-weight: 700;
}

.point-list {
  margin-top: 0.55rem;
}

.point-list details {
  padding-top: 0.45rem;
  border-top: 1px dashed var(--color-border-default);
}

.point-list summary {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 0.65rem;
  cursor: pointer;
}

.point-state {
  color: var(--color-success);
  font-size: 0.75rem;
  font-style: normal;
  font-weight: 700;
}

.point-state.is-uncertain,
.point-state.is-unreadable {
  color: var(--color-warning);
}

.point-review-form {
  margin-top: 0.55rem;
  padding: 0.65rem;
  border-radius: calc(var(--radius) - 2px);
  background: var(--color-bg-subtle);
}

.point-review-form label {
  display: grid;
  grid-template-columns: minmax(92px, 0.24fr) minmax(0, 1fr);
  align-items: center;
  gap: 0.55rem;
  color: var(--color-text-secondary);
  font-size: 0.78rem;
}

.point-review-form input,
.point-review-form select {
  min-width: 0;
  padding: 0.42rem;
}

.ledger-publish {
  padding: 0.75rem;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-success-subtle);
}

.ledger-publish > div:last-child {
  display: flex;
  flex: 0 0 auto;
  gap: 0.5rem;
}

.training-link.is-danger {
  color: var(--color-danger);
}

.feedback-sheet {
  padding: 0.8rem;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-control);
  background: var(--color-warning-subtle);
}

.feedback-sheet h6 {
  font-size: 0.95rem;
}

.feedback-sheet > header > strong {
  color: var(--color-warning);
  font-size: 0.8rem;
  font-variant-numeric: tabular-nums;
}

.mastery-strip {
  display: grid;
  gap: 0.45rem;
  margin-top: 0.7rem;
}

.mastery-strip article {
  display: grid;
  grid-template-columns: minmax(120px, 0.8fr) auto minmax(180px, 1.5fr);
  align-items: center;
  gap: 0.6rem;
  padding-top: 0.45rem;
  border-top: 1px solid var(--color-border-default);
}

.mastery-strip span {
  font-variant-numeric: tabular-nums;
}

.mastery-strip b {
  padding: 0 0.2rem;
  color: var(--color-success);
}

.mastery-strip small {
  color: var(--color-text-muted);
}

.next-round-card {
  margin-top: 0.75rem;
  padding-top: 0.7rem;
  border-top: 2px solid var(--color-success);
}

.feedback-safety,
.training-notice,
.training-error {
  margin-top: 0.65rem !important;
}

@media (max-width: 720px) {
  .ledger-heading,
  .question-ledger li > header,
  .ledger-publish,
  .feedback-sheet > header,
  .next-round-card {
    align-items: stretch;
    flex-direction: column;
  }

  .point-list summary,
  .point-review-form label,
  .mastery-strip article {
    grid-template-columns: 1fr;
  }

  .ledger-publish > div:last-child {
    align-items: stretch;
    flex-direction: column;
  }
}
</style>
