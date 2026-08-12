<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import {
  confirmReviewItem,
  fetchReviewRubric,
  type ReviewConfirmInput,
  type ReviewItemLike,
  type ReviewRubricSection,
} from '../../api/review'
import { ApiError } from '../../api/errors'
import {
  reviewDraftRevision,
  scoreIssue,
  useReviewDraftStore,
  type ReviewDraft,
} from '../../stores/review-drafts'
import { useReviewQueueStore } from '../../stores/review-queue'
import { translateGradingReason } from '../../utils/grading-reasons'
import StatePanel from '../design-system/StatePanel.vue'
import StatusBadge from '../design-system/StatusBadge.vue'
import ReviewFeedbackToast from './ReviewFeedbackToast.vue'

const reviewStore = useReviewQueueStore()
const draftStore = useReviewDraftStore()
const props = defineProps<{
  registerAnnotationRetry: (entry: {
    input: ReviewConfirmInput
    item: ReviewItemLike
  }) => void
}>()
const rubric = ref<ReviewRubricSection | null>(null)
const rubricState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const currentDraft = ref<ReviewDraft | null>(null)
const submitting = ref(false)
const feedback = ref('')
const feedbackTone = ref<'success' | 'warning' | 'error'>('success')
const rubricCache = new Map<string, ReviewRubricSection | null>()
let rubricController: AbortController | null = null
let rubricGeneration = 0

const emit = defineEmits<{
  confirmed: [payload: {
    reviewItemId: string
    annotationRetry: boolean
  }]
}>()

const item = computed(() => reviewStore.currentItem)
const rubricTargetKey = computed(() => {
  const current = item.value
  return current
    ? rubricCacheKey(current.session_id, current.question_id)
    : null
})
const issue = computed(() => {
  if (!item.value || !currentDraft.value) return null
  return scoreIssue(currentDraft.value.scoreText, item.value.max_score)
})
const submitDisabled = computed(() =>
  !item.value ||
  !currentDraft.value ||
  issue.value !== null ||
  submitting.value,
)
const disabledReason = computed(() => {
  if (!item.value) return '请先选择一条复核记录'
  if (issue.value) return issue.value
  if (submitting.value) return '正在确认当前评分'
  return ''
})
const evidenceSteps = computed(() => stringList(item.value?.metadata.evidence_steps))
const missingSteps = computed(() => stringList(item.value?.metadata.missing_steps))
const hasAiAssessment = computed(() => item.value?.score_source === 'ai')
const statusLabel = computed(() => {
  const status = item.value?.score_status
  if (!status) return ''
  return {
    ungraded: '待人工评分',
    ai_ready: 'AI 已完成',
    ai_review: 'AI 待复核',
    teacher_final: '教师已确认',
    failed: '处理失败',
  }[status]
})
const statusTone = computed(() => {
  const status = item.value?.score_status
  if (!status) return 'neutral' as const
  return ({
    ungraded: 'neutral',
    ai_ready: 'ai',
    ai_review: 'warning',
    teacher_final: 'teacher',
    failed: 'danger',
  } as const)[status]
})
const candidates = computed(() => (item.value?.candidate_scores ?? []).flatMap((candidate) => {
  const score = typeof candidate.score === 'number' && Number.isFinite(candidate.score)
    ? candidate.score
    : null
  const confidence = typeof candidate.confidence === 'number' && Number.isFinite(candidate.confidence)
    ? candidate.confidence
    : null
  const reason = typeof candidate.reason === 'string' ? candidate.reason.trim() : ''
  if (score === null && !reason) return []
  return [{ score, confidence, reason }]
}))

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((entry) => {
    if (typeof entry !== 'string') return []
    const text = entry.trim()
    return text ? [text] : []
  })
}

function formatScore(value: number | null): string {
  return value !== null && Number.isFinite(value) ? String(value) : '—'
}

function formatConfidence(value: number | null): string {
  if (value === null) return '未提供'
  return value <= 1 ? `${Math.round(value * 100)}%` : `${Math.round(value)}%`
}

function rubricCacheKey(sessionId: number, questionId: string): string {
  return `${sessionId}\u0000${questionId.trim()}`
}

function updateScore(event: Event): void {
  if (!currentDraft.value) return
  draftStore.updateScore(currentDraft.value.key, (event.target as HTMLInputElement).value)
}

function selectScore(event: FocusEvent): void {
  ;(event.currentTarget as HTMLInputElement).select()
}

async function submitCurrent(): Promise<void> {
  const submittedItem = item.value
  const draft = currentDraft.value
  if (!submittedItem || !draft || submitDisabled.value || submitting.value) return

  const submittedQuestionId = submittedItem.question_id
  const submittedSessionId = submittedItem.session_id
  const score = Number(draft.scoreText.trim())
  const note = draft.note.trim()
  const confirmInput: ReviewConfirmInput = {
    review_item_id: submittedItem.review_item_id,
    expected_revision: reviewDraftRevision(draft),
    student_id: submittedItem.student_id,
    result_id: submittedItem.result_id,
    detail_id: submittedItem.detail_id,
    score_awarded: score,
    ...(note ? { deduction_reason: note } : {}),
  }

  submitting.value = true
  feedback.value = ''
  try {
    const response = await confirmReviewItem(
      submittedSessionId,
      submittedQuestionId,
      confirmInput,
    )
    draftStore.markConfirmed(draft.key)
    const annotationRetry = response.annotation_outcomes.some(
      (outcome) => outcome.status === 'retry_required',
    )
    if (annotationRetry) props.registerAnnotationRetry({ input: confirmInput, item: submittedItem })
    emit('confirmed', {
      reviewItemId: submittedItem.review_item_id,
      annotationRetry,
    })
    feedbackTone.value = annotationRetry ? 'warning' : 'success'
    feedback.value = annotationRetry
      ? '分数已确认，标注图需要稍后刷新。'
      : '教师最终分已确认。'
  } catch (error) {
    feedbackTone.value = 'error'
    feedback.value = error instanceof ApiError && error.kind === 'conflict'
      ? '这份答卷已在别处更新。教师草稿仍保留，请返回批量页刷新后再确认。'
      : '确认失败，教师草稿已保留。请检查网络后重试。'
  } finally {
    submitting.value = false
  }
}

async function loadRubric(sessionId: number, questionId: string): Promise<void> {
  rubricController?.abort()
  const generation = ++rubricGeneration
  const cacheKey = rubricCacheKey(sessionId, questionId)
  rubric.value = null
  if (rubricCache.has(cacheKey)) {
    rubric.value = rubricCache.get(cacheKey) ?? null
    rubricState.value = 'ready'
    rubricController = null
    return
  }

  const controller = new AbortController()
  rubricController = controller
  rubricState.value = 'loading'
  try {
    const loaded = await fetchReviewRubric(sessionId, questionId, controller.signal)
    if (generation !== rubricGeneration || controller.signal.aborted) return
    rubricCache.set(cacheKey, loaded)
    rubric.value = loaded
    rubricState.value = 'ready'
  } catch {
    if (generation !== rubricGeneration || controller.signal.aborted) return
    rubricState.value = 'error'
  } finally {
    if (rubricController === controller) rubricController = null
  }
}

watch(
  item,
  (next) => {
    if (!next) {
      currentDraft.value = null
      return
    }
    currentDraft.value = draftStore.ensureDraft(next)
  },
  { immediate: true },
)

watch(
  rubricTargetKey,
  (next) => {
    const current = item.value
    if (!next || !current) {
      rubricGeneration += 1
      rubricController?.abort()
      rubricController = null
      rubric.value = null
      rubricState.value = 'idle'
      return
    }
    void loadRubric(current.session_id, current.question_id)
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  rubricGeneration += 1
  rubricController?.abort()
  rubricCache.clear()
})
</script>

<template>
  <section
    class="review-scoring-inspector"
    data-testid="review-scoring-inspector"
    aria-label="评分与复核检查器"
  >
    <ReviewFeedbackToast
      :message="feedback"
      :tone="feedbackTone"
      @dismiss="feedback = ''"
    />
    <StatePanel
      v-if="!item || !currentDraft"
      kind="empty"
      title="请选择复核记录"
      description="从左侧队列选择一名学生后，可在这里核对并确认教师最终分。"
    />

    <template v-else>
      <div class="review-scoring-inspector__scroll" data-testid="scoring-scroll-region">
        <header class="review-scoring-inspector__header">
          <p>{{ item.question_id }} · 满分 {{ formatScore(item.max_score) }}</p>
          <h2>评分与复核</h2>
          <StatusBadge
            v-if="currentDraft.dirty"
            class="review-scoring-inspector__draft-state"
            tone="warning"
            label="教师草稿未确认"
          />
          <StatusBadge
            v-else
            class="review-scoring-inspector__status-state"
            :tone="statusTone"
            :label="statusLabel"
          />
        </header>

        <section class="review-scoring-section" aria-labelledby="review-rubric-title">
          <h3 id="review-rubric-title">评分标准</h3>
          <p v-if="rubricState === 'loading'" class="review-scoring-section__muted">正在读取当前题评分标准…</p>
          <p v-else-if="rubricState === 'error'" class="review-scoring-section__warning">
            评分标准暂时无法读取，不影响查看和编辑当前分数。
          </p>
          <p v-else-if="rubricState === 'ready' && !rubric" class="review-scoring-section__warning">
            当前题没有可展示的评分标准，请以原始证据和既有满分为准。
          </p>
          <template v-else-if="rubric">
            <p class="review-scoring-section__meta">
              {{ rubric.question_id }} · {{ formatScore(rubric.max_score) }} 分
              <span v-if="rubric.question_type"> · {{ rubric.question_type }}</span>
            </p>
            <ul v-if="rubric.knowledge_labels.length" class="review-scoring-tags" aria-label="知识点">
              <li v-for="label in rubric.knowledge_labels" :key="label">{{ label }}</li>
            </ul>
            <div
              v-if="rubric.points.length"
              class="review-rubric-points"
              data-testid="review-rubric-points"
            >
              <article
                v-for="point in rubric.points"
                :key="`${point.part_id}:${point.step_id}`"
                class="review-rubric-point"
              >
                <header class="review-rubric-point__header">
                  <strong>{{ point.core_goal || point.part_label }}</strong>
                  <span>{{ formatScore(point.score) }} 分</span>
                </header>
                <dl class="review-rubric-point__details">
                  <template v-if="point.standard_answer">
                    <dt>标准答案</dt>
                    <dd>{{ point.standard_answer }}</dd>
                  </template>
                  <template v-if="point.accepted_answers.length">
                    <dt>等价答案</dt>
                    <dd>{{ point.accepted_answers.join('；') }}</dd>
                  </template>
                  <template v-if="point.match_rule">
                    <dt>匹配规则</dt>
                    <dd>{{ point.match_rule }}</dd>
                  </template>
                  <template v-if="point.required_elements.length">
                    <dt>证据要求</dt>
                    <dd>{{ point.required_elements.join('；') }}</dd>
                  </template>
                  <template v-if="point.deduction_rules.length">
                    <dt>扣分规则</dt>
                    <dd>{{ point.deduction_rules.join('；') }}</dd>
                  </template>
                  <template v-if="point.answer_only_max_score !== null">
                    <dt>仅有答案</dt>
                    <dd>最高 {{ formatScore(point.answer_only_max_score) }} 分</dd>
                  </template>
                  <template v-if="point.require_final_answer !== null">
                    <dt>最终答案</dt>
                    <dd>{{ point.require_final_answer ? '必须明确写出' : '不要求单独写出' }}</dd>
                  </template>
                  <template v-if="point.final_answer_rule">
                    <dt>结论规则</dt>
                    <dd>{{ point.final_answer_rule }}</dd>
                  </template>
                </dl>
              </article>
            </div>
            <p v-else class="review-scoring-section__muted">评分配置只提供了本题满分，暂无更细步骤。</p>
          </template>
        </section>

        <section
          v-if="hasAiAssessment"
          class="review-scoring-section review-scoring-section--ai"
          aria-labelledby="review-ai-title"
        >
          <h3 id="review-ai-title">AI 初评</h3>
          <div class="review-ai-score">
            <strong>{{ formatScore(item.score_awarded) }}</strong>
            <span>/ {{ formatScore(item.max_score) }} 分</span>
          </div>
          <p>置信度：{{ formatConfidence(item.confidence_score) }}</p>
          <ul v-if="evidenceSteps.length" class="review-evidence-list">
            <li v-for="entry in evidenceSteps" :key="entry">已有证据：{{ entry }}</li>
          </ul>
          <ul v-if="missingSteps.length" class="review-evidence-list review-evidence-list--warning">
            <li v-for="entry in missingSteps" :key="entry">缺失步骤：{{ entry }}</li>
          </ul>
          <details v-if="candidates.length">
            <summary>查看其他 AI 候选</summary>
            <ul class="review-candidate-list">
              <li v-for="(candidate, index) in candidates" :key="index">
                <strong v-if="candidate.score !== null">{{ formatScore(candidate.score) }} 分</strong>
                <span v-if="candidate.confidence !== null"> · {{ formatConfidence(candidate.confidence) }}</span>
                <p v-if="candidate.reason">{{ candidate.reason }}</p>
              </li>
            </ul>
          </details>
        </section>
        <section
          v-else
          class="review-scoring-section review-scoring-section--manual"
          aria-labelledby="review-manual-title"
        >
          <h3 id="review-manual-title">教师干预</h3>
          <p>这份答卷没有 AI 初评分数或 AI 标注，请直接参考左侧原卷与裁图评分。</p>
        </section>

        <section class="review-scoring-section" aria-labelledby="review-risk-title">
          <h3 id="review-risk-title">风险与错因</h3>
          <p v-if="item.score_status === 'ungraded'" class="review-scoring-section__warning">
            当前记录尚未评分。
          </p>
          <p v-else-if="item.score_status === 'ai_review'" class="review-scoring-section__warning">
            当前 AI 结果需要教师复核。
          </p>
          <p v-else-if="item.score_status === 'failed'" class="review-scoring-section__warning">
            自动处理失败，请教师直接评分。
          </p>
          <p v-else class="review-scoring-section__muted">当前状态：{{ statusLabel }}。</p>
          <dl class="review-risk-list">
            <template v-if="item.deduction_reason"><dt>扣分原因</dt><dd>{{ translateGradingReason(item.deduction_reason) }}</dd></template>
            <template v-if="item.error_category"><dt>错误类别</dt><dd>{{ translateGradingReason(item.error_category) }}</dd></template>
            <template v-if="item.error_summary"><dt>错误摘要</dt><dd>{{ translateGradingReason(item.error_summary) }}</dd></template>
          </dl>
        </section>

      </div>

      <section
        class="review-scoring-section review-scoring-section--teacher"
        aria-labelledby="review-teacher-title"
        data-testid="teacher-score-panel"
      >
        <h3 id="review-teacher-title">教师最终分</h3>
        <label for="teacher-score">最终得分</label>
        <div class="review-teacher-score-field">
          <input
            id="teacher-score"
            data-testid="teacher-score"
            type="number"
            inputmode="decimal"
            min="0"
            :max="item.max_score"
            step="any"
            :value="currentDraft.scoreText"
            :aria-invalid="issue ? 'true' : 'false'"
            aria-describedby="teacher-score-help teacher-score-error"
            @input="updateScore"
            @focus="selectScore"
          >
          <span>/ {{ formatScore(item.max_score) }} 分</span>
        </div>
        <p id="teacher-score-help" class="review-scoring-section__muted">
          {{ hasAiAssessment ? '教师确认结果将覆盖 AI 初评。' : '待人工评分答卷必须填写分数后才能确认。' }}
        </p>
        <p v-if="issue" id="teacher-score-error" class="review-field-error">{{ issue }}</p>
      </section>

      <footer class="review-scoring-inspector__footer" data-testid="scoring-footer">
        <p v-if="draftStore.dirtyCount > 0">本机内存中有 {{ draftStore.dirtyCount }} 条未确认草稿。</p>
        <button
          type="button"
          data-testid="confirm-single"
          :disabled="submitDisabled"
          :title="disabledReason"
          @click="submitCurrent"
        >
          {{ submitting ? '正在确认…' : '确认此份并返回' }}
        </button>
      </footer>
    </template>
  </section>
</template>
