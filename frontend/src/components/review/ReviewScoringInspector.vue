<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'

import {
  confirmReviewItem,
  type ReviewConfirmInput,
  type ReviewItemLike,
  type ReviewRubricPoint,
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
import { displayErrorCategory, translateGradingReason } from '../../utils/grading-reasons'
import {
  cachedReviewRubric,
  clearReviewRubrics,
  hasReviewRubric,
  loadReviewRubric,
  reviewRubricCacheKey,
} from './review-rubric-cache'
import QuestionHtmlBlock from '../question-bank/QuestionHtmlBlock.vue'
import StatePanel from '../design-system/StatePanel.vue'
import StatusBadge from '../design-system/StatusBadge.vue'
import ReviewFeedbackToast from './ReviewFeedbackToast.vue'

const reviewStore = useReviewQueueStore()
const draftStore = useReviewDraftStore()
const props = withDefaults(
  defineProps<{
    registerAnnotationRetry: (entry: {
      input: ReviewConfirmInput
      item: ReviewItemLike
    }) => void
    stayAfterConfirm?: boolean
  }>(),
  { stayAfterConfirm: false },
)
const rubric = ref<ReviewRubricSection | null>(null)
const rubricState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const currentDraft = ref<ReviewDraft | null>(null)
const submitting = ref(false)
const feedback = ref('')
const feedbackTone = ref<'success' | 'warning' | 'error'>('success')
const stepModeNotice = ref('')
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
    ? reviewRubricCacheKey(current.session_id, current.question_id)
    : null
})
const issue = computed(() => {
  if (!item.value || !currentDraft.value) return null
  return scoreIssue(currentDraft.value.scoreText, item.value.max_score)
})
const submitDisabled = computed(() =>
  !item.value ||
  !currentDraft.value ||
  quickInvalid.size > 0 ||
  issue.value !== null ||
  (currentDraft.value?.stepScores?.some((step) => scoreIssue(step.scoreText, step.maxScore) !== null) ?? false) ||
  submitting.value,
)
const disabledReason = computed(() => {
  if (!item.value) return '请先选择一条复核记录'
  if (quickInvalid.size > 0) return '请先修正标红的步骤分'
  if (issue.value) return issue.value
  if (submitting.value) return '正在确认当前评分'
  return ''
})
const evidenceSteps = computed(() => stringList(item.value?.metadata.evidence_steps))
const missingSteps = computed(() => stringList(item.value?.metadata.missing_steps))
const stepAssessments = computed(() => {
  const raw = item.value?.metadata.step_assessments
  if (!Array.isArray(raw)) return []
  return raw.flatMap((entry) => {
    if (!entry || typeof entry !== 'object') return []
    const record = entry as Record<string, unknown>
    const stepId = typeof record.step_id === 'string' ? record.step_id.trim() : ''
    if (!stepId) return []
    const score = typeof record.score_awarded === 'number' && Number.isFinite(record.score_awarded)
      ? record.score_awarded
      : null
    const achievement = typeof record.achievement === 'string' ? record.achievement.trim() : ''
    const reason = typeof record.reason === 'string' ? record.reason.trim() : ''
    const studentEvidence = typeof record.student_evidence === 'string' ? record.student_evidence.trim() : ''
    const missingOrError = typeof record.missing_or_error === 'string' ? record.missing_or_error.trim() : ''
    const partId = typeof record.part_id === 'string' ? record.part_id.trim() : ''
    return [{ stepId, partId, score, achievement, reason, studentEvidence, missingOrError }]
  })
})
const canReviewSteps = computed(() => Boolean(item.value?.result_id && item.value?.detail_id
  && !item.value.review_item_id.startsWith('legacy:') && rubric.value?.points.length
  && rubric.value.points.every((point) => point.step_id && Number.isInteger(point.score) && point.score > 0)
  && rubric.value.points.reduce((total, point) => total + point.score, 0) === item.value.max_score))

function beginStepReview(): void {
  if (!currentDraft.value || !rubric.value || !canReviewSteps.value) return
  const steps = rubric.value.points.map((point) => {
    const matches = stepAssessments.value.filter((step) => step.stepId === point.step_id
      && (!step.partId || step.partId === point.part_id))
    return { partId: point.part_id, stepId: point.step_id, maxScore: point.score,
      scoreText: matches.length === 1 && matches[0]?.score != null ? String(matches[0].score) : '' }
  })
  const matchesCurrentTotal = steps.every((step) => scoreIssue(step.scoreText, step.maxScore) === null)
    && steps.reduce((total, step) => total + Number(step.scoreText), 0) === Number(currentDraft.value.scoreText)
  stepModeNotice.value = matchesCurrentTotal ? '' : 'AI 步骤分与总分不一致，请逐步给分'
  draftStore.initSteps(currentDraft.value.key, matchesCurrentTotal ? steps
    : steps.map((step) => ({ ...step, scoreText: '' })))
}

function setStepScore(partId: string, stepId: string, value: string): void {
  if (!currentDraft.value?.stepScores) return
  draftStore.updateSteps(currentDraft.value.key, currentDraft.value.stepScores.map((step) =>
    step.partId === partId && step.stepId === stepId ? { ...step, scoreText: value } : step))
}

function stepDraft(partId: string, stepId: string) {
  return currentDraft.value?.stepScores?.find((step) => step.partId === partId && step.stepId === stepId)
}

function stepState(partId: string, stepId: string, maxScore: number): {
  text: string
  tone: 'teacher' | 'warning' | 'muted'
} {
  const value = stepDraft(partId, stepId)?.scoreText ?? ''
  if (value === '') return { text: `—/${formatScore(maxScore)} 待评分`, tone: 'muted' }
  return Number(value) === maxScore
    ? { text: `${value}/${formatScore(maxScore)} 达成`, tone: 'teacher' }
    : { text: `${value}/${formatScore(maxScore)} 未达成`, tone: 'warning' }
}

function hasStepEvidence(point: ReviewRubricPoint): boolean {
  const assessment = stepAssessmentFor(point)
  return Boolean(
    assessment?.reason || assessment?.studentEvidence || assessment?.missingOrError,
  )
}

function stepAssessmentFor(point: ReviewRubricPoint) {
  const matches = stepAssessments.value.filter((step) => step.stepId === point.step_id
    && (!step.partId || step.partId === point.part_id))
  return matches.length === 1 ? matches[0] : null
}

function stepAiVerdict(point: ReviewRubricPoint): string | null {
  const assessment = stepAssessmentFor(point)
  if (!assessment) return null
  const parts: string[] = []
  if (assessment.achievement) parts.push(achievementLabel(assessment.achievement))
  parts.push(`${formatScore(assessment.score)}/${formatScore(point.score)}`)
  return `AI · ${parts.join(' ')}`
}

const achievementLabel = (value: string): string => ({
  full: '完成',
  equivalent: '等价完成',
  partial: '部分完成',
  none: '未完成',
  uncertain: '无法确定（按最优判断给分）',
}[value] ?? value)
const aiScore = computed(() => {
  const original = item.value?.metadata.ai_score_awarded
  if (typeof original === 'number' && Number.isFinite(original)) return original
  return item.value?.score_source === 'ai' ? item.value.score_awarded : null
})
const hasAiAssessment = computed(() => aiScore.value != null || stepAssessments.value.length > 0
  || evidenceSteps.value.length > 0 || missingSteps.value.length > 0)
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


const riskText = computed(() => {
  const current = item.value
  if (!current) return ''
  const parts: string[] = []
  if (current.score_status === 'ungraded') parts.push('当前记录尚未评分')
  else if (current.score_status === 'ai_review') parts.push('当前 AI 结果需要教师复核')
  else if (current.score_status === 'failed') parts.push('自动处理失败，请教师直接评分')
  for (const value of [current.deduction_reason, displayErrorCategory(current.error_category), current.error_summary]) {
    const text = typeof value === 'string' ? translateGradingReason(value).trim() : ''
    if (text) parts.push(text)
  }
  return parts.join('；')
})

// 快捷给分条：逐步骤数字输入，与草稿为同一来源；聚焦时高亮并滚到对应步骤卡。
const quickBar = ref<HTMLElement | null>(null)
const inspectorRoot = ref<HTMLElement | null>(null)
const activeStepKey = ref('')
const quickInvalid = reactive(new Set<string>())
const quickNotice = ref('')

function onQuickStepFocus(step: { partId: string; stepId: string }): void {
  activeStepKey.value = stepKey(step)
  inspectorRoot.value
    ?.querySelector<HTMLElement>(`[data-step-key="${stepKey(step)}"]`)
    ?.scrollIntoView?.({ block: 'nearest' })
}

function onQuickStepBlur(): void {
  activeStepKey.value = ''
}

function focusQuickInput(partId: string, stepId: string, event: MouseEvent): void {
  if ((event.target as HTMLElement).closest('details, summary, input, button, a')) return
  const index = currentDraft.value?.stepScores?.findIndex(
    (step) => step.partId === partId && step.stepId === stepId,
  ) ?? -1
  quickBar.value?.querySelectorAll<HTMLInputElement>('input')[index]?.focus()
}

function stepKey(step: { partId: string; stepId: string }): string {
  return `${step.partId}:${step.stepId}`
}

function quickStepMaxLength(maxScore: number): number {
  // 多留一位让"直接打新数字覆盖旧值"先进入输入框，再由校验归一化或标错。
  return Math.max(1, Math.min(3, String(Math.max(0, maxScore)).length + 1))
}

function onQuickStep(step: { partId: string; stepId: string; maxScore: number }, index: number, event: Event): void {
  const input = event.target as HTMLInputElement
  const raw = input.value.trim()
  const key = stepKey(step)
  quickNotice.value = ''
  // 直接打新数字覆盖旧值时浏览器会得到类似 "02" 的串，按整数归一化后照常接受。
  const normalized = /^\d+$/.test(raw) ? String(Number(raw)) : raw
  if (raw === '' || (/^\d+$/.test(raw) && scoreIssue(normalized, step.maxScore) === null)) {
    quickInvalid.delete(key)
    setStepScore(step.partId, step.stepId, normalized)
    input.value = normalized
    // 当前值仍可能追加成合法的多位数（如满分 12 时先输入 1）就不前进焦点。
    const mayExtend = normalized !== ''
      && String(step.maxScore).length > normalized.length
      && Number(normalized) * 10 <= step.maxScore
    if (normalized !== '' && !mayExtend) {
      const inputs = [...(quickBar.value?.querySelectorAll<HTMLInputElement>('input') ?? [])]
      const next = inputs[index + 1]
      if (next) {
        next.focus()
        next.select()
      }
    }
    return
  }
  // 非法输入不写草稿，仅标记错误样式；原输入内容保留在框内供教师修改。
  quickInvalid.add(key)
}

function onQuickTotal(event: Event): void {
  quickNotice.value = ''
  setScoreText((event.target as HTMLInputElement).value)
}

function onQuickEnter(): void {
  if (submitDisabled.value) {
    quickNotice.value = disabledReason.value || '当前评分暂不能提交'
    return
  }
  void submitCurrent()
}

function onQuickEscape(event: KeyboardEvent): void {
  ;(event.target as HTMLInputElement).blur()
}

// 深查条目打开/切换完成后把焦点落到快捷给分首格；搜索框、答案面板或别处输入中不抢焦点。
watch(
  () => `${currentDraft.value?.key ?? ''}:${currentDraft.value?.stepScores ? 'steps' : 'total'}`,
  async () => {
    if (!currentDraft.value) return
    await nextTick()
    const active = document.activeElement
    if (active instanceof Element
      && active.closest('.review-deep-workspace__student-search, .review-answer-panel, input, textarea, [contenteditable]')) return
    quickBar.value?.querySelector<HTMLInputElement>('input:not(:disabled)')?.focus()
  },
  { immediate: true },
)

function setScoreText(value: string): void {
  if (!currentDraft.value) return
  draftStore.updateScore(currentDraft.value.key, value)
}

function selectScore(event: Event): void {
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
    ...(draft.stepScores ? { step_scores: draft.stepScores.map((step) => ({
      part_id: step.partId, step_id: step.stepId, score_awarded: Number(step.scoreText),
    })) } : {}),
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
  const generation = ++rubricGeneration
  const cacheKey = reviewRubricCacheKey(sessionId, questionId)
  rubric.value = null
  if (hasReviewRubric(cacheKey)) {
    rubric.value = cachedReviewRubric(cacheKey) ?? null
    rubricState.value = 'ready'
    return
  }

  rubricState.value = 'loading'
  try {
    const loaded = await loadReviewRubric(sessionId, questionId)
    if (generation !== rubricGeneration) return
    rubric.value = loaded
    rubricState.value = 'ready'
  } catch {
    if (generation !== rubricGeneration) return
    rubricState.value = 'error'
  }
}

watch(
  item,
  (next) => {
    stepModeNotice.value = ''
    if (!next) {
      currentDraft.value = null
      return
    }
    currentDraft.value = draftStore.ensureDraft(next)
  },
  { immediate: true },
)

// 可分步复核时默认进入按步骤给分；草稿里已有步骤分则不覆盖教师已有输入。
watch(
  [canReviewSteps, () => currentDraft.value?.key],
  ([ready]) => {
    if (ready && currentDraft.value && !currentDraft.value.stepScores) beginStepReview()
  },
)

watch(
  rubricTargetKey,
  (next) => {
    const current = item.value
    if (!next || !current) {
      rubricGeneration += 1
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
  clearReviewRubrics()
})
</script>

<template>
  <section
    ref="inspectorRoot"
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
          <p class="review-scoring-inspector__heading">
            <span class="review-scoring-inspector__heading-title">
              {{ item.question_id }} · {{ formatScore(item.max_score) }} 分
            </span>
            <StatusBadge
              v-if="currentDraft.dirty"
              tone="warning"
              label="教师草稿未确认"
            />
            <StatusBadge
              v-else
              :tone="statusTone"
              :label="statusLabel"
            />
          </p>
        </header>

        <section class="review-scoring-section" aria-labelledby="review-rubric-title">
          <h3 id="review-rubric-title">评分步骤</h3>
          <p v-if="rubricState === 'loading'" class="review-scoring-section__muted">正在读取当前题评分标准…</p>
          <p v-else-if="rubricState === 'error'" class="review-scoring-section__warning">
            评分标准暂时无法读取，不影响查看和编辑当前分数。
          </p>
          <p v-else-if="rubricState === 'ready' && !rubric" class="review-scoring-section__warning">
            当前题没有可展示的评分标准，请以原始证据和既有满分为准。
          </p>
          <template v-else-if="rubric">
            <ul v-if="rubric.knowledge_labels.length" class="review-scoring-tags" aria-label="知识点">
              <li v-for="label in rubric.knowledge_labels" :key="label">{{ label }}</li>
            </ul>
            <p v-if="currentDraft.stepScores && stepModeNotice" class="review-scoring-section__warning">{{ stepModeNotice }}</p>
            <div
              v-if="rubric.points.length"
              class="review-rubric-points"
              data-testid="review-rubric-points"
            >
              <article
                v-for="point in rubric.points"
                :key="`${point.part_id}:${point.step_id}`"
                class="review-rubric-point"
                :class="{ 'review-rubric-point--active': activeStepKey === `${point.part_id}:${point.step_id}` }"
                :data-step-key="`${point.part_id}:${point.step_id}`"
                @click="focusQuickInput(point.part_id, point.step_id, $event)"
              >
                <header class="review-rubric-point__header">
                  <span
                    class="review-rubric-point__goal"
                    :title="point.core_goal || point.part_label"
                  >
                    <QuestionHtmlBlock :text="point.core_goal || point.part_label" inline typeset-text />
                  </span>
                  <span
                    v-if="currentDraft.stepScores && stepDraft(point.part_id, point.step_id)"
                    class="review-rubric-point__state"
                    :data-tone="stepState(point.part_id, point.step_id, point.score).tone"
                  >
                    {{ stepState(point.part_id, point.step_id, point.score).text }}
                  </span>
                  <span v-else class="review-rubric-point__max">{{ formatScore(point.score) }} 分</span>
                </header>
                <div
                  v-if="stepAiVerdict(point) || hasStepEvidence(point)"
                  class="review-rubric-point__ai-line"
                >
                  <span v-if="stepAiVerdict(point)" class="review-rubric-point__ai">
                    {{ stepAiVerdict(point) }}
                  </span>
                  <span
                    v-if="stepAiVerdict(point) && hasStepEvidence(point)"
                    class="review-rubric-point__ai-separator"
                    aria-hidden="true"
                  >·</span>
                  <details
                    v-if="hasStepEvidence(point)"
                    class="review-rubric-point__evidence"
                  >
                    <summary>AI 依据</summary>
                    <div v-if="stepAssessmentFor(point)?.reason" class="review-rubric-point__evidence-block">
                      <QuestionHtmlBlock :text="stepAssessmentFor(point)!.reason" typeset-text />
                    </div>
                    <div v-if="stepAssessmentFor(point)?.studentEvidence" class="review-rubric-point__evidence-block">
                      <span class="review-rubric-point__evidence-label">依据</span>
                      <QuestionHtmlBlock :text="stepAssessmentFor(point)!.studentEvidence" typeset-text />
                    </div>
                    <div
                      v-if="stepAssessmentFor(point)?.missingOrError"
                      class="review-rubric-point__evidence-block review-rubric-point__evidence-block--warning"
                    >
                      <span class="review-rubric-point__evidence-label">缺漏</span>
                      <QuestionHtmlBlock :text="stepAssessmentFor(point)!.missingOrError" typeset-text />
                    </div>
                  </details>
                </div>
              </article>
            </div>
            <p v-else class="review-scoring-section__muted">评分配置只提供了本题满分，暂无更细步骤。</p>
          </template>
        </section>

        <details
          v-if="hasAiAssessment"
          class="review-scoring-section review-scoring-section--ai review-ai-details"
        >
          <summary class="review-ai-details__line">
            AI {{ formatScore(aiScore) }}/{{ formatScore(item.max_score) }}
            · 置信度 {{ formatConfidence(item.confidence_score) }} · AI 依据
          </summary>
          <div class="review-ai-details__body">
            <ul v-if="evidenceSteps.length" class="review-evidence-list">
              <li v-for="entry in evidenceSteps" :key="entry">
                已有证据：<QuestionHtmlBlock :text="entry" typeset-text />
              </li>
            </ul>
            <ul v-if="missingSteps.length" class="review-evidence-list review-evidence-list--warning">
              <li v-for="entry in missingSteps" :key="entry">
                缺失步骤：<QuestionHtmlBlock :text="entry" typeset-text />
              </li>
            </ul>
            <ul v-if="!canReviewSteps && stepAssessments.length" class="review-step-list">
              <li v-for="step in stepAssessments" :key="`${step.partId}:${step.stepId}`" class="review-step-item">
                <strong>{{ step.partId }} {{ step.stepId }}</strong>
                <span v-if="step.score !== null"> · {{ formatScore(step.score) }} 分</span>
                <span v-if="step.achievement"> · {{ achievementLabel(step.achievement) }}</span>
                <QuestionHtmlBlock v-if="step.reason" :text="step.reason" typeset-text />
                <QuestionHtmlBlock
                  v-if="step.studentEvidence"
                  class="review-step-item__evidence"
                  :text="step.studentEvidence"
                  typeset-text
                />
                <QuestionHtmlBlock
                  v-if="step.missingOrError"
                  class="review-step-item__evidence review-step-item__evidence--warning"
                  :text="step.missingOrError"
                  typeset-text
                />
              </li>
            </ul>
            <ul v-if="candidates.length" class="review-candidate-list">
              <li v-for="(candidate, index) in candidates" :key="index">
                <strong v-if="candidate.score !== null">{{ formatScore(candidate.score) }} 分</strong>
                <span v-if="candidate.confidence !== null"> · {{ formatConfidence(candidate.confidence) }}</span>
                <QuestionHtmlBlock v-if="candidate.reason" :text="candidate.reason" typeset-text />
              </li>
            </ul>
            <p
              v-if="!evidenceSteps.length && !missingSteps.length && !candidates.length && (canReviewSteps || !stepAssessments.length)"
              class="review-scoring-section__muted"
            >暂无更多依据</p>
          </div>
        </details>

        <div v-if="riskText" class="review-scoring-section review-scoring-inspector__risk">
          <QuestionHtmlBlock :text="riskText" typeset-text />
        </div>

      </div>

      <footer class="review-scoring-inspector__footer" data-testid="scoring-footer">
        <div ref="quickBar" class="review-quick-score" data-testid="quick-score">
          <div class="review-quick-score__row">
            <template v-if="currentDraft.stepScores">
              <span
                v-for="(step, index) in currentDraft.stepScores"
                :key="stepKey(step)"
                class="review-quick-score__step"
              >
                <input
                  class="review-quick-score__input"
                  :class="{ 'review-quick-score__input--invalid': quickInvalid.has(stepKey(step)) }"
                  :value="step.scoreText"
                  type="text"
                  inputmode="numeric"
                  :maxlength="quickStepMaxLength(step.maxScore)"
                  :aria-label="`第${index + 1}步 给分，满分${formatScore(step.maxScore)}`"
                  :aria-invalid="quickInvalid.has(stepKey(step)) ? 'true' : 'false'"
                  :title="quickInvalid.has(stepKey(step))
                    ? `请输入 0-${formatScore(step.maxScore)} 的整数`
                    : `第${index + 1}步给分，满分${formatScore(step.maxScore)}`"
                  :disabled="submitting"
                  @input="onQuickStep(step, index, $event)"
                  @keydown.enter.prevent="onQuickEnter"
                  @keydown.esc="onQuickEscape"
                  @focus="selectScore($event); onQuickStepFocus(step)"
                  @blur="onQuickStepBlur"
                  @click="selectScore"
                >
                <small>{{ step.stepId }} / {{ formatScore(step.maxScore) }}</small>
              </span>
            </template>
            <template v-else>
              <input
                id="teacher-score"
                data-testid="teacher-score"
                class="review-quick-score__input"
                :value="currentDraft.scoreText"
                type="text"
                inputmode="decimal"
                :maxlength="quickStepMaxLength(item.max_score) + 1"
                aria-label="教师最终分"
                :aria-invalid="issue ? 'true' : 'false'"
                aria-describedby="teacher-score-error"
                :disabled="submitting"
                @input="onQuickTotal"
                @keydown.enter.prevent="onQuickEnter"
                @keydown.esc="onQuickEscape"
                @focus="selectScore"
                @click="selectScore"
              >
              <span class="review-quick-score__unit">/ {{ formatScore(item.max_score) }} 分</span>
            </template>
            <span class="review-quick-score__total">
              {{ currentDraft.stepScores ? '合计 ' : '' }}{{ currentDraft.scoreText || '—' }} / {{ formatScore(item.max_score) }}
            </span>
          </div>
          <div class="review-quick-score__meta">
            <span class="review-quick-score__hint">数字给分 · Tab 下一步 · Enter 提交 · 方向键切换</span>
            <span v-if="quickNotice" class="review-quick-score__notice" role="status">{{ quickNotice }}</span>
            <span v-if="draftStore.dirtyCount > 0" class="review-quick-score__drafts">
              {{ draftStore.dirtyCount }} 条草稿未确认
            </span>
          </div>
        </div>
        <p v-if="issue" id="teacher-score-error" class="review-field-error">{{ issue }}</p>
        <button
          type="button"
          class="review-scoring-inspector__confirm"
          data-testid="confirm-single"
          :disabled="submitDisabled"
          :title="disabledReason"
          @click="submitCurrent"
        >
          {{ submitting ? '正在确认…' : stayAfterConfirm ? '确认此份' : '确认此份并返回' }}
        </button>
      </footer>
    </template>
  </section>
</template>
